"""Image generation on Vertex AI. Two model families, one CLI:

- Gemini image ("nano-banana") via :generateContent — supports --ref (image-to-image restyle).
- Imagen via :predict — high-fidelity text-to-image only (no --ref).

Same auth as video: gcloud token + Vertex REST. No API keys, no SDKs.
Model availability is region-specific (see MODELS); verified on florece-492623.
"""

from __future__ import annotations

import base64
from pathlib import Path

from mediagen.vertex import VertexError, encode_image_b64, gcloud_token, model_base, post


class ImageError(VertexError):
    pass


# shorthand -> (vertex model id, location, api)
#   api: "gemini" (generateContent, supports --ref) | "imagen" (predict, text-only)
# Verified working on florece-492623, 2026-06-06.
MODELS: dict[str, tuple[str, str, str]] = {
    "nano-banana":     ("gemini-2.5-flash-image",      "us-central1", "gemini"),  # default; ref/edit
    "nano-banana-pro": ("gemini-3-pro-image-preview",  "global",      "gemini"),  # newer; global-only
    "imagen-4-fast":   ("imagen-4.0-fast-generate-001", "us-central1", "imagen"),  # fast t2i
    "imagen-4":        ("imagen-4.0-generate-001",      "us-central1", "imagen"),  # high-fidelity t2i
    "imagen-3":        ("imagen-3.0-generate-002",      "us-central1", "imagen"),
}
DEFAULT_MODEL = "nano-banana"


def _resolve(model: str | None) -> tuple[str, str, str]:
    model = model or DEFAULT_MODEL
    if model in MODELS:
        return MODELS[model]
    # raw vertex id → assume Gemini family, default region
    return (model, "us-central1", "gemini")


# ------------------------------------------------------------------ Gemini path
def _gemini_body(prompt: str, ref: str | None, aspect_ratio: str | None) -> dict:
    parts: list[dict] = [{"text": prompt}]
    if ref:
        b64, mime = encode_image_b64(ref, max_edge=1536, fmt="PNG")
        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
    gen_cfg: dict = {"responseModalities": ["IMAGE"]}
    if aspect_ratio:
        gen_cfg["imageConfig"] = {"aspectRatio": aspect_ratio}
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}


def _gemini_extract(resp: dict) -> bytes:
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise ImageError(f"no image part in response: {str(resp)[:400]}")


# ------------------------------------------------------------------ Imagen path
def _imagen_body(prompt: str, aspect_ratio: str | None) -> dict:
    params: dict = {"sampleCount": 1}
    if aspect_ratio:
        params["aspectRatio"] = aspect_ratio
    return {"instances": [{"prompt": prompt}], "parameters": params}


def _imagen_extract(resp: dict) -> bytes:
    preds = resp.get("predictions") or []
    if not preds:
        raise ImageError(f"no prediction in imagen response: {str(resp)[:400]}")
    b64 = preds[0].get("bytesBase64Encoded")
    if not b64:
        raise ImageError(f"no image bytes in imagen prediction: {str(preds[0])[:300]}")
    return base64.b64decode(b64)


# ----------------------------------------------------------------------- public
def generate_image(
    out: str | Path,
    prompt: str,
    *,
    ref: str | Path | None = None,
    model: str | None = None,
    aspect_ratio: str | None = "9:16",
    dry_run: bool = False,
) -> Path | dict:
    """Generate (or restyle, when ref is given) one image via Vertex.

    Gemini models support --ref (image-to-image). Imagen models are text-to-image
    only and reject --ref. Returns the output path; dry_run returns the plan.
    """
    out = Path(out)
    model_id, location, api = _resolve(model)
    ref = str(ref) if ref else None

    if api == "imagen" and ref:
        raise ImageError(f"model '{model}' (imagen) is text-to-image only — drop --ref or use a nano-banana model")

    if api == "imagen":
        url = f"{model_base(model_id, location)}:predict"
        body = _imagen_body(prompt, aspect_ratio)
        extract = _imagen_extract
    else:
        url = f"{model_base(model_id, location)}:generateContent"
        body = _gemini_body(prompt, ref, aspect_ratio)
        extract = _gemini_extract

    if dry_run:
        info: dict = {"url": url, "model": model_id, "location": location, "api": api, "ref": bool(ref)}
        if api == "imagen":
            info["parameters"] = body["parameters"]
        else:
            info["generationConfig"] = body["generationConfig"]
            info["parts"] = [
                ({"inlineData": f"<{len(p['inlineData']['data'])} b64>"} if "inlineData" in p else p)
                for p in body["contents"][0]["parts"]
            ]
        return info

    token = gcloud_token()
    resp = post(url, body, token)
    out.write_bytes(extract(resp))
    return out
