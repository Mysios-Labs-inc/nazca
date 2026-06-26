# Batch / rate-limit follow-ups

Field observations from a real bulk run (6 concurrent shell loops × synchronous
`nazca image`, all on one Vertex model → 429 `RESOURCE_EXHAUSTED`, ~13 images lost).
Three high-value items shipped alongside this doc; the rest are scoped here.

## Shipped (this change set)

- **🔴 Vertex Batch correlation fix (silent data corruption).** Vertex Batch returns
  predictions **out of input order** and echoes each request with extra/normalized fields.
  The old `_OutputSink` keyed off a full-body request hash and, when that missed on the first
  line, silently fell back to **positional** mapping — so every image landed at the wrong
  `out` path, identities cross-contaminated, and the wrong row was blamed for a safety filter
  (a real 20-row job: all 20 mislabeled). Now predictions correlate by `request_signature`
  (prompt text + ref URIs — the fields Vertex passes through verbatim), order-independent; an
  unmatched line is reported as an error instead of guessed into a position. Safety-filtered
  rows (`finishReason: IMAGE_SAFETY`, prompt `blockReason`) are reported with the **correct**
  identity and surfaced in the CLI summary (`written` + per-row failures, exit 1 on any).
  (`vertex_batch.py:request_signature`, `_OutputSink`, `_no_image_reason`;
  `cli.py:_run_vertex_batch_cmd`)
  - *Future hardening (the brief's option 2):* inject an explicit per-row passthrough id into
    each request and read it back. Strictly more robust for rows with an identical prompt **and**
    identical refs — but those produce identical images and identical safety verdicts, so the
    content signature is already correct for image-fidelity purposes. Deferred until a Vertex
    GenAI-batch passthrough field is confirmed to echo (the module is not yet live-validated).
- **🔴 Long-job auth-token refresh (poll crash).** The ADC/gcloud access token
  (~1h life) was minted once at the start of `run_vertex_batch` and reused for the whole
  poll + download. A long job (72 imgs, 30+ min, plus queue time) outlived it, so
  `GetBatchPredictionJob` 401'd (`UNAUTHENTICATED` / `ACCESS_TOKEN_TYPE_UNSUPPORTED`) and
  nazca abandoned a job that was still **running and succeeding** server-side. Now a fresh
  token is minted before every long-phase call — each poll iteration and each shard
  list/download — with a single re-auth-and-retry on a 401 (`_with_fresh_token`), so the
  loop survives arbitrarily long jobs. The job id + output dir are surfaced on submit
  (`submitted` event → `🔖 job …`) and returned in `summary["submitted"]`, so a billed job
  is recoverable even if the local process dies.
- **Clean 429, no traceback.** `nazca image` / `nazca video` now catch `BackendError`
  and print a one-line `❌` instead of a urllib stacktrace. A persistent rate limit adds
  a `↳` hint pointing at `nazca batch` / `--vertex-batch`. (`cli.py:_emit_backend_error`)
- **`Retry-After` as a backoff floor.** `retry.post_json` honors a server `Retry-After`
  (seconds) as the lower bound for the next delay, so a provider that tells us exactly when
  the quota refills self-recovers instead of us guessing short. (`retry.py:_retry_after_seconds`)
- **Manifest schema documented.** Row fields (`out`/`output`, `prompt`, `ref`/`refs`,
  `model`, `aspect`/`aspect_ratio`, `size`, `quality`) are in `nazca batch --help` and the
  README `### nazca batch` section, with the concurrency note (throughput scales with model
  lanes, not local processes).
- **`nazca batch <manifest> --status`.** Diffs each row's `out` against the filesystem,
  reports done/pending, exits 1 if any pending — no API calls. Detection no longer requires
  grepping logs for "429". (`batch.py:batch_status`)

## Deferred — larger / riskier, proposed

### 1. `--vertex-batch` live progress (`batch <id>: <state> N/M`)

Today `run_vertex_batch` emits `on_event("poll", state)` with the raw job state and the CLI
prints `⏳ <state>`; stdout is otherwise quiet until the job completes. The Vertex
`batchPredictionJobs` resource exposes `completionStats` (`successfulCount` / `failedCount` /
`incompleteCount`) on the GET. Proposal: thread those counts through the `poll` event and print
`⏳ <model> <state> · N/M done` each interval. Low risk (read-only field on a call already made
in `_poll`), but unverified against a live job — the module is explicitly "not yet validated
against a live batch job", so the field names need a live probe before relying on them.

### 2. `--upscale-keepers` (close the 1K loop)

Vertex Batch is 1K-only. The current follow-up is manual: re-run
`nazca image <out> --upscale --scale N` per keeper. Two options:

- **Documented recipe (cheap):** add a README snippet showing a shell loop over the kept 1K
  outputs through `nazca image --upscale`. Zero code, ships now.
- **Integrated flag (more work):** `nazca batch --vertex-batch ... --upscale-keepers 2` that,
  after fetch, feeds each written `out` through the existing fal upscaler as a second paced
  lane. Needs a keeper-selection story (all written rows? a curated list?) and interacts with
  idempotency (the upscaled file is a *different* path, so skip-if-exists still holds). Recommend
  shipping the recipe first; promote to a flag only if the manual step proves common.

### 3. `nazca batch --vertex-batch --resume` (don't re-pay for a finished job)

The token-refresh fix keeps a *running* process alive across a long job, but if the process
itself is killed (laptop sleep, Ctrl-C, OOM) the submitted job is now reported
(`summary["submitted"]` + the `🔖 job …` line) yet there is no first-class way to reconnect.
Proposal: persist a small sidecar per run — `{job_name, location, output_prefix, model,
signature→out map}` keyed by the input manifest — and add `nazca batch <manifest>
--vertex-batch --resume` that, instead of resubmitting (and re-paying ~$0.05/img), polls the
recorded job to completion and streams its predictions back via the existing signature
correlation. Skip-if-exists already makes the *download* idempotent; this adds idempotency to
the *submit*. Needs a state-file location decision (CWD sidecar like the dry-run
`*.request.json`, gitignored, vs a `~/.cache/nazca` dir) and a live job to validate the
reconnect path.

### 4. Quota-bump guidance / throttle parity (informational)

`docs/throughput-and-rate-limits.md` already captures the per-provider escape hatches
(ModelArk Seedream 500 IPM, Vertex Batch, fal concurrency, Vertex online quota bump). No code
change proposed; the 429 hint now routes users toward `nazca batch`, which is the right first
move. A Cloud-Quotas pointer ("request 30–60 RPM at `aiplatform.googleapis.com`") could be added
to the hint if users ask for raw online throughput rather than batching.
