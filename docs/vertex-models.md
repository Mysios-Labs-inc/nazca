# Vertex AI model inventory

Functionally probed 2026-06-06 (real API calls, not metadata reads — the
metadata GET returns 403 here regardless, so only a live call is trustworthy).
Status: ✅ generated successfully · ❌ 404 not available · ⏳ documented, not live-probed.

## Image — Gemini family (`:generateContent`, supports `--ref` image-to-image)
| shorthand | model id | region | --ref | legible text | status |
|---|---|---|---|---|---|
| `nano-banana` (default, fast) | `gemini-2.5-flash-image` | us-central1 | yes | ❌ garbles | ✅ |
| `nano-banana-3` | `gemini-3.1-flash-image` | **global** | yes | ~ok | ✅ (GA) |
| `nano-banana-pro` | `gemini-3-pro-image` | **global** | yes (up to **14 refs**) | ✅ **legible** | ✅ (GA) |
| — | `gemini-2.0-flash-preview-image-generation` | us-central1 | — | — | ❌ 404 |

> **Big deal:** `gemini-3-pro-image` (GA) renders **legible baked-in text** (verified:
> clean gold wordmark) and accepts **up to 14 reference images** (subject +
> wordmark + style refs for brand consistency). nano-banana 2.5 cannot render text.
> → For some designed posts this can replace the separate overlay step; for exact
> brand fonts/hex/wordmark, the Pillow/Figma overlay is still more controllable.

## Image — Imagen family (`:predict`, text-to-image ONLY, no `--ref`)
| shorthand | model id | region | status |
|---|---|---|---|
| `imagen-4-fast` | `imagen-4.0-fast-generate-001` | us-central1 | ✅ |
| `imagen-4` | `imagen-4.0-generate-001` | us-central1 | ✅ |
| `imagen-3` | `imagen-3.0-generate-002` | us-central1 | ✅ |
| (edit/customization) | `imagen-3.0-capability-001` | us-central1 | ⏳ not wired (would add Imagen ref-edit) |

## Video — Veo (`:predictLongRunning`, start + optional end frame)
| shorthand | model id | region | status |
|---|---|---|---|
| `veo-fast` (default) | `veo-3.1-fast-generate-001` | us-central1 | ✅ (made real DG clips) |
| `veo` | `veo-3.1-generate-001` | us-central1 | ⏳ documented; higher fidelity, not live-probed (cost) |

## Text — Gemini (`:generateContent`) — for prompt-writing / agents, not used by nazca
| model id | region | status |
|---|---|---|
| `gemini-3.5-flash` | global | ✅ (near-Pro at flash cost) |
| `gemini-3.1-flash-lite` | global | ✅ (cheapest, high-volume) |
| `gemini-2.5-pro` | us-central1 | ✅ |
| `gemini-2.5-flash` | us-central1 | ✅ |
| `gemini-2.5-flash-lite` | us-central1 | ✅ |
| `gemini-3-flash-preview` | global | ✅ |
| `gemini-3.1-pro` / `gemini-3-flash` / `gemini-3-pro-preview` | global | ❌ 404 (not provisioned) |

Per the official catalog there's also: Lyria 3/2 (music), Imagen (Model Garden),
Gemma (open models), embeddings (gemini-embedding-001, multimodalembedding). Not
wired into nazca.

## How to use them (guidance)
- **Restyle a real product photo** → `nano-banana` (default) or `nano-banana-pro`
  with `--ref`. Pro is higher fidelity but lives in `global`. This is the
  primary path for food/product content (keep the real dish, change the look).
- **Fresh text-to-image** (no source photo) → `imagen-4` (fidelity) or
  `imagen-4-fast` (speed). NOTE: text-to-image invents content (e.g. it added
  off-brand bell peppers in testing) — prefer restyle for brand accuracy.
- **Video** → `nazca video` (Veo 3.1 fast); single start frame for camera
  moves, start+end for tight keyframe transforms.

## Re-running this inventory
```bash
# functional probe pattern (gemini image):
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"contents":[{"role":"user","parts":[{"text":"red dot"}]}],"generationConfig":{"responseModalities":["IMAGE"]}}' \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/<your-project>/locations/us-central1/publishers/google/models/<MODEL>:generateContent"
```
Model IDs change often — re-probe before trusting. global region uses host
`aiplatform.googleapis.com` + `locations/global`.

## Resolution + "cinematic" finding (2026-06-07)
Why Vertex output felt less premium than Higgsfield was **not the engine** (both
use the nano-banana family) — it was two defaults:
1. **Resolution.** `imageSize` 1K/2K/4K is honored by **gemini-3 image models only**
   (`gemini-3.1-flash-image`, `gemini-3-pro-image`). `gemini-2.5-flash-image`
   ignores it and stays ~1K (896-928×1152). Higgsfield emits 2K (1856×2304).
   → for finals use `nano-banana-pro --size 2K` (== 1856×2304) or `4K` (3712×4608).
2. **Grade.** Raw output skews oversaturated (~0.8 HSL-S = "AI look"). Cinematic =
   restrained saturation (~0.45-0.56) + deeper shadows. Prompt for it: "cinematic
   film grade, muted/natural saturation, rich shadows, not instagram-bright."

Recipe for premium DG stills: `nano-banana-pro` + `--size 2K` + brasa-atmosphere,
restrained-grade prompt. Drafts: `nano-banana` (cheap, 1K).

## Rate limits (2026-06-22) — READ BEFORE ANY BATCH
Default Vertex image-gen quota is **2 requests/minute, per base model**, on the
**global** endpoint:
- Binding quota: `GenContentImageGenRequestsPerMinutePerProjectPerBaseModelGlobal` = **2 RPM**.
  Token-input quotas (`GenContentImageInputPerMinute...` ~1.7–6.7M/min) never bind.
- `429 RESOURCE_EXHAUSTED` = pacing, **not** a ban and **not** a daily cap; refills each minute.
  **Pace ≥30s/call (`THROTTLE=32`) to stay under 2/min.** Bounded retries + exp backoff, never tight loops.
- **Per-base-model buckets are independent** → `gemini-3-pro-image` and `gemini-3.1-flash-image`
  each get their own 2/min. Split heroes→pro, bulk→flash for ~4/min combined.
- At 2/min a large batch is hours (840 imgs ≈ 7h). **Request an RPM quota increase**
  (GCP Cloud Quotas → `aiplatform.googleapis.com`) before any big run; 60/min ⇒ ~15 min.

Check current limit:
```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://cloudquotas.googleapis.com/v1/projects/<PROJ>/locations/global/services/aiplatform.googleapis.com/quotaInfos?pageSize=2000" \
  | grep -A4 GenContentImageGenRequestsPerMinute
```
