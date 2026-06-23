"""Video generation — Vertex Veo 3.1, fal.ai (Seedance / Wan long tail), ModelArk (Seedance direct).

Vertex path (default, no API key):
  Start frame + optional end frame (keyframe interpolation).
  Submit predictLongRunning → poll fetchPredictOperation → decode inline bytes.

fal path (opt-in, requires FAL_KEY):
  POST to queue.fal.run/{model} with start image as data-URI → poll → download URL.
  --end is passed if the model supports it; --resolution / --audio may be unsupported
  (fal model schemas vary — check fal docs).

ModelArk path (opt-in, requires ARK_API_KEY):
  POST /contents/generations/tasks → poll → download URL.
  endpoints/IDs UNVERIFIED (dry-run only) — benchmark vs fal before real spend.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from nazca import config
from nazca.backends import get_backend
from nazca.errors import BackendError


class VeoError(BackendError):
    """Raised for video-generation failures that are not provider-specific."""


def video_cost_label(
    model: str | None,
    *,
    duration: int = 8,
    resolution: str = "720p",
    audio: bool = False,
) -> str | None:
    """Cost line for a Veo clip, e.g. "~$1.6". Returns None when we have no pricing
    (fal/ModelArk video, edit ops, raw ids) — same posture as image_cost_label."""
    from nazca.cost import estimate_video_cost

    est = estimate_video_cost(model, duration=duration, resolution=resolution, audio=audio)
    return est.label() if est is not None else None


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

# ModelArk video model shorthands → BytePlus ModelArk model id.
# nazca video is image-to-video, so we use the i2v variants.
# IDs verified against BytePlus ModelArk docs (2026-06-19). NOTE: the endpoint,
# auth and request shape are confirmed working live; calling these requires the
# model to be ACTIVATED for your account in the BytePlus console (region
# ap-southeast), else the API returns 404 InvalidEndpointOrModel.NotFound.
ARK_VIDEO_MODELS: dict[str, str] = {
    "seedance-pro":  "bytedance-seedance-1-0-pro-250528",
    "seedance-lite": "bytedance-seedance-1-0-lite-i2v-250428",
}

# tier tags: each shorthand → "cheap" | "premium"
# Vertex-direct models are the tier defaults (direct-first rule).
# fal and modelark long-tail models are tagged too but never auto-selected as tier defaults.
VIDEO_MODEL_TIERS: dict[str, str] = {
    "veo-3.1-lite":    "cheap",
    "veo-3.1-fast":    "cheap",
    "veo-3.1":         "premium",
    "seedance-2-fast": "cheap",
    "wan-2.6":         "cheap",
    "seedance-pro":    "premium",  # ModelArk: unverified pricing — benchmark vs fal before spend
    "seedance-lite":   "cheap",    # ModelArk: unverified pricing — benchmark vs fal before spend
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


# fal video-EDIT ops (source VIDEO → video). Shorthand == op name. The source
# enters as a URL (video_url), never inlined — see edit_video(). reframe's id and
# input field are verified (research workflow, fal.ai 2026-06-22); v2v/extend are
# deferred pending a live input-field probe.
VIDEO_EDIT_MODELS: dict[str, str] = {
    "reframe": "fal-ai/luma-dream-machine/ray-2/reframe",
    "v2v":     "fal-ai/wan-vace-apps/video-edit",  # video_url field UNVERIFIED — verify before spend
    "extend":  "fal-ai/pixverse/extend",           # video_url field UNVERIFIED — verify before spend
}
VIDEO_EDIT_OPS = tuple(VIDEO_EDIT_MODELS)


def default_video_edit_model(op: str) -> str:
    """Default model shorthand for a video-edit op (the op name is the shorthand)."""
    return op


# Vertex backend name (isolate so future providers stay additive)
VEO_BACKEND = "vertex"


def generate_video(
    out: str | Path,
    start: str | Path | None,
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
    """Generate a video clip — text-to-video, or from a start frame (+ optional end).

    Vertex Veo: t2v (no frames), i2v (start), or keyframe (start + end).
    fal: start frame as data-URI when given; end/resolution/audio support varies.

    `start=None` produces text-to-video; the per-backend image field is simply
    omitted. (The CLI validates that the chosen model supports the inferred op.)

    Returns the output path (or .request.json for Vertex dry-run).
    """
    out = Path(out)
    resolved_model = model or config.VEO_MODEL

    # ---- 1. backend:rawid prefix passthrough --------------------------
    if ":" in resolved_model:
        _prefix, _raw_id = resolved_model.split(":", 1)
        _prefix = _prefix.lower()
        if _prefix in ("vertex", "veo"):
            # treat as raw Vertex Veo id; fall through to Vertex dispatch below
            resolved_model = _raw_id
        elif _prefix == "fal":
            # inject into FAL_VIDEO_MODELS logic via a temporary local mapping
            _fal_id = _raw_id
            _backend = get_backend("fal")
            _url = _backend.build_url(_fal_id)
            _body: dict = {
                "prompt": prompt,
                "duration": int(duration),
                "aspect_ratio": aspect_ratio,
            }
            if start:
                _body["image_url"] = _backend.encode_image_data_uri(start, max_edge=1280)
            if end:
                _body["end_image_url"] = _backend.encode_image_data_uri(end, max_edge=1280)
            if dry_run:
                _plan = dict(_body)
                for _k in ("image_url", "end_image_url"):
                    if _k in _plan and _plan[_k].startswith("data:"):
                        _dp = _plan[_k].split(",", 1)[1] if "," in _plan[_k] else ""
                        _plan[_k] = f"<data-uri {len(_dp)} b64>"
                _dbg = out.with_suffix(".request.json")
                _dbg.write_text(
                    json.dumps({"url": _url, "model": _fal_id, "backend": "fal", **_plan}, indent=2)
                )
                return _dbg
            _fal_key = _backend.auth_token()
            _raw = _backend.submit_and_download(_url, _body, _fal_key, media_type="video")
            out.write_bytes(_raw)
            return out
        elif _prefix in ("ark", "modelark"):
            _ark_id = _raw_id
            _backend = get_backend("modelark")
            _content: list[dict] = [{"type": "text", "text": prompt}]
            if start:
                _start_uri = _backend.encode_image_data_uri(start, max_edge=1280)
                _content.append({"type": "image_url", "image_url": {"url": _start_uri}})
            _body = {
                "model": _ark_id,
                "content": _content,
                "duration": int(duration),
                "aspect_ratio": aspect_ratio,
            }
            if end:
                _body["end_image_url"] = _backend.encode_image_data_uri(end, max_edge=1280)
            if dry_run:
                _dbg = out.with_suffix(".request.json")
                _preview = json.loads(json.dumps(_body))
                for _part in _preview["content"]:
                    _u = _part.get("image_url", {}).get("url", "")
                    if isinstance(_u, str) and _u.startswith("data:"):
                        _part["image_url"]["url"] = f"<data-uri {len(_u.split(',', 1)[-1])} b64>"
                if isinstance(_preview.get("end_image_url"), str) and _preview["end_image_url"].startswith("data:"):
                    _preview["end_image_url"] = f"<data-uri {len(_preview['end_image_url'].split(',', 1)[-1])} b64>"
                _dbg.write_text(
                    json.dumps({"url": _backend.video_endpoint(), "backend": "modelark", **_preview}, indent=2)
                )
                return _dbg
            _raw = _backend.generate_video(_ark_id, _body)
            out.write_bytes(_raw)
            return out

    # ---- 2. user override file (~/.config/nazca/models.json) -----------
    from nazca.registry import video_override

    _ov = video_override(resolved_model)
    if _ov is not None:
        _ov_backend = _ov.get("backend", "vertex")
        _ov_id = _ov.get("id", resolved_model)
        if _ov_backend == "fal":
            # Reroute to fal dispatch using the overridden model id
            _FAL_VIDEO_MODELS_TEMP = {resolved_model: _ov_id}
            _fal_model_id = _ov_id
            _backend = get_backend("fal")
            _url = _backend.build_url(_fal_model_id)
            _body = {
                "prompt": prompt,
                "duration": int(duration),
                "aspect_ratio": aspect_ratio,
            }
            if start:
                _body["image_url"] = _backend.encode_image_data_uri(start, max_edge=1280)
            if end:
                _body["end_image_url"] = _backend.encode_image_data_uri(end, max_edge=1280)
            if dry_run:
                _plan = dict(_body)
                for _k in ("image_url", "end_image_url"):
                    if _k in _plan and _plan[_k].startswith("data:"):
                        _dp = _plan[_k].split(",", 1)[1] if "," in _plan[_k] else ""
                        _plan[_k] = f"<data-uri {len(_dp)} b64>"
                _dbg = out.with_suffix(".request.json")
                _dbg.write_text(
                    json.dumps({"url": _url, "model": _fal_model_id, "backend": "fal", **_plan}, indent=2)
                )
                return _dbg
            _fal_key = _backend.auth_token()
            _raw = _backend.submit_and_download(_url, _body, _fal_key, media_type="video")
            out.write_bytes(_raw)
            return out
        if _ov_backend == "modelark":
            _ark_model_id = _ov_id
            _backend = get_backend("modelark")
            _content = [{"type": "text", "text": prompt}]
            if start:
                _start_uri = _backend.encode_image_data_uri(start, max_edge=1280)
                _content.append({"type": "image_url", "image_url": {"url": _start_uri}})
            _body = {
                "model": _ark_model_id,
                "content": _content,
                "duration": int(duration),
                "aspect_ratio": aspect_ratio,
            }
            if end:
                _body["end_image_url"] = _backend.encode_image_data_uri(end, max_edge=1280)
            if dry_run:
                _dbg = out.with_suffix(".request.json")
                _preview = json.loads(json.dumps(_body))
                for _part in _preview["content"]:
                    _u = _part.get("image_url", {}).get("url", "")
                    if isinstance(_u, str) and _u.startswith("data:"):
                        _part["image_url"]["url"] = f"<data-uri {len(_u.split(',', 1)[-1])} b64>"
                if isinstance(_preview.get("end_image_url"), str) and _preview["end_image_url"].startswith("data:"):
                    _preview["end_image_url"] = f"<data-uri {len(_preview['end_image_url'].split(',', 1)[-1])} b64>"
                _dbg.write_text(
                    json.dumps({"url": _backend.video_endpoint(), "backend": "modelark", **_preview}, indent=2)
                )
                return _dbg
            _raw = _backend.generate_video(_ark_model_id, _body)
            out.write_bytes(_raw)
            return out
        # vertex override: _ov_id is the raw Veo model id
        resolved_model = _ov_id

    # ---- 3. fal dispatch ------------------------------------------------
    if resolved_model in FAL_VIDEO_MODELS:
        fal_model_id = FAL_VIDEO_MODELS[resolved_model]
        backend = get_backend("fal")
        url = backend.build_url(fal_model_id)

        # Build fal video body (image_url only when a start frame is given → t2v when not)
        body: dict = {
            "prompt": prompt,
            "duration": int(duration),
            "aspect_ratio": aspect_ratio,
        }
        if start:
            body["image_url"] = backend.encode_image_data_uri(start, max_edge=1280)
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

    # ---- 4. ModelArk dispatch -------------------------------------------
    # WARNING: endpoint, schema, and model IDs are UNVERIFIED (dry-run only).
    # Benchmark against fal (seedance-2-fast) before real spend.
    if resolved_model in ARK_VIDEO_MODELS:
        ark_model_id = ARK_VIDEO_MODELS[resolved_model]
        backend = get_backend("modelark")

        # Build content array: text prompt + (optional) seed frame as a data URI
        # (a remote API cannot read a local path). Schema is UNVERIFIED.
        content: list[dict] = [{"type": "text", "text": prompt}]  # verify schema
        if start:
            start_uri = backend.encode_image_data_uri(start, max_edge=1280)
            content.append({"type": "image_url", "image_url": {"url": start_uri}})  # verify schema
        body: dict = {
            "model": ark_model_id,
            "content": content,
            "duration": int(duration),
            "aspect_ratio": aspect_ratio,
        }
        if end:
            body["end_image_url"] = backend.encode_image_data_uri(end, max_edge=1280)  # verify field

        if dry_run:
            dbg = out.with_suffix(".request.json")
            # Summarize data URIs so the plan stays readable.
            preview = json.loads(json.dumps(body))
            for part in preview["content"]:
                u = part.get("image_url", {}).get("url", "")
                if isinstance(u, str) and u.startswith("data:"):
                    part["image_url"]["url"] = f"<data-uri {len(u.split(',', 1)[-1])} b64>"
            if isinstance(preview.get("end_image_url"), str) and preview["end_image_url"].startswith("data:"):
                preview["end_image_url"] = f"<data-uri {len(preview['end_image_url'].split(',', 1)[-1])} b64>"
            dbg.write_text(
                json.dumps(
                    {"url": backend.video_endpoint(), "backend": "modelark", **preview}, indent=2
                )
            )
            return dbg

        raw = backend.generate_video(ark_model_id, body)
        out.write_bytes(raw)
        return out

    # ---- 5. Vertex dispatch (unchanged) ---------------------------------
    veo_model = VEO_ALIASES.get(resolved_model, resolved_model)
    backend = get_backend(VEO_BACKEND)

    instance: dict = {"prompt": prompt}
    if start:  # omit `image` for text-to-video
        start_b64, mime = backend.encode_image_b64(start, max_edge=1280, fmt="JPEG")
        instance["image"] = {"bytesBase64Encoded": start_b64, "mimeType": mime}
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


def edit_video(
    out: str | Path,
    source: str,
    *,
    op: str,
    model: str | None = None,
    aspect_ratio: str = "9:16",
    prompt: str | None = None,
    duration: int = 8,
    dry_run: bool = False,
) -> Path:
    """Video-edit ops (source VIDEO → video) via fal.

    The source is passed as a URL (`video_url`), NOT inlined as a base64 data-URI
    — a real clip is MB-scale and fal expects a URL. Local-file → fal-storage
    upload is a planned follow-up; for now SOURCE must be a public http(s) URL.

      reframe → fal-ai/luma-dream-machine/ray-2/reframe  {video_url, aspect_ratio}
      v2v     → fal-ai/wan-vace-apps/video-edit          {video_url, prompt}
      extend  → fal-ai/pixverse/extend                   {video_url, prompt, duration}

    NOTE: the `video_url` field for v2v/extend is fal's convention but UNVERIFIED
    live — dry-run safe; verify with a real call before spending.

    Returns the output path (or .request.json for dry-run).
    """
    out = Path(out)
    src = str(source)
    if not (src.startswith("http://") or src.startswith("https://")):
        raise VeoError(
            f"{op} SOURCE must be a public https:// video URL "
            f"(local-file upload to fal storage is a planned follow-up); got: {src}"
        )

    resolved = model or default_video_edit_model(op)
    fal_id = VIDEO_EDIT_MODELS.get(resolved, resolved)  # shorthand → fal id, or raw passthrough
    backend = get_backend("fal")
    url = backend.build_url(fal_id)

    if op == "reframe":
        body: dict = {"video_url": src, "aspect_ratio": aspect_ratio}
        if prompt:
            body["prompt"] = prompt  # optional: guides inpainting of exposed regions
    elif op == "v2v":
        if not prompt:
            raise VeoError("v2v needs a prompt (the edit instruction)")
        body = {"video_url": src, "prompt": prompt}
    elif op == "extend":
        if not prompt:
            raise VeoError("extend needs a prompt")
        if int(duration) not in (5, 8):
            raise VeoError("extend --duration must be 5 or 8 (seconds added to the clip)")
        body = {"video_url": src, "prompt": prompt, "duration": str(int(duration))}
    else:
        raise VeoError(f"video-edit op '{op}' is not wired yet")

    if dry_run:
        dbg = out.with_suffix(".request.json")
        dbg.write_text(json.dumps({"url": url, "model": fal_id, "backend": "fal", "op": op, **body}, indent=2))
        return dbg

    key = backend.auth_token()
    raw = backend.submit_and_download(url, body, key, media_type="video")
    out.write_bytes(raw)
    return out
