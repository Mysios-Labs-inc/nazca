# Changelog

All notable changes to nazca are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/) (pre-1.0: minor = features, patch = fixes).

## [0.12.0] — 2026-07-01

### Added
- **Gemini Omni Flash (`omni-flash` video model):** new Vertex-backed video model
  (`gemini-omni-flash-preview`), routed through a new `api="omni"` sub-route that
  calls `:generateContent` synchronously (no long-running-operation polling, unlike
  Veo). Supports t2v, i2v, ref2v (up to 2 reference images verified live; Google's
  docs example goes to 6), and v2v (local-file video edit — the opposite of fal's
  URL convention). Fixed output: ~10s / 720p / 24fps, always includes audio, no
  aspect-ratio control. `--v2v`/`--ref2v` reuse the existing CLI flags and ops
  vocabulary.
- **Nano Banana 2 Lite (`nano-banana-2-lite` image model):** new Vertex-backed
  image model (`gemini-3.1-flash-lite-image`) for fast/cheap 1K generation and
  single-reference editing ($0.034/image); rides the existing `api="gemini"` path,
  no new code needed beyond the registry entry.

### Fixed
- **`_resolve_video` region/api propagation:** the Vertex video resolver hardcoded
  `api=""`/`region=""` on every resolution, silently discarding a model's declared
  `api` sub-route (invisible until `omni-flash` needed `api="omni"` to route off
  the Veo `predictLongRunning` path). Now reads both fields from the model's spec.
- **Omni Flash dry-run cost:** `--v2v --model omni-flash --dry-run` was reading and
  base64-encoding the entire local source video before redacting it for the
  preview; now derives the redacted placeholder from the file's on-disk size
  without ever reading its contents.

## [0.11.0] — 2026-06-28

### Added
- **Virtual Try-On (`try_on` op):** new image operation backed by Vertex AI
  `virtual-try-on-001` (GA) — dress a person photo in one or more garment/product
  images. It rides the predict-style path (like Imagen/Veo) via a new Vertex
  `api="vto"` sub-route and reuses the Imagen response extractor. Surfaces:
  `nazca try-on PERSON GARMENT... -o out.png` (variadic garments, up to 4) and the
  `try_on_image` MCP tool. Reuses existing `ImageRequest` fields (person → source,
  garments → refs); no new request knobs.

### Fixed
- **Try-on cost estimate:** a no-model `try_on_image` call (MCP tool / direct API)
  reported the `nano-banana` default price; now keyed to the resolved `try-on`
  model (price unset → cost-unknown).

> ⚠️ `virtual-try-on-001` is wired and unit-/dry-run-tested but **not yet validated
> against a live Vertex call** — confirm the served region (`us-central1` assumed)
> and set the per-image `price_usd` (currently `None` = cost-unknown) before relying
> on it. Run `pytest -m live -k try_on` against a project with the model enabled.

## [0.10.1] — 2026-06-26

### Fixed
- **Vertex Batch output correlation (silent data corruption):** batch predictions
  return out of input order; the old hash-keyed match silently fell back to
  *positional* mapping → images written to the wrong `out` path, identities
  cross-contaminated. Now correlates by `request_signature` (prompt + ref URIs,
  order-independent) and errors on an unmatched line instead of guessing.
- **Vertex Batch long-job auth-token expiry (lost jobs):** the ~1h ADC token was
  minted once and reused for the whole poll+download, so a 30-min+ job 401'd and
  nazca abandoned a job still succeeding server-side. Now mints a fresh token
  before every long-phase call and re-auths once on a 401.

### Added
- Clean one-line 429 errors (no traceback) pointing at `nazca batch`; server
  `Retry-After` honored as a backoff floor; `nazca batch --status`; manifest
  schema + `nazca batch` README section.

> ⚠️ `vertex_batch` is unit-tested against documented response shapes but **not yet
> validated against a live Vertex Batch job** — run one small live `--vertex-batch`
> job before a large bulk. (`docs/batch-followups.md`)

## [0.10.0] — 2026-06-26

A large internal architecture refactor (no behavior change) plus a few
backward-compatible user-facing additions.

### Added
- **Diagnostics logging** — global `-v` / `-vv` flags and the `NAZCA_LOG_LEVEL`
  env var. Diagnostics go to **stderr only** (stdout/`--dry-run` JSON stays clean
  and pipeable); off by default. Surfaces submit→poll loops, retries, and
  auth-token minting, with secrets/data-URIs redacted. (`nazca.log`)
- **`nazca models` confidence marker** — models whose cost/schema is not
  live-verified (`atlas` / `fal` / `modelark` backends) are flagged `⚠`;
  `vertex` / `openai` rows are unmarked.
- **README** — documents the `speak` (TTS) and `make3d` (3D/GLB) commands, the
  image modify ops, the Atlas video ops (`--avatar`/`--ref2v`/…), the Atlas Cloud
  credentials + `atlas:` passthrough, and the new diagnostics flags.

### Changed (internal refactor — no behavior change)
- **Unified model resolution** — the four hand-rolled resolvers collapse into one
  `nazca.resolve.resolve(model, modality)` returning a typed `ResolvedModel`.
- **Registry is the single source** — `models.py` now owns the registry, the
  derived accessors (`models_for` / `tiers` / `tier_default`), **and** every named
  projection (`VEO_ALIASES`, `FAL_VIDEO_MODELS`, `MODEL_TIERS`, …). Orchestrators
  re-export for back-compat and are pure consumers.
- **Uniform backend seam** — `run_<modality>(resolved, req)` across all backends;
  the leaked `api`/`region` positional args and `""` placeholders are gone.
- **Capability protocols (ISP)** — `@runtime_checkable` `SupportsImage` /
  `SupportsVideo` / `SupportsAudio` / `SupportsThreeD`; `Backend` no longer carries
  dead `NotImplementedError` stubs. `require_capability()` guards dispatch with a
  clear error.
- **Errors consolidated** into `nazca.errors` (`VideoError`, with `VeoError` kept
  as an alias; `AudioError`/`ThreeDError` re-homed).
- Test→backend-internal coupling removed (public `vertex.gemini_extract`); stale
  docstrings corrected.

## [0.9.0] — 2026-06-26
- Atlas Cloud provider; audio (TTS) and 3D (GLB) modalities; ~91-model Atlas registry.

## [0.8.1] — 2026-06-26
- `nazca grade`: support oversized LUTs (RawTherapee level-12 HALDs) via 3-D resample.

## [0.8.0] — 2026-06-25
- `nazca grade` (local LUT color grading) and `nazca format` (head-safe platform crops);
  bundled CC0 looks; monochrome film grain.

## [0.7.0] — earlier
- Architecture refresh + public library API.

Earlier releases (0.6.0 and prior) are recorded in the git tag history (`git tag`).

[0.10.1]: https://github.com/Mysios-Labs-inc/nazca/releases/tag/v0.10.1
[0.10.0]: https://github.com/Mysios-Labs-inc/nazca/releases/tag/v0.10.0
[0.9.0]: https://github.com/Mysios-Labs-inc/nazca/releases/tag/v0.9.0
[0.8.1]: https://github.com/Mysios-Labs-inc/nazca/releases/tag/v0.8.1
[0.8.0]: https://github.com/Mysios-Labs-inc/nazca/releases/tag/v0.8.0
[0.7.0]: https://github.com/Mysios-Labs-inc/nazca/releases/tag/v0.7.0
