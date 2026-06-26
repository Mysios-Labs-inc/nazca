# Changelog

All notable changes to nazca are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/) (pre-1.0: minor = features, patch = fixes).

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
