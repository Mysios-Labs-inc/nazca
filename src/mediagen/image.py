"""Image generation — fal (default) or Gemini. text-to-image + image-to-image.

--ref turns it into a restyle (image-to-image): the engine edits/restyles the
reference instead of inventing from scratch. This is the workflow that matters
for product/food content (keep the real dish, restyle the look).
"""

from __future__ import annotations

import base64
import urllib.request
from pathlib import Path

from mediagen import config


class ImageError(RuntimeError):
    pass


# shorthand -> (text_endpoint, edit_endpoint)  [edit used when --ref is given]
FAL_MODELS: dict[str, tuple[str, str | None]] = {
    "nano-banana": ("fal-ai/nano-banana-2", "fal-ai/nano-banana-2/edit"),
    "nano-banana-pro": ("fal-ai/nano-banana-pro", "fal-ai/nano-banana-pro/edit"),
    "seedream": ("fal-ai/bytedance/seedream/v4/text-to-image", "fal-ai/bytedance/seedream/v4/edit"),
    "flux": ("fal-ai/flux/dev", None),
}
DEFAULT_FAL_MODEL = "nano-banana"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-image"


def _download(url: str, out: Path) -> Path:
    with urllib.request.urlopen(url) as r:  # noqa: S310
        out.write_bytes(r.read())
    return out


# --------------------------------------------------------------------------- fal
def _plan_fal(prompt: str, model: str, ref: str | None, aspect_ratio: str | None) -> dict:
    text_ep, edit_ep = FAL_MODELS.get(model, (model, None))
    if ref:
        if not edit_ep:
            raise ImageError(f"model '{model}' has no image-to-image (edit) endpoint")
        endpoint = edit_ep
        args: dict = {"prompt": prompt, "image_urls": ["<uploaded ref url>"]}
    else:
        endpoint = text_ep
        args = {"prompt": prompt}
    if aspect_ratio:
        args["aspect_ratio"] = aspect_ratio
    args["num_images"] = 1
    return {"provider": "fal", "endpoint": endpoint, "arguments": args}


def _run_fal(out: Path, prompt: str, model: str, ref: str | None, aspect_ratio: str | None) -> Path:
    if not config.FAL_KEY:
        raise ImageError("FAL_KEY not set")
    try:
        import fal_client
    except ImportError as e:
        raise ImageError("fal-client not installed — `pip install mediagen[fal]`") from e

    plan = _plan_fal(prompt, model, ref, aspect_ratio)
    args = dict(plan["arguments"])
    if ref:
        args["image_urls"] = [fal_client.upload_file(str(ref))]
    result = fal_client.subscribe(plan["endpoint"], arguments=args)
    images = result.get("images") or []
    if not images:
        raise ImageError(f"no image in fal response: {str(result)[:300]}")
    return _download(images[0]["url"], out)


# ------------------------------------------------------------------------ gemini
def _run_gemini(out: Path, prompt: str, model: str, ref: str | None) -> Path:
    if not config.GEMINI_API_KEY:
        raise ImageError("GEMINI_API_KEY / GOOGLE_API_KEY not set")
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise ImageError("google-genai not installed — `pip install mediagen[gemini]`") from e

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    contents: list = [prompt]
    if ref:
        contents.append(types.Part.from_bytes(data=Path(ref).read_bytes(), mime_type="image/png"))
    resp = client.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    for part in resp.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            data = part.inline_data.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            out.write_bytes(data)
            return out
    raise ImageError("no image part in Gemini response")


# ----------------------------------------------------------------------- public
def generate_image(
    out: str | Path,
    prompt: str,
    *,
    ref: str | Path | None = None,
    model: str | None = None,
    provider: str | None = None,
    aspect_ratio: str | None = "9:16",
    dry_run: bool = False,
) -> Path | dict:
    """Generate (or restyle, when ref is given) one image. Returns the path."""
    out = Path(out)
    provider = provider or ("gemini" if (model or "").startswith("gemini") else "fal")
    ref = str(ref) if ref else None

    if dry_run:
        if provider == "fal":
            return _plan_fal(prompt, model or DEFAULT_FAL_MODEL, ref, aspect_ratio)
        return {"provider": "gemini", "model": model or DEFAULT_GEMINI_MODEL,
                "ref": ref, "prompt": prompt}

    if provider == "fal":
        return _run_fal(out, prompt, model or DEFAULT_FAL_MODEL, ref, aspect_ratio)
    if provider == "gemini":
        return _run_gemini(out, prompt, model or DEFAULT_GEMINI_MODEL, ref)
    raise ImageError(f"unknown provider: {provider}")
