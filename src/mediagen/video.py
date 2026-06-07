"""Vertex Veo 3.1 image-to-video (ported from the proven make_clip.sh).

Start frame + optional end frame (keyframe interpolation). gcloud auth, inline
base64 in/out (no GCS bucket needed). Synchronous: submit → poll → decode.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from PIL import Image

from mediagen import config


class VeoError(RuntimeError):
    pass


def _gcloud_token() -> str:
    try:
        out = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError as e:
        raise VeoError("gcloud not found — install the Google Cloud SDK") from e
    except subprocess.CalledProcessError as e:
        raise VeoError(f"gcloud auth failed: {e.stderr.strip()}") from e
    return out.stdout.strip()


def _encode_frame(path: str | Path, max_edge: int = 1280) -> str:
    """Downscale a frame to keep the request small, return base64 JPEG."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def _post(url: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Google endpoint)
        return json.loads(resp.read().decode())


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

    Returns the output path. With dry_run=True, writes the request JSON next to
    the output and returns without calling the API (no credits spent).
    """
    out = Path(out)
    model = model or config.VEO_MODEL
    base = (
        f"https://{config.VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/"
        f"{config.VERTEX_PROJECT}/locations/{config.VERTEX_LOCATION}/publishers/google/models/{model}"
    )

    instance: dict = {
        "prompt": prompt,
        "image": {"bytesBase64Encoded": _encode_frame(start), "mimeType": "image/jpeg"},
    }
    if end:
        instance["lastFrame"] = {"bytesBase64Encoded": _encode_frame(end), "mimeType": "image/jpeg"}

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
        # don't dump the giant base64 — summarize
        preview = json.loads(json.dumps(body))
        for inst in preview["instances"]:
            for k in ("image", "lastFrame"):
                if k in inst:
                    inst[k]["bytesBase64Encoded"] = f"<{len(instance[k]['bytesBase64Encoded'])} b64 chars>"
        dbg.write_text(json.dumps({"url": f"{base}:predictLongRunning", **preview}, indent=2))
        return dbg

    token = _gcloud_token()
    submit = _post(f"{base}:predictLongRunning", body, token)
    op = submit.get("name")
    if not op:
        raise VeoError(f"submit failed: {json.dumps(submit)[:500]}")

    for _ in range(config.POLL_MAX_TRIES):
        time.sleep(config.POLL_INTERVAL)
        poll = _post(f"{base}:fetchPredictOperation", {"operationName": op}, token)
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
