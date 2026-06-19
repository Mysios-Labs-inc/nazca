"""Image generation on Vertex AI. Two model families, one CLI:

- Gemini image ("nano-banana") via :generateContent — supports --ref (image-to-image restyle).
- Imagen via :predict — high-fidelity text-to-image only (no --ref).

Same auth as video: gcloud token + Vertex REST. No API keys, no SDKs.
Model availability is region-specific (see MODELS); verified on florece-492623.
"""

from __future__ import annotations

import base64
from pathlib import Path

from mediagen.backends import get_backend
from mediagen.vertex import VertexError, encode_image_b64


class ImageError(VertexError):
    pass


# shorthand -> (model id, location, api, backend)
#   api: "gemini" (generateContent, supports --ref) | "imagen" (predict, text-only)
#   backend: provider plumbing (only "vertex" today)
# Verified working on florece-492623, 2026-06-06.
MODELS: dict[str, tuple[str, str, str, str]] = {
    "nano-banana":     ("gemini-2.5-flash-image",  "us-central1", "gemini", "vertex"),  # fast default; ref/edit
    "nano-banana-3":   ("gemini-3.1-flash-image",  "global",      "gemini", "vertex"),  # newer flash image (GA)
    "nano-banana-pro": ("gemini-3-pro-image",      "global",      "gemini", "vertex"),  # premium: legible text + up to 14 refs
    "imagen-4-fast":   ("imagen-4.0-fast-generate-001", "us-central1", "imagen", "vertex"),  # fast t2i
    "imagen-4":        ("imagen-4.0-generate-001",      "us-central1", "imagen", "vertex"),  # high-fidelity t2i
    "imagen-3":        ("imagen-3.0-generate-002",      "us-central1", "imagen", "vertex"),
}
DEFAULT_MODEL = "nano-banana"


def _resolve(model: str | None) -> tuple[str, str, str, str]:
    model = model or DEFAULT_MODEL
    if model in MODELS:
        return MODELS[model]
    # raw vertex id → assume Gemini family, default region, vertex backend
    return (model, "us-central1", "gemini", "vertex")


# ------------------------------------------------------------------ Gemini path
def _gemini_body(prompt: str, refs: list[str], aspect_ratio: str | None, size: str | None) -> dict:
    parts: list[dict] = [{"text": prompt}]
    for r in refs:  # gemini-3-pro-image accepts up to 14 reference images
        b64, mime = encode_image_b64(r, max_edge=2048, fmt="PNG")
        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
    gen_cfg: dict = {"responseModalities": ["IMAGE"]}
    img_cfg: dict = {}
    if aspect_ratio:
        img_cfg["aspectRatio"] = aspect_ratio
    if size:
        # 1K/2K/4K — honored by gemini-3 image models; 2.5-flash-image ignores it (1K)
        img_cfg["imageSize"] = size
    if img_cfg:
        gen_cfg["imageConfig"] = img_cfg
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
    ref: str | Path | list[str | Path] | None = None,
    model: str | None = None,
    aspect_ratio: str | None = "9:16",
    size: str | None = "2K",
    dry_run: bool = False,
) -> Path | dict:
    """Generate (or restyle, when ref is given) one image via Vertex.

    Gemini models support --ref image-to-image (one or many reference images;
    gemini-3-pro-image takes up to 14) and `size` 1K/2K/4K (gemini-3 only; 2.5-
    flash stays 1K). Imagen models are text-to-image only and reject --ref.
    Returns the output path; dry_run returns the plan.
    """
    out = Path(out)
    model_id, location, api, backend_name = _resolve(model)
    backend = get_backend(backend_name)
    if ref is None:
        refs = []
    elif isinstance(ref, (list, tuple)):
        refs = [str(r) for r in ref]
    else:
        refs = [str(ref)]

    if api == "imagen" and refs:
        raise ImageError(f"model '{model}' (imagen) is text-to-image only — drop --ref or use a nano-banana model")

    if api == "imagen":
        url = backend.build_url(model_id, "predict", location)
        body = _imagen_body(prompt, aspect_ratio)
        extract = _imagen_extract
    else:
        url = backend.build_url(model_id, "generateContent", location)
        body = _gemini_body(prompt, refs, aspect_ratio, size)
        extract = _gemini_extract

    if dry_run:
        info: dict = {"url": url, "model": model_id, "location": location, "api": api, "refs": len(refs), "size": size}
        if api == "imagen":
            info["parameters"] = body["parameters"]
        else:
            info["generationConfig"] = body["generationConfig"]
            info["parts"] = [
                ({"inlineData": f"<{len(p['inlineData']['data'])} b64>"} if "inlineData" in p else p)
                for p in body["contents"][0]["parts"]
            ]
        return info

    token = backend.auth_token()
    resp = backend.post(url, body, token)
    out.write_bytes(extract(resp))
    return out
