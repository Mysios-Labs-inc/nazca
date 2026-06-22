# Throughput implementation plan (nazca)

What to build *into* nazca so the throughput fixes stop living in external scripts.
Grounded in the current source (branch `MRCORD/nazca-context`). Companion to
`docs/throughput-and-rate-limits.md` (provider limits) and `docs/LEARNINGS.md`.

Context: Vertex image gen is capped at **2 req/min per base model** (default, not
self-serve increasable yet — `NOT_ENOUGH_USAGE_HISTORY`). The DG bulk (840 imgs)
exposed that nazca has **no retry, no batch, no pacing** — all of that was bolted on
in `marketing/scripts/{make_variations,batch_gen}.{sh,py}`. Port it in, ranked.

---

## Tier 1 — do first (small, high leverage)

### A. 429/503 retry + backoff in the backends
**File:** `src/nazca/backends/vertex.py:176` `post()` (also `fal.py`, `modelark.py`).
Today `post()` raises immediately on `HTTP 429` (`VertexError`). Every caller
(CLI, MCP, any batch) inherits zero resilience — the bash scripts had to wrap it.

- Add bounded exponential backoff on `429` and `503` (and `RESOURCE_EXHAUSTED` in
  body): e.g. 5 tries, 20→40→80→160s, jittered. Make it opt-out via arg/env
  (`NAZCA_MAX_RETRIES`, default 5) so the MCP path can stay snappy.
- Return a typed `RateLimitError(VertexError)` when retries exhaust, so batch logic
  can distinguish "paced wrong" from "real failure".
- fal already documents server-side requeue — for the fal backend prefer surfacing
  the `X-Fal-needs-retry` header semantics; for raw HTTP use the same backoff.

### B. `nazca batch` command  ← the main fix
**Files:** new `src/nazca/batch.py`, wire in `src/nazca/cli.py` (new `@cli.command`).
Port `marketing/scripts/batch_gen.py` (proven: 0 429s, true 2/min) into nazca.

- **Input:** a JSONL/CSV manifest — one row per image: `{out, prompt, ref(s), model,
  aspect, size}`. Also accept `--from-dir` glob → caller supplies prompt template.
- **Rate limiter:** token bucket pacing request **starts** at `60/rpm` (+small margin),
  NOT a post-gen sleep. This is the bug that wasted ~40% (52s vs 32s between starts).
- **Multi-lane:** group rows by `model`; one worker thread per distinct base model,
  each its own bucket → N models = N×rpm combined (pro+flash+2.5 = 6/min). Keep all
  rows of one logical asset on one lane for visual consistency.
- **Idempotent:** skip when `out` exists. **Resumable:** safe to re-run to fill gaps.
- **Flags:** `--rpm` (per-lane, default 2), `--models`, `--size`, `--concurrency`,
  `--dry-run`. Emit a start-summary with the throughput estimate.
- Reuse `generate_image()` per row; depends on (A) for backoff.

---

## Tier 2 — the real escape hatches

### C. Make ModelArk Seedream actually usable (ref-to-image)
**File:** `src/nazca/image.py:252-278` (ModelArk dispatch) + `backends/modelark.py`.
**Bug:** the current body is **text-to-image only** — `refs` are counted then *never
sent*, and `size`/`aspect_ratio`/`response_format` are UNVERIFIED guesses. So our
single biggest throughput unlock is currently dead code.

Per BytePlus docs (researched 2026-06-22): Seedream 4.0 = **500 IPM**, **$0.03/img**,
native **multi-reference image-to-image** (2–10 refs), model `seedream-4-0-250828`.
- Send refs in the real `image`/`images` field (URLs or base64 data-URIs per the
  ModelArk image-generation API), set `sequential_image_generation: "disabled"` for
  single-image, map `--size`/`--aspect`. Verify field names against
  `docs.byteplus.com/en/docs/ModelArk/1541523`.
- Update the stale price comment (`image.py:51`: "~$0.035/img" → **$0.03**) and add the
  500 IPM note. Requires model **activation** in the BytePlus console (region
  `ap-southeast`) + balance — flag clearly on auth failure.
- Optional: expose Seedream **group-image** mode (`sequential_image_generation: auto`,
  up to 15 related images per call) → N formats per request, a different throughput axis.

### D. Vertex Batch Prediction mode (no RPM wall, −50%)
**Files:** new `src/nazca/vertex_batch.py`, `nazca batch --vertex-batch`.
Async `batchPredictionJobs`: write JSONL of `generateContent` requests to GCS →
submit job → poll → read predictions from GCS. **No per-minute quota** (shared pool),
flat **50% cost cut**. Confirmed Gemini image output works in batch.
- Needs a GCS bucket (`--gcs gs://…`) and the job lifecycle (submit/poll/fetch).
- Open question to verify before building: whether the **global-only** image models
  (`gemini-3-pro-image`, `gemini-3.1-flash-image`) are batch-eligible, or whether batch
  only covers regional `gemini-2.5-flash-image`. Probe with a 2-row job first.

---

## Tier 3 — cleanup / already staged

- **Docs already updated on this branch** (uncommitted): `docs/LEARNINGS.md`
  (rate-limit section), `docs/vertex-models.md` (rate-limit section),
  `docs/throughput-and-rate-limits.md` (provider matrix). **Correction needed:** the
  LEARNINGS line quoting pro at "~$0.10/img" should be **$0.134 (1K/2K), $0.24 (4K)**;
  flash-3.1 is the $0.067/0.101/0.151 one.
- **Quota preference filed:** `dg-imagegen-bump-20` (Cloud Quotas) — currently granted
  2, auto-upgrades to 20 once usage history accrues. No code needed; note in ops docs.
- Consider a `nazca quota` helper that prints the live image-gen RPM + eligibility via
  the Cloud Quotas API (the curl in `throughput-and-rate-limits.md`).

---

## Suggested order
1. **A** (backoff) → unblocks safe batching. ~30 LOC.
2. **B** (`nazca batch`) → the throughput win for Vertex today. Port of proven script.
3. **C** (Seedream refs) → the 500-IPM/$0.03 escape hatch; biggest single unlock if quality probes well.
4. **D** (Vertex batch) → only if staying on Gemini for uniformity at scale.
5. **Tier 3** doc/metadata fixes alongside whichever PR touches them.
