"""Image generation — Vertex AI (Gemini / Imagen), fal.ai (FLUX long tail), ModelArk (Seedream).

Vertex paths (default, no API key needed):
  - Gemini image ("nano-banana") via :generateContent — supports --ref.
  - Imagen via :predict — text-to-image only (no --ref).

fal path (opt-in, requires FAL_KEY env var):
  - FLUX schnell / dev — text-to-image; --ref sent as a data-URI.
  - Routed when model's backend == "fal".

ModelArk path (opt-in, requires ARK_API_KEY env var):
  - Seedream 4.0 — native multi-reference image-to-image (--ref → `image` field,
    up to 14 refs); schema verified against BytePlus docs (2026-06-22). $0.03/img,
    500 IPM. Needs model activation in the BytePlus console (region ap-southeast).
  - Routed when model's backend == "modelark".

Same Vertex auth as video: gcloud token + REST.  No provider SDKs.
"""

from __future__ import annotations

import base64
from pathlib import Path

from nazca.backends import get_backend
from nazca.vertex import VertexError, encode_image_b64


class ImageError(VertexError):
    pass


# shorthand -> (model id, location/fal-id, api, backend)
#   api:     "gemini" | "imagen" | "fal"
#   backend: "vertex" | "fal"
# Vertex models verified live, 2026-06-06.
# fal model IDs are plausible but UNVERIFIED against a live key — check
# https://fal.ai/models before spending. (dry-run only in this PR)
MODELS: dict[str, tuple[str, str, str, str]] = {
    # --- Vertex: Gemini image (supports --ref) ---
    "nano-banana":     ("gemini-2.5-flash-image",  "us-central1", "gemini", "vertex"),  # fast default; ref/edit
    "nano-banana-2":   ("gemini-3.1-flash-image",  "global",      "gemini", "vertex"),  # Nano Banana 2 (Gemini 3.1 Flash Image)
    "nano-banana-pro": ("gemini-3-pro-image",      "global",      "gemini", "vertex"),  # premium: legible text + up to 14 refs
    # --- Vertex: Imagen (text-to-image only) ---
    "imagen-4-fast":   ("imagen-4.0-fast-generate-001", "us-central1", "imagen", "vertex"),  # fast t2i
    "imagen-4":        ("imagen-4.0-generate-001",      "us-central1", "imagen", "vertex"),  # high-fidelity t2i
    "imagen-3":        ("imagen-3.0-generate-002",      "us-central1", "imagen", "vertex"),
    # --- fal.ai: FLUX long tail (verify ids against fal docs before spend) ---
    "flux-schnell":    ("fal-ai/flux/schnell", "", "fal", "fal"),  # ~$0.003/MP; fastest FLUX  # verify id
    "flux-2-dev":      ("fal-ai/flux/dev",     "", "fal", "fal"),  # FLUX 2 dev; higher quality  # verify id
    # --- ByteDance ModelArk: Seedream (id from BytePlus docs; requires model
    #     activation in the BytePlus console, region ap-southeast, before it works) ---
    # $0.03/img, 500 IPM, native multi-ref image-to-image (up to 14 refs). See
    # _modelark dispatch below + docs/throughput-and-rate-limits.md.
    "seedream":        ("seedream-4-0-250828", "", "modelark", "modelark"),  # $0.03/img
}
DEFAULT_MODEL = "nano-banana"

# tier tags: each shorthand → "cheap" | "premium"
# Vertex-direct models are the tier defaults (direct-first rule).
# fal long-tail models are tagged too but are never auto-selected as tier defaults.
MODEL_TIERS: dict[str, str] = {
    "nano-banana":     "cheap",
    "nano-banana-2":   "cheap",
    "nano-banana-pro": "premium",
    "imagen-4-fast":   "cheap",
    "imagen-4":        "premium",
    "imagen-3":        "cheap",
    "flux-schnell":    "cheap",
    "flux-2-dev":      "premium",
    "seedream":        "cheap",   # ModelArk $0.03/img — probe brand fidelity vs Gemini before bulk
}

# tier → default Vertex-direct model (never auto-route to fal)
_TIER_DEFAULTS: dict[str, str] = {
    "cheap":   "nano-banana",
    "premium": "nano-banana-pro",
}


def select_model(tier: str | None) -> str | None:
    """Return the default model shorthand for *tier*, or None if tier is None."""
    if tier is None:
        return None
    return _TIER_DEFAULTS.get(tier)


def _resolve(model: str | None) -> tuple[str, str, str, str]:
    model = model or DEFAULT_MODEL

    # 1. backend:rawid prefix passthrough — route by prefix without touching MODELS
    if ":" in model:
        prefix, raw_id = model.split(":", 1)
        prefix = prefix.lower()
        if prefix in ("vertex", "veo"):
            return (raw_id, "us-central1", "gemini", "vertex")
        if prefix == "fal":
            return (raw_id, "", "fal", "fal")
        if prefix in ("ark", "modelark"):
            return (raw_id, "", "modelark", "modelark")

    # 2. user override file (~/.config/nazca/models.json)
    from nazca.registry import image_override

    ov = image_override(model)
    if ov is not None:
        ov_id = ov.get("id", model)
        ov_region = ov.get("region", "us-central1")
        ov_api = ov.get("api", "gemini")
        ov_backend = ov.get("backend", "vertex")
        return (ov_id, ov_region, ov_api, ov_backend)

    # 3. built-in MODELS dict (unchanged)
    if model in MODELS:
        return MODELS[model]

    # 4. fallback: raw vertex id → assume Gemini family, default region, vertex backend
    return (model, "us-central1", "gemini", "vertex")


# ------------------------------------------------------------------ fal path
# fal expects image_size as a named string ("portrait_16_9" etc.) or
# {width, height}.  We map our aspect/size flags to the named-string form.
_FAL_ASPECT_MAP: dict[str, str] = {
    "9:16":  "portrait_16_9",
    "16:9":  "landscape_16_9",
    "1:1":   "square",
    "4:3":   "landscape_4_3",
    "3:4":   "portrait_4_3",
}


def _fal_image_body(
    prompt: str,
    refs: list[str],
    aspect_ratio: str | None,
    backend,  # FalBackend — avoid circular import by keeping type loose
) -> dict:
    body: dict = {"prompt": prompt}
    if aspect_ratio:
        image_size = _FAL_ASPECT_MAP.get(aspect_ratio)
        if image_size:
            body["image_size"] = image_size
        # unknown aspect → omit (fal will use its default)
    if refs:
        # fal FLUX models accept a single reference image as a data-URI
        body["image_url"] = backend.encode_image_data_uri(refs[0], max_edge=2048)
        if len(refs) > 1:
            # fal FLUX does not support multi-ref; silently use only the first
            # (caller should have already warned if this matters)
            pass
    return body


# ------------------------------------------------------------------ ModelArk / Seedream path
# Verified against BytePlus ModelArk image-generation API (docs 2026-06-22,
# docs.byteplus.com/en/docs/ModelArk/1541523). Seedream 4.0 takes refs in the
# `image` field (string for one, array for many), single-image output via
# `sequential_image_generation: "disabled"`, and sizing via `size` — either a
# named resolution ("1K"/"2K"/"4K", Method 1) or "<w>x<h>" pixels (Method 2).
_SEEDREAM_MAX_REFS = 14  # API ceiling for seedream-4-0 multi-reference input

# Named-resolution square edge (px); the doc's 1:1 dimensions per resolution.
_SEEDREAM_EDGE: dict[str, int] = {"1K": 1024, "2K": 2048, "4K": 4096}
# Documented valid total-pixel range for seedream-4-0 Method 2 (w*h).
_SEEDREAM_MIN_PX = 1280 * 720  # 921,600
_SEEDREAM_MAX_PX = 4096 * 4096  # 16,777,216


def _seedream_size(size: str | None, aspect_ratio: str | None) -> str | None:
    """Map nazca --size (+ --aspect) to ModelArk's `size` field.

    Seedream has no aspect field — aspect is expressed by giving explicit pixel
    dimensions. With a named --size and an explicit W:H aspect we compute a
    "<w>x<h>" that holds the aspect at roughly the resolution's pixel budget
    (rounded to /16, clamped to the valid range). Without a usable aspect we pass
    the named resolution and let the model pick dimensions (e.g. follow the ref).
    """
    if not size:
        return None
    edge = _SEEDREAM_EDGE.get(size.upper()) if isinstance(size, str) else None
    if edge is None or not aspect_ratio or ":" not in aspect_ratio:
        return size  # named resolution, or a caller-supplied raw "WxH"
    try:
        aw, ah = (float(x) for x in aspect_ratio.split(":", 1))
        if aw <= 0 or ah <= 0:
            return size
    except ValueError:
        return size
    budget = edge * edge
    h = (budget * ah / aw) ** 0.5
    w = h * aw / ah
    w = max(16, round(w / 16) * 16)
    h = max(16, round(h / 16) * 16)
    if not (_SEEDREAM_MIN_PX <= w * h <= _SEEDREAM_MAX_PX):
        return size  # fall back to the named resolution rather than an invalid dim
    return f"{w}x{h}"


def _seedream_body(prompt: str, refs: list[str], aspect_ratio: str | None, size: str | None, backend) -> dict:
    """Build the ModelArk Seedream request body (sends refs as the `image` field)."""
    body: dict = {
        "model": None,  # filled by caller (resolved model id)
        "prompt": prompt,
        "sequential_image_generation": "disabled",  # one image out (not a batch)
        "response_format": "url",
        "watermark": False,  # no "AI generated" stamp on brand assets
    }
    sd_size = _seedream_size(size, aspect_ratio)
    if sd_size:
        body["size"] = sd_size
    if refs:
        encoded = [backend.encode_image_data_uri(r, max_edge=2048) for r in refs[:_SEEDREAM_MAX_REFS]]
        # one ref → string; many → array (Seedream accepts both)
        body["image"] = encoded[0] if len(encoded) == 1 else encoded
    return body


def _summarize_data_uris(value):
    """Replace base64 data-URIs with a short tag so dry-run plans stay readable."""
    def _one(v):
        if isinstance(v, str) and v.startswith("data:"):
            b64 = v.split(",", 1)[1] if "," in v else ""
            return f"<data-uri {len(b64)} b64>"
        return v

    return [_one(v) for v in value] if isinstance(value, list) else _one(value)


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
    """Generate (or restyle, when ref is given) one image.

    Vertex/Gemini: supports --ref (one or many; gemini-3-pro-image up to 14)
      and `size` 1K/2K/4K (gemini-3 only).
    Vertex/Imagen: text-to-image only, rejects --ref.
    fal/FLUX: text-to-image; --ref sends the first image as a data-URI.

    Returns the output path; dry_run returns the plan dict (no API call, no key needed).
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

    # ---- fal dispatch ------------------------------------------------
    if backend_name == "fal":
        url = backend.build_url(model_id)
        body = _fal_image_body(prompt, refs, aspect_ratio, backend)

        if dry_run:
            # Summarize any data-URI ref so the plan stays readable
            plan_body = dict(body)
            if "image_url" in plan_body and plan_body["image_url"].startswith("data:"):
                data_part = plan_body["image_url"].split(",", 1)[1] if "," in plan_body["image_url"] else ""
                plan_body["image_url"] = f"<data-uri {len(data_part)} b64>"
            return {
                "url": url,
                "model": model_id,
                "backend": backend_name,
                "api": api,
                "refs": len(refs),
                "body": plan_body,
            }

        key = backend.auth_token()
        raw = backend.submit_and_download(url, body, key, media_type="image")
        out.write_bytes(raw)
        return out

    # ---- ModelArk / Seedream dispatch --------------------------------
    # Native multi-reference image-to-image: refs go in the `image` field (the
    # body is built once and reused for dry-run and real send, so the planned
    # JSON matches what's POSTed). Requires model activation in the BytePlus
    # console (region ap-southeast) + balance; auth failure → see backend error.
    if backend_name == "modelark":
        body = _seedream_body(prompt, refs, aspect_ratio, size, backend)
        body["model"] = model_id

        if dry_run:
            plan_body = dict(body)
            if "image" in plan_body:
                plan_body["image"] = _summarize_data_uris(plan_body["image"])
            return {
                "url": backend.image_endpoint(),
                "model": model_id,
                "backend": backend_name,
                "api": api,
                "refs": len(refs),
                "body": plan_body,
            }

        raw = backend.generate_image(model_id, body)
        out.write_bytes(raw)
        return out

    # ---- Vertex dispatch (unchanged) ---------------------------------
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
