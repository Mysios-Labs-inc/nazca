# mediagen — competitive landscape, structuring patterns, cheaper-model strategy

**Date:** 2026-06-18 · **Confidence:** Medium-High (multiple 2026 sources; pricing moves fast, treat numbers as ±20% and re-verify before committing spend)

## Executive summary

mediagen today is a **single-provider, single-auth wrapper** (Google Vertex only, gcloud token). The
market has moved to a **multi-model aggregator** pattern: the median production deployment uses **14
different models** because no single model wins every task. The two ways teams get there:

1. **Use an aggregator** (fal.ai, Replicate) — one API key, hundreds of models, one bill.
2. **Build a thin router** over 2–3 direct providers and pick the cheapest model that clears a quality bar per task.

mediagen is *already structured* for option 2 — its `MODELS` map + shared `vertex.py` is exactly the
seam an aggregator/router needs. The cheap win is to generalize `vertex.py` into a `backends/` layer and
add a `fal` backend (breadth + cheapest tier) and optionally a `modelark` backend (ByteDance, cinematic).

---

## 1. What others are doing & how they structure it

| Provider | Type | Image models | Video models | Billing | Notes |
|---|---|---|---|---|---|
| **fal.ai** | Aggregator | 406+ | 450+ (Kling, Veo, Seedance, Wan, LTX) | output-based (per image / per sec) | 50% image / 44% video market share; ~30–50% cheaper; `@fal-ai/client`; $10 free |
| **Replicate** | Aggregator | ~200 | Kling, Veo, Wan | per-second compute | Best docs; **custom model hosting**; ~30–50% pricier than fal |
| **ByteDance ModelArk** | Direct | Seedream 5/4.5/4.0 | Seedance 2.0 Fast/Pro | token-billed | Cinematic quality leader; native audio + lip-sync; 720p cap |
| **Google Vertex/Gemini** | Direct (*what mediagen uses*) | Nano Banana Pro, Imagen 4 | Veo 3.1, Veo 3.1 Lite | per-image / per-sec | gcloud auth; Veo 3.1 Lite $0.05/s 720p |
| **OpenAI** | Direct | GPT Image 1.5, DALL-E | ~~Sora 2~~ (killed Mar 2026) | per-image | Best text-in-image |
| **AWS Bedrock** | Cloud MaaS | Nova Canvas, Stability suite | Nova Reel, Luma Ray v2 | per-output | One IAM path for many vendors |
| **Azure AI Foundry** | Cloud MaaS | via Foundry Models | (limited) | MaaS | Bedrock analog |
| **Runway / Luma** | Direct | limited / none | Gen-4.5 / Dream Machine 2 | credits/subscription | Creator-tooling, not API-first |

**Structuring conventions observed:**
- **Shorthand → (model_id, region/endpoint, api_shape)** map — exactly what mediagen's `MODELS` dict is.
- **Per-provider "backend" adapters** behind one call signature (`generate_image`, `generate_video`).
- **Submit→poll→download** for video everywhere (long-running ops); mediagen already does this for Veo.
- **Cost-aware routing**: atlascloud's own guide recommends *"build a routing system that automatically
  selects the cheapest acceptable model for each use case"* — a `select_model(use_case, needs_audio, min_duration)` fn.

## 2. Cheaper-model tiers (mix-in targets)

> **Pricing provenance:** Google/Vertex figures below are from **Google Cloud's official pricing page**
> (verified 2026-06-18) — authoritative, since that is what mediagen bills against. fal figures are from
> fal's own pages. **Seedance/ByteDance figures are NOT reliable** (see §3) — resolution/tier dominates.

**Image — Google Vertex (official, what mediagen uses):**
- Imagen 4 Fast **$0.02/img** · Imagen 4 / Imagen 3 **$0.04/img** · Imagen 4 Ultra **$0.06/img** (flat per-image).
- `nano-banana` (Gemini 2.5 Flash Image): token-billed, ~**$0.039/img** at 1K (effectively the cheap default).
- `nano-banana-pro` (Gemini 3 Pro Image): output **$0.134/img at 1K–2K**, **$0.24 at 4K** — *premium, not ~$0.04*.
- `nano-banana-3` (Gemini 3.1 Flash Image): **$0.045** (512px) / **$0.067/img** (1K).

**Image — fal (other providers):** FLUX.1 [schnell] **$0.003 / megapixel** · up to **$0.15/img** premium. (The $0.003 is per-MP for schnell, *not* a flat SDXL price.)

**Video — Google Vertex Veo (official, per second):**
- Veo 3.1 **Lite**: **$0.05/s** (720p) · Veo 3.1 **Fast**: **$0.10/s** (720p), $0.12/s (1080p) · Veo 3.1 (video only): **$0.20/s** · Veo 3.1 **+ audio**: **$0.40/s** · Veo 2: $0.50/s.
- **mediagen's default `veo-3.1-fast` ≈ $0.10/s (720p)** → switching to **Lite ($0.05/s) is a real ~2x saving** (Phase 0).
- `--audio` **doubles** cost ($0.20→$0.40/s) — clips are silent by default; keep them silent unless needed.

**Video — fal (other providers, fal's listing):** Wan 2.6 ~$0.05/s · Kling 3.0 Pro ~$0.09/s · Veo 3.1 Lite ~$0.05/s.

**Cheapest mix without leaving Vertex (zero new auth):**
- Image cheap → `nano-banana` (~$0.039) or `imagen-4-fast` ($0.02); premium → `nano-banana-pro` (~$0.134/img@2K).
- Video cheap → `veo-3.1-lite` ($0.05/s); premium → `veo-3.1-fast` ($0.10) / `veo-3.1` full ($0.20, +audio $0.40).

**Cheapest mix with one new key (fal):** FLUX schnell ($0.003/MP), Flux 2, Seedance, Wan, Kling under a single `FAL_KEY`.

## 3. ByteDance specifically

- **Seedream** = image family (5.0 Jan 2026 beats Flux 2 on cinematic/multi-figure); **Seedance** = video (2.0 = cinematic leader, native audio, multi-shot, lip-sync 8+ langs).
- **Access:** direct via **ModelArk** (`ARK_API_KEY`, token-billed, dashboard billing lags) **or** via **fal/Replicate/Atlas Cloud** (one key, no separate account).
- **Pricing is unreliable / tier-and-resolution dependent — do NOT cite a single $/s.** Observed range across resellers: Seedance 2.0 **Fast ~$0.022/s**, **Pro ~$0.247/s** (≈10x apart), with 480p vs 720p roughly $0.10 vs $0.20/s elsewhere. The earlier "~$0.03–0.05/s" and "ModelArk ~25% cheaper than fal" claims are **retracted** — verify per-call at your target resolution before committing.
- **Caveats:** 720p output cap (upscale in post); flags close-up human faces as privacy risk.
- Watch: Alibaba **Wan-next** topped the Video Arena (Apr 2026), launching via ModelScope/fal.

## 4. Recommendation for mediagen (keep it thin)

Preserve the "two commands, prints a path" ethos. Generalize the auth seam, don't bolt on an SDK zoo.

1. **Refactor `vertex.py` → `backends/` interface**: `get_token()`, `build_url()`, `post()`, `extract()`.
   Vertex becomes `backends/vertex.py` (unchanged behavior).
2. **Add `backends/fal.py`** (`FAL_KEY`, `urllib` POST to fal queue + poll) — instant breadth + cheap long-tail (FLUX, Flux 2, Seedance, Wan, Kling). Keeps the "stdlib HTTP, no SDK" rule.
3. **Extend the `MODELS` map** with a `backend` field: `(model_id, location/endpoint, api, backend)`. CLI/`--model` stays identical; routing is data, not code.
4. **Add `veo-3.1-lite` now** — pure Vertex, half-price video, zero new dependency.
5. **(Optional) cost tiers**: `--tier cheap|premium` or a `select_model()` helper so the agent (Claude) can ask for "cheapest acceptable" — matches how the market routes.

This keeps mediagen the "hands": still one path per provider, still `--dry-run`, still a few hundred lines —
but now spans Vertex + fal (+ ByteDance via fal) and exposes a cheap/premium lever the agent can pull.

---

## Sources
- **Google Cloud — official pricing** (`cloud.google.com/.../generative-ai/pricing`, verified 2026-06-18) — **authoritative** Veo (Lite $0.05/s, Fast $0.10/s, full $0.20/s, +audio $0.40/s) and Imagen/Gemini-image figures. Use these over any secondary source.
- fal.ai — `/pricing`, `/learn/tools/ai-image-generators` — FLUX schnell $0.003/MP, image range to $0.15.
- Seedance pricing (Atlas Cloud, Gamsgo, EvoLink, Reddit) — **conflicting**; treated as unreliable/tier-dependent, not cited as fact.
- TeamDay.ai — *AI Image & Video API Providers 2026: Complete Comparison* (Apr 2026) — secondary; market share, Sora 2 shutdown, model landscape (pricing cross-checked against Google official above).
- Atlas Cloud — *Cheapest AI Video Generation APIs in 2026* — per-second ranking, ~7x spread, "build a router for cheapest acceptable model."
- pricepertoken.com/image — image API floor ($0.0020/image).
- fal.ai (homepage, learn/ai-video-generators, gen-media-report) — 1,000+ models, market share, video price entries.
- AWS (Bedrock model catalog 2026, Nova Reel blog), Microsoft Learn (Azure Foundry Models) — cloud MaaS image/video.
- seed.bytedance.com/Seedream4_0, fal.ai/seedance-2.0, atlascloud.ai/providers/bytedance — ByteDance access paths.
- OpenRouter / Inworld "Best LLM Router & AI Gateway 2026" — gateway/router structuring pattern.
