"""Vertex Veo 3.1 image-to-video (ported from the proven make_clip.sh).

Start frame + optional end frame (keyframe interpolation). Same auth/REST as
image (shared vertex.py). Synchronous: submit → poll → decode inline bytes.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from mediagen import config
from mediagen.vertex import VertexError, encode_image_b64, gcloud_token, model_base, post


class VeoError(VertexError):
    pass


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
    """Generate a Veo clip from a start frame (+ optional end frame).

    Returns the output path. dry_run writes the request JSON (frames summarized)
    next to the output and spends no credits.
    """
    out = Path(out)
    model = model or config.VEO_MODEL
    base = model_base(model)

    start_b64, mime = encode_image_b64(start, max_edge=1280, fmt="JPEG")
    instance: dict = {"prompt": prompt, "image": {"bytesBase64Encoded": start_b64, "mimeType": mime}}
    if end:
        end_b64, emime = encode_image_b64(end, max_edge=1280, fmt="JPEG")
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
        dbg.write_text(json.dumps({"url": f"{base}:predictLongRunning", **preview}, indent=2))
        return dbg

    token = gcloud_token()
    submit = post(f"{base}:predictLongRunning", body, token)
    op = submit.get("name")
    if not op:
        raise VeoError(f"submit failed: {json.dumps(submit)[:500]}")

    for _ in range(config.POLL_MAX_TRIES):
        time.sleep(config.POLL_INTERVAL)
        poll = post(f"{base}:fetchPredictOperation", {"operationName": op}, token)
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
