"""Image generation on Vertex AI (Gemini image models / "nano-banana").

Same auth as video: gcloud token + Vertex REST. No API keys, no SDKs.
--ref turns it into a restyle (image-to-image): the reference is passed as an
inline image part, so the model edits the real photo instead of inventing one.
"""

from __future__ import annotations

import base64
from pathlib import Path

from mediagen import config
from mediagen.vertex import VertexError, encode_image_b64, gcloud_token, model_base, post


class ImageError(VertexError):
    pass


# shorthand -> Vertex publisher model id
MODELS: dict[str, str] = {
    "nano-banana": "gemini-2.5-flash-image",
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "imagen": "imagen-4.0-generate-001",
}
DEFAULT_MODEL = "nano-banana"


def _resolve(model: str | None) -> str:
    model = model or DEFAULT_MODEL
    return MODELS.get(model, model)


def _build_body(prompt: str, ref: str | None, aspect_ratio: str | None) -> dict:
    parts: list[dict] = [{"text": prompt}]
    if ref:
        b64, mime = encode_image_b64(ref, max_edge=1536, fmt="PNG")
        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
    gen_cfg: dict = {"responseModalities": ["IMAGE"]}
    if aspect_ratio:
        gen_cfg["imageConfig"] = {"aspectRatio": aspect_ratio}
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}


def _extract_image(resp: dict) -> bytes:
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise ImageError(f"no image part in response: {str(resp)[:400]}")


def generate_image(
    out: str | Path,
    prompt: str,
    *,
    ref: str | Path | None = None,
    model: str | None = None,
    aspect_ratio: str | None = "9:16",
    dry_run: bool = False,
) -> Path | dict:
    """Generate (or restyle, when ref is given) one image via Vertex Gemini.

    Returns the output path. dry_run returns the planned request (ref summarized).
    """
    out = Path(out)
    resolved = _resolve(model)
    ref = str(ref) if ref else None
    body = _build_body(prompt, ref, aspect_ratio)

    if dry_run:
        preview = {"url": f"{model_base(resolved)}:generateContent", "model": resolved}
        parts_summary = []
        for p in body["contents"][0]["parts"]:
            if "inlineData" in p:
                parts_summary.append({"inlineData": f"<{len(p['inlineData']['data'])} b64 chars, {p['inlineData']['mimeType']}>"})
            else:
                parts_summary.append(p)
        preview["parts"] = parts_summary
        preview["generationConfig"] = body["generationConfig"]
        return preview

    token = gcloud_token()
    resp = post(f"{model_base(resolved)}:generateContent", body, token)
    out.write_bytes(_extract_image(resp))
    return out
