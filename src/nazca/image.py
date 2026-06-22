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
    # --- OpenAI: gpt-image-2 (best-in-class legible text; ad creative). t2i via
    #     /images/generations; --ref (up to 5) routes to /images/edits. ---
    "gpt-image-2":     ("gpt-image-2", "", "openai", "openai"),  # token-billed; cost scales with size×quality
    # --- fal modify ops (source image → image). api="fal-modify" routes the
    #     modify_image() dispatch. IDs verified against fal.ai docs 2026-06-22. ---
    "upscale":         ("fal-ai/clarity-upscaler", "", "fal-modify", "fal"),  # $0.03/MP
    "rmbg":            ("fal-ai/birefnet/v2",       "", "fal-modify", "fal"),  # free compute
    "inpaint":         ("fal-ai/flux-pro/v1/fill",  "", "fal-modify", "fal"),  # $0.05/MP; image+mask+prompt
    "outpaint":        ("fal-ai/flux-2-pro/outpaint", "", "fal-modify", "fal"),  # expand px/side
}
DEFAULT_MODEL = "nano-banana"

# Source-image modify ops and their default models.
MODIFY_OPS = ("upscale", "bg_remove", "inpaint", "outpaint")
_MODIFY_DEFAULT_MODEL = {
    "upscale": "upscale",
    "bg_remove": "rmbg",
    "inpaint": "inpaint",
    "outpaint": "outpaint",
}

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
    "gpt-image-2":     "premium", # legible text + ad creative; per-token billing
    "upscale":         "cheap",   # fal clarity-upscaler
    "rmbg":            "cheap",   # fal birefnet (free compute)
    "inpaint":         "cheap",   # fal flux-pro/v1/fill
    "outpaint":        "cheap",   # fal flux-2-pro/outpaint
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
        if prefix in ("openai", "oai"):
            return (raw_id, "", "openai", "openai")

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


# ------------------------------------------------------------------ OpenAI path
# gpt-image-2 sizes are pixel strings, not aspect ratios. Map our aspect flag to
# the nearest supported size; anything unknown falls back to "auto" (model picks).
_OPENAI_ASPECT_MAP: dict[str, str] = {
    "1:1":  "1024x1024",
    "9:16": "1024x1536",
    "3:4":  "1024x1536",
    "2:3":  "1024x1536",
    "16:9": "1536x1024",
    "4:3":  "1536x1024",
    "3:2":  "1536x1024",
}


def _openai_image_body(
    prompt: str, model_id: str, aspect_ratio: str | None, quality: str | None = None
) -> dict:
    """Build the /images/{generations,edits} body (shared by both ops).

    `quality` (low|medium|high|auto) is the main cost/speed lever — output image
    tokens, which dominate the bill, scale ~4× between medium and high. Defaults
    to "high" (best text fidelity) when unset.
    """
    body: dict = {
        "model": model_id,
        "prompt": prompt,
        "n": 1,
        "quality": quality or "high",
    }
    body["size"] = _OPENAI_ASPECT_MAP.get(aspect_ratio or "", "auto")
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
    quality: str | None = None,
    dry_run: bool = False,
) -> Path | dict:
    """Generate (or restyle, when ref is given) one image.

    Vertex/Gemini: supports --ref (one or many; gemini-3-pro-image up to 14)
      and `size` 1K/2K/4K (gemini-3 only).
    Vertex/Imagen: text-to-image only, rejects --ref.
    fal/FLUX: text-to-image; --ref sends the first image as a data-URI.
    OpenAI/gpt-image-2: t2i + edits; `quality` (low|medium|high|auto) sets the
      cost/speed lever (ignored by other backends).

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

    # ---- OpenAI dispatch (gpt-image-2) -------------------------------
    if backend_name == "openai":
        body = _openai_image_body(prompt, model_id, aspect_ratio, quality)

        # With refs → /images/edits (multipart). Without → /images/generations.
        if refs:
            from nazca.backends.openai import MAX_EDIT_IMAGES

            if len(refs) > MAX_EDIT_IMAGES:
                raise ImageError(
                    f"gpt-image-2 accepts at most {MAX_EDIT_IMAGES} reference images, got {len(refs)}"
                )
            if dry_run:
                return {
                    "url": backend.edit_endpoint(),
                    "model": model_id,
                    "backend": backend_name,
                    "api": api,
                    "refs": len(refs),
                    "body": body,  # sent as multipart form fields alongside image[] parts
                }
            raw = backend.edit_image(body, refs)
            out.write_bytes(raw)
            return out

        if dry_run:
            return {
                "url": backend.image_endpoint(),
                "model": model_id,
                "backend": backend_name,
                "api": api,
                "refs": 0,
                "body": body,
            }

        raw = backend.generate_image(body)
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


def default_modify_model(op: str) -> str:
    """Default model shorthand for a source-image modify op."""
    return _MODIFY_DEFAULT_MODEL[op]


def _summarize_data_uri(value: str) -> str:
    """Shorten a base64 data-URI for readable dry-run plans (leave URLs as-is)."""
    if isinstance(value, str) and value.startswith("data:"):
        b64 = value.split(",", 1)[1] if "," in value else ""
        return f"<data-uri {len(b64)} b64>"
    return value


def modify_image(
    out: str | Path,
    source: str | Path,
    *,
    op: str,
    model: str | None = None,
    prompt: str | None = None,
    mask: str | Path | None = None,
    upscale_factor: int = 2,
    expand: int = 256,
    dry_run: bool = False,
) -> Path | dict:
    """Apply a source-image modify op via fal. Verified fal schemas (2026-06-22):

    upscale   → clarity-upscaler   {image_url, upscale_factor}
    bg_remove → birefnet/v2        {image_url, output_format:"png"}  (transparent PNG)
    inpaint   → flux-pro/v1/fill   {image_url, mask_url, prompt}     (mask: white=edit)
    outpaint  → flux-2-pro/outpaint {image_url, expand_top/bottom/left/right}

    The body is built once and reused for dry-run and real send (only base64
    data-URIs are summarized), so the planned JSON matches what's POSTed.
    Returns the output path; dry_run returns the plan dict.
    """
    out = Path(out)
    resolved = model or _MODIFY_DEFAULT_MODEL[op]
    model_id, _location, _api, backend_name = _resolve(resolved)
    if backend_name != "fal":
        raise ImageError(f"modify op '{op}' needs a fal model; '{resolved}' resolves to {backend_name}")
    backend = get_backend(backend_name)

    body: dict = {"image_url": backend.encode_image_data_uri(source, max_edge=2048)}
    if op == "upscale":
        body["upscale_factor"] = int(upscale_factor)
    elif op == "bg_remove":
        body["output_format"] = "png"
    elif op == "inpaint":
        if not mask:
            raise ImageError("inpaint needs a --mask image (white pixels = region to edit)")
        if not prompt:
            raise ImageError("inpaint needs a prompt describing the masked region")
        body["mask_url"] = backend.encode_image_data_uri(mask, max_edge=2048)
        body["prompt"] = prompt
    elif op == "outpaint":
        px = int(expand)
        body.update({"expand_top": px, "expand_bottom": px, "expand_left": px, "expand_right": px})
    else:
        raise ImageError(f"unknown modify op: {op}")

    url = backend.build_url(model_id)
    if dry_run:
        # Summarize ONLY the image-bearing fields — never scalars like prompt
        # (a prompt starting with "data:" must not be mistaken for a data-URI).
        plan = dict(body)
        for k in ("image_url", "mask_url"):
            if k in plan:
                plan[k] = _summarize_data_uri(plan[k])
        return {"url": url, "model": model_id, "backend": backend_name, "op": op, "body": plan}

    key = backend.auth_token()
    raw = backend.submit_and_download(url, body, key, media_type="image")
    out.write_bytes(raw)
    return out
