# Throughput & rate limits — all providers

Researched 2026-06-22. The problem: nazca's default path (Vertex `nano-banana-*`,
synchronous, global endpoint) is capped at **2 images/min per base model** — a hard
wall for any bulk job (840 imgs ≈ 7–14h). This doc maps the escape hatches across
**every provider nazca routes to**, with the fix ranked.

## TL;DR ranking for a large ref-restyle batch

| Rank | Path | Throughput | Cost/img | Ref-to-img | In nazca today? |
|---|---|---|---|---|---|
| 🥇 | **ModelArk Seedream 4.0** | **500 img/min** | **$0.03** | ✅ native (2–10 refs) | ✅ `seedream` (sync) — needs account activation |
| 🥈 | **Vertex Batch** (Gemini image) | no preset cap (shared pool) | **−50%** ($0.05/img @1K) | ✅ | ❌ async job mode not wired |
| 🥉 | **fal.ai** (nano-banana / FLUX) | concurrency 2→40 self-serve | $0.04 (nano) / $0.003–0.04 (FLUX) | ✅ (Kontext/redux) | ✅ `flux-*` (sync `run`) |
| — | Vertex online + quota bump | 2 → 60+ RPM (request) | $0.067 @1K | ✅ | ✅ default path |
| — | Vertex online, fixed throttle | true 2/min (≈2× today) | $0.067 | ✅ | ⚠️ throttle logic bug |

## Per-provider detail

### Google Vertex (default path) — the bottleneck
- **Online quota:** `GenContentImageGenRequestsPerMinutePerProjectPerBaseModelGlobal`
  = **2 RPM**, per base model, global endpoint. Default, **increasable** (Cloud Quotas
  → `aiplatform.googleapis.com` → request 30–60 RPM). Refills per-minute; 429 = pacing.
- **Two free wins without a quota bump:**
  1. **Fix the throttle**: pace request *starts* ~30s apart (2/min) with gens overlapping,
     instead of `gen + sleep 32s` after completion (= 1.15/min, wastes ~40%). ≈2× faster.
  2. **Stack base-model buckets**: pro / 3.1-flash / 2.5-flash are **independent** 2/min
     counters → run as parallel workers = up to 6/min combined.
- **Vertex Batch Prediction** (the real Vertex fix): submit a JSONL of requests as an
  async batch job — **no predefined per-minute quota** (large shared pool) and a flat
  **50% cost reduction**. Gemini image output works in batch ("submit multiple multimodal
  requests as a Vertex AI Batch job"). Trade-off: async (submit → poll → fetch from GCS),
  minutes-to-hours latency, needs a GCS bucket. Not wired in nazca yet.

### ByteDance ModelArk — Seedream 4.0 (best for bulk)
- **500 IPM** (images/min) — 250× the Vertex online limit. Effectively no throttle for 840.
- **$0.03/image** (cheapest of all), billed only on success.
- Native **multi-reference image-to-image** (2–10 refs), 1K–4K output, plus a "group image"
  mode (one call → up to 15 related images — could generate several formats per request).
- Model id `seedream-4-0-250828` — **already nazca's `seedream`** (sync `/images/generations`).
- Caveats: (1) model must be **ACTIVATED per account** in the BytePlus console + region
  `ap-southeast` (see LEARNINGS); (2) different model family ⇒ different aesthetic than the
  Gemini pro heroes — **needs a brand-fidelity probe** before committing the bulk; (3) also
  has burst-traffic limits (RPM/TPM/concurrency/growth-rate) but the 500 IPM ceiling is the
  relevant one here.

### fal.ai — concurrency model (not RPM)
- No per-minute cap; a **global concurrency limit** = how many run at once. New accounts **2**,
  scales with 4-week paid-invoice total up to **40** self-serve (more via sales). Queue never
  drops requests — over-limit ones wait with server-side backoff (`subscribe()` recommended).
- Pricing: nano-banana **$0.0398/img**, FLUX schnell **$0.003/MP**, FLUX Kontext Pro **$0.04/img**.
- Good fallback / long-tail; FLUX restyle of food may drift off-brand vs Gemini — probe first.
- nazca wires `flux-schnell` (and the registry can add Kontext for ref edits). Uses sync `run`;
  for bulk, switch to queue `subscribe` to exploit concurrency.

## Recommendation for the DG 840-image bulk
1. **Probe Seedream 4.0** on 2–3 DG dishes (anticuchos, parrillada, a cocktail) vs the pro
   heroes. If brand-faithful → run the whole bulk on `seedream` (~$25, minutes). This is the
   single biggest unlock and it's already in nazca.
2. **If we must stay on Gemini** for aesthetic consistency → implement **Vertex Batch** (no RPM
   wall, −50%). Best when latency doesn't matter.
3. **Interim, no new code/accounts** → fix the throttle to true 2/min start-pacing + fan out
   pro+flash buckets (≈4–6/min), and/or request the Vertex RPM bump.

## nazca changes implied
- Add a **batch/async mode** for Vertex (JSONL → `batchPredictionJobs` → poll → GCS fetch).
- Add a **start-paced concurrent batcher** (token-bucket on request starts, not post-sleep),
  with `--rpm` and multi-model fan-out.
- Expose `seedream` group-image mode (`sequential_image_generation`) for N-formats-per-call.
- fal: prefer queue `subscribe` over `run` for batches.
