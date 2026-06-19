"""Video generation — Vertex Veo 3.1 and fal.ai (Seedance / Wan long tail).

Vertex path (default, no API key):
  Start frame + optional end frame (keyframe interpolation).
  Submit predictLongRunning → poll fetchPredictOperation → decode inline bytes.

fal path (opt-in, requires FAL_KEY):
  POST to queue.fal.run/{model} with start image as data-URI → poll → download URL.
  --end is passed if the model supports it; --resolution / --audio may be unsupported
  (fal model schemas vary — check fal docs).
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from mediagen import config
from mediagen.backends import get_backend
from mediagen.vertex import VertexError


class VeoError(VertexError):
    pass


# Shorthand aliases → full Vertex Veo model ids
VEO_ALIASES: dict[str, str] = {
    "veo-3.1-lite": "veo-3.1-lite-generate-001",
    "veo-3.1-fast": "veo-3.1-fast-generate-001",
    "veo-3.1": "veo-3.1-generate-001",
}

# fal video model shorthands → (fal model id, backend)
# IDs are plausible but UNVERIFIED against a live key — check fal.ai/models.
FAL_VIDEO_MODELS: dict[str, str] = {
    "seedance-2-fast": "fal-ai/bytedance/seedance/v2/lite",  # verify id
    "wan-2.6":         "fal-ai/wan/v2.6/text-to-video",     # verify id
}

# tier tags: each shorthand → "cheap" | "premium"
# Vertex-direct models are the tier defaults (direct-first rule).
# fal long-tail models are tagged too but never auto-selected as tier defaults.
VIDEO_MODEL_TIERS: dict[str, str] = {
    "veo-3.1-lite":    "cheap",
    "veo-3.1-fast":    "cheap",
    "veo-3.1":         "premium",
    "seedance-2-fast": "cheap",
    "wan-2.6":         "cheap",
}

# tier → default Vertex-direct model (never auto-route to fal)
_TIER_DEFAULTS: dict[str, str] = {
    "cheap":   "veo-3.1-lite",
    "premium": "veo-3.1",
}


def select_model(tier: str | None) -> str | None:
    """Return the default model shorthand for *tier*, or None if tier is None."""
    if tier is None:
        return None
    return _TIER_DEFAULTS.get(tier)


# Vertex backend name (isolate so future providers stay additive)
VEO_BACKEND = "vertex"


def generate_video(
    out: str | Path,
    start: str | Path,
    prompt: str,
    end: str | Path | None = None,
    *,
    model: str | None = None,
    duration: int = 8,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    generate_audio: bool = False,
    dry_run: bool = False,
) -> Path:
    """Generate a video clip from a start frame (+ optional end frame).

    Vertex Veo: start + optional end frame keyframe interpolation.
    fal: start frame as data-URI; end/resolution/audio support varies by model.

    Returns the output path (or .request.json for Vertex dry-run).
    """
    out = Path(out)
    resolved_model = model or config.VEO_MODEL

    # ---- fal dispatch ------------------------------------------------
    if resolved_model in FAL_VIDEO_MODELS:
        fal_model_id = FAL_VIDEO_MODELS[resolved_model]
        backend = get_backend("fal")
        url = backend.build_url(fal_model_id)

        # Build fal video body
        start_uri = backend.encode_image_data_uri(start, max_edge=1280)
        body: dict = {
            "prompt": prompt,
            "image_url": start_uri,
            "duration": int(duration),
            "aspect_ratio": aspect_ratio,
        }
        if end:
            body["end_image_url"] = backend.encode_image_data_uri(end, max_edge=1280)

        if dry_run:
            plan_body = dict(body)
            for key in ("image_url", "end_image_url"):
                if key in plan_body and plan_body[key].startswith("data:"):
                    data_part = plan_body[key].split(",", 1)[1] if "," in plan_body[key] else ""
                    plan_body[key] = f"<data-uri {len(data_part)} b64>"
            dbg = out.with_suffix(".request.json")
            dbg.write_text(
                json.dumps({"url": url, "model": fal_model_id, "backend": "fal", **plan_body}, indent=2)
            )
            return dbg

        fal_key = backend.auth_token()
        raw = backend.submit_and_download(url, body, fal_key, media_type="video")
        out.write_bytes(raw)
        return out

    # ---- Vertex dispatch (unchanged) ---------------------------------
    veo_model = VEO_ALIASES.get(resolved_model, resolved_model)
    backend = get_backend(VEO_BACKEND)

    start_b64, mime = backend.encode_image_b64(start, max_edge=1280, fmt="JPEG")
    instance: dict = {"prompt": prompt, "image": {"bytesBase64Encoded": start_b64, "mimeType": mime}}
    if end:
        end_b64, emime = backend.encode_image_b64(end, max_edge=1280, fmt="JPEG")
        instance["lastFrame"] = {"bytesBase64Encoded": end_b64, "mimeType": emime}

    body = {
        "instances": [instance],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": aspect_ratio,
            "resolution": resolution,
            "durationSeconds": int(duration),
            "generateAudio": generate_audio,
        },
    }

    if dry_run:
        dbg = out.with_suffix(".request.json")
        preview = json.loads(json.dumps(body))
        for inst in preview["instances"]:
            for k in ("image", "lastFrame"):
                if k in inst:
                    inst[k]["bytesBase64Encoded"] = f"<{len(instance[k]['bytesBase64Encoded'])} b64 chars>"
        dbg.write_text(json.dumps({"url": backend.build_url(veo_model, "predictLongRunning"), **preview}, indent=2))
        return dbg

    token = backend.auth_token()
    submit = backend.post(backend.build_url(veo_model, "predictLongRunning"), body, token)
    op = submit.get("name")
    if not op:
        raise VeoError(f"submit failed: {json.dumps(submit)[:500]}")

    for _ in range(config.POLL_MAX_TRIES):
        time.sleep(config.POLL_INTERVAL)
        poll = backend.post(backend.build_url(veo_model, "fetchPredictOperation"), {"operationName": op}, token)
        if poll.get("done"):
            break
    else:
        raise VeoError("timed out waiting for video generation")

    if poll.get("error"):
        raise VeoError(f"generation error: {poll['error'].get('message')}")
    resp = poll.get("response", {})
    vids = resp.get("videos") or resp.get("generatedSamples") or []
    if not vids:
        raise VeoError(f"no video in response: {json.dumps(resp)[:500]}")
    v = vids[0]
    b64 = v.get("bytesBase64Encoded") or v.get("video", {}).get("bytesBase64Encoded")
    if not b64:
        gcs = v.get("gcsUri")
        if gcs:
            raise VeoError(f"stored at {gcs} (no inline bytes) — fetch with gsutil cp")
        raise VeoError(f"unrecognized video payload: {json.dumps(v)[:300]}")
    out.write_bytes(base64.b64decode(b64))
    return out
