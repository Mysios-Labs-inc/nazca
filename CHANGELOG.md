# Changelog

All notable changes to nazca are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/) (pre-1.0: minor = features, patch = fixes).

## [Unreleased]

### Added
- **Atlas music generation (`atlas-music-minimax` / `nazca music`, issue #122 phase
  A4):** `music` was named in `capabilities.AUDIO_OPS` since phase A1 but never
  wired by any model until now — this is the first. Wired `minimax/music-2.6`
  through Atlas Cloud's existing async submit→poll dispatch (`AtlasBackend.
  run_audio`, same seam as TTS — music is text/style-prompt → one audio file,
  just like TTS, unlike phase A2's `voice_clone`/`voice_design`, which needed a
  new module because their output shape genuinely differs). New CLI command:
  `nazca music "style prompt" [--lyrics "[Verse]\n..."] -o track.mp3`. A new
  `AudioRequest.lyrics` field and `audio.generate_music()` (a thin wrapper over
  `speak(..., op="music")`) carry the music-specific state. `$0.15/gen` is a
  **confirmed** price (unlike most Atlas entries, which are priced from
  marketing copy) — pulled directly from Atlas's live, public, no-auth
  `GET https://api.atlascloud.ai/api/v1/models` endpoint; the request/response
  *schema* is still unverified (`prompt`/`lyrics` field names are a best-effort
  guess following this file's established convention, same posture as every
  other Atlas model here).

  **A4 survey findings** (same live API call, 446 models total): Atlas's own
  catalog tags 17 models `TEXT-TO-SPEECH` — but that category conflates real
  TTS (2 already wired as `atlas-tts-grok`/`atlas-tts-elevenlabs-v3`, plus 6
  more unwired: `bytedance/seed-audio-1.0`, three `google/gemini-*-tts`
  variants, two `minimax/speech-2.6-*` variants — deferred, low marginal value
  given nazca already has 4 direct TTS providers) with actual music generation
  (`minimax/music-2.6`, wired here, plus 8 `suno/chirp-*` variants — deferred
  as a dedicated batch fast-follow rather than wiring 8 near-duplicates in one
  pass). `SPEECH-TO-TEXT` (2 models: `bytedance/seed-asr-2.0`, `xai/stt-v1`)
  stays out of scope per the standing issue #121 decision (analysis, not
  generation — doesn't fit nazca's generation-only CLI). `AUDIO-TO-VIDEO` (4
  models, a video-output/avatar modality, not this phase's concern) has 3
  already wired and one unwired `veed/fabric-1.0/fast` variant, noted but not
  built. See `docs/media-modalities.md`'s Audio roadmap for the full writeup.

- **ElevenLabs speech backend (`elevenlabs-tts` / `elevenlabs:<voice_id>`, issue #122
  phase A3, absorbs issue #121):** new opt-in TTS provider (`ELEVENLABS_API_KEY`)
  alongside Atlas/Worder/Fish as a fourth `nazca speak` option — a direct path to
  ElevenLabs' own model catalog, `voice_settings`, and output-format control,
  previously only reachable indirectly via one fixed Atlas-proxied model
  (`atlas-tts-elevenlabs-v3`). This pass is **TTS only**; sound effects, voice
  design, speech-to-speech, dubbing, etc. remain unwired (see
  `docs/media-modalities.md`'s Audio roadmap for the rest of A3). Two structural
  differences from every other backend in this codebase, called out prominently
  in `backends/elevenlabs.py`'s module docstring since they're exactly the kind
  of thing that bites the next person to extend it:
  - **Auth header is `xi-api-key: <key>`, NOT `Authorization: Bearer <key>`** —
    every other backend here (Fish, Worder, Atlas, OpenAI) uses Bearer auth.
  - **`voice_id` is a URL *path* parameter** (`POST
    /v1/text-to-speech/{voice_id}`), not a body field like Fish's
    `reference_id`/Worder's `voice_id`; `output_format` is likewise a **query
    string parameter**, not a body field like Fish/Atlas — nazca's
    `--format mp3|wav` maps to `mp3_44100_128`/`wav_44100`, omitted entirely
    when unset.

  Synchronous (no submit→poll): `/v1/text-to-speech/{voice_id}` streams raw audio
  bytes directly (not a JSON envelope), so it's POSTed via the existing
  `retry.post_bytes` helper, same as Fish. The `model_id` sent in the body
  defaults to `eleven_multilingual_v2` (ElevenLabs' own default, reused as-is —
  unlike Fish where nazca overrides Fish's own default) and is not
  user-configurable today; `voice_settings` is likewise omitted from the body
  entirely, letting ElevenLabs apply its own defaults. No default voice
  exists — callers must pass `--voice <voice_id>` (from `GET /v2/voices`, the
  modern paginated listing endpoint — `/v1/voices` also exists but ElevenLabs'
  own docs say it stops working past 500 voices in a workspace) or use the
  `elevenlabs:<voice_id>` prefix. Pricing is subscription-tier-based, not a
  simple per-request rate, so `elevenlabs-tts` has `price_usd=None` in
  `models.py` (`--dry-run` shows the request plan, not a cost estimate). Wired
  into `nazca login` / `nazca config`.

  **Error handling note:** ElevenLabs returns quota/credit exhaustion as HTTP
  **401** with a body `status` of `"quota_exceeded"` — NOT HTTP 402, which is
  what one might guess by analogy to Fish/Atlas/fal's conventions. Verified
  against ElevenLabs' own error-messages docs rather than assumed; the new
  `error_hints.py` entry distinguishes the two 401 cases (invalid key vs.
  quota) by body substring. 429 (`too_many_concurrent_requests` /
  `system_busy`) is the genuine rate-limit signal, matching `retry.py`'s
  `RETRYABLE_STATUS`.

  *Status: integrated per the published OpenAPI schema and public docs,
  unverified against a live key.*

### Fixed
- **ElevenLabs' `voice_id` was interpolated unescaped into the request URL.**
  Unlike Fish/Worder (voice is a JSON body field, so `json.dumps` handles
  escaping automatically), ElevenLabs bakes `voice_id` into the URL path —
  a `--voice` value containing `?`/`&` could hijack the query string (letting
  it silently override `output_format`), and a space/control character raised
  a raw `http.client.InvalidURL` that isn't a `BackendError`, so it slipped
  past the CLI's `except BackendError` entirely instead of the intended clean
  `❌ ...` message. Now percent-encoded via `urllib.parse.quote`/`urlencode`.
- **An unsupported `output_format` was silently dropped** instead of raising —
  unreachable via the CLI (`--format` is restricted to `mp3`/`wav`), but a
  direct library caller passing e.g. `"ogg"` got a request silently sent with
  ElevenLabs' *own* default format instead of an error saying so.
- **Fish Audio `voice_clone` / `voice_design` (issue #122, phase A2):** two new
  Fish Audio endpoints are wired — `POST /model` (create a reusable voice from
  1-20 audio samples, `FishBackend.voice_clone`) and `POST /v1/voice-design`
  (generate `n` candidate voices from a text description, `FishBackend.
  voice_design`). Neither fits `audio.speak()`'s text→single-audio-file shape
  (`voice_clone` uploads files and returns model metadata, no media file;
  `voice_design` returns several audio candidates in one response), so they're
  exposed through a new orchestrator module, `nazca.voice`
  (`clone_voice`/`design_voice`), and two new CLI commands:
  - `nazca voice-clone AUDIO... --title "My Voice" [--description] [--visibility private|unlist|public] [--tags a,b] [--dry-run]`
    — visibility defaults to `private` (Fish's own API default is `public`;
    nazca picks the safer default so a clone isn't silently published). Prints
    the created `reference_id` on success; `--dry-run` prints the request plan
    (file names + sizes only — never the raw audio bytes) to stdout.
  - `nazca voice-design INSTRUCTION [-o prefix] [--reference-text] [--language] [-n] [--speed] [--dry-run]`
    — `-o/--out` is an output filename **prefix** (default `voice_design`);
    writes each candidate's decoded preview audio to `<prefix>_<index>.mp3`.
  - New two-model registry entries, `fish-voice-clone`/`fish-voice-design`
    (routing placeholders, like `fish-tts`/`worder-tts` — empty `provider_id`,
    no default voice), each declaring exactly one of the two new ops so
    `capabilities.validate_op` rejects them for every other audio model
    (`fish-tts`, `worder-tts`, the two Atlas TTS models) and vice versa.
  - **New retry machinery:** `retry.post_multipart()` — the first
    `multipart/form-data` POST in nazca (every other backend POSTs JSON or
    receives raw bytes). Hand-builds the multipart body (random `uuid4` hex
    boundary, one part per simple form field, one part per file — `voices`
    repeats its field name once per audio sample) since the project takes no
    `requests`/`httpx` dependency, and shares the same retry/backoff loop as
    `post_json`/`post_bytes`. That required a small refactor to
    `retry._post_with_retry`: it now takes pre-encoded `data: bytes` instead of
    a `body: dict` it JSON-encoded internally, with `post_json`/`post_bytes`
    doing the `json.dumps(...).encode()` themselves before calling in — a
    behavior-preserving change (existing `retry` tests pass unchanged) that
    lets `post_multipart` share the loop without being forced through
    `json.dumps`.
  - The Fish `422` error hint (`backends/error_hints.py`) was widened from
    TTS-specific wording (`text`/`reference_id`) to cover all three endpoints
    it can now fire from.

### Fixed
- **`nazca speak` let real backend errors crash instead of printing a clean
  message.** `FishError`/`WorderError`/`AtlasError` subclass `BackendError`
  directly, not `AudioError` — but the CLI's `speak` command (and, before this
  release, the two new voice-clone/voice-design commands built the same way)
  caught only `AudioError`, so a genuine Fish/Worder/Atlas HTTP error (bad key,
  4xx, etc.) propagated as an unhandled Python traceback instead of the `❌
  ...` one-liner + clean exit `nazca image`/`nazca video` already give for the
  same class of failure. Found while wiring the new voice-clone/voice-design
  commands (issue #122 A2) on the same pattern — fixed at the source for all
  three commands by catching `BackendError` (which `AudioError` already
  subclasses) via the existing `_emit_backend_error` helper. **User-visible
  side effect:** `nazca speak`'s exit code for a backend/HTTP failure moved
  from `2` to `1`, aligning it with `nazca image`/`nazca video` (exit `2`
  stays reserved for capability-validation failures, e.g. an unsupported
  `--model`/op combination); anyone scripting against `speak`'s previous exit
  code for that specific failure class should update to `1`.

### Changed (internal — no behavior change)
- **Audio ops vocabulary spine (issue #122, phase A1):** `capabilities.AUDIO_OPS`
  now names the full 10-op audio vocabulary (`tts`, `voice_clone`, `voice_design`,
  `speech_to_speech`, `stt`, `sfx`, `music`, `dub`, `separate`, `align`) documented
  in `docs/media-modalities.md`'s "Audio out" table, instead of just `tts`.
  `audio.speak()`'s `op` is now a real parameter (default `"tts"`, unchanged for
  every existing caller) instead of being hardcoded, and both `nazca speak` and
  `audio.speak()` now run the same `validate_op` capability check `nazca
  image`/`nazca video` already had — previously `speak()`'s `op` parameter was
  unvalidated, so an unsupported op would have silently fallen back to plain
  TTS (Atlas) or been ignored outright (Worder/Fish) instead of erroring. No
  audio model declares anything but `tts` yet — this is descriptive plumbing
  (mirrors image/video's P1), not new capability, for every op *other* than
  `tts`; a raw non-audio `--model` (e.g. `nazca speak --model nano-banana`)
  now gets a clearer error message from the capability check rather than the
  resolver, same exit code. Sets up wiring real ops per provider next: Fish
  Audio's unused `voice_clone`/`voice_design` endpoints (A2), an ElevenLabs
  backend (A3, absorbs #121), an Atlas audio catalog survey (A4).

## [0.14.0] — 2026-07-30

### Added
- **Fish Audio speech backend (`fish-tts` / `fish:<reference_id>`):** new opt-in TTS
  provider (`FISH_API_KEY`) alongside Atlas and Worder as a third `nazca speak`
  option — a platform of hosted + community voice models, each selected by a
  `reference_id`. Synchronous `POST /v1/tts` whose success response is a raw
  audio stream (chunked transfer encoding), not a JSON envelope, so it is POSTed
  via a new `retry.post_bytes` helper (added alongside `retry.post_json`, sharing
  the same retry/backoff loop) instead of `retry.post_json`. The TTS quality tier
  is selected via a required `model` HTTP header (`s1` / `s2-pro` / `s2.1-pro` /
  `s2.1-pro-free`), defaulted to `s2-pro`, separate from the `reference_id` voice.
  No default voice exists — callers must pass `--voice <reference_id>` (from
  `GET /model`) or use the `fish:<reference_id>` prefix. `--format mp3|wav` is
  forwarded as the `format` body field, same as Atlas. Pricing is unverified
  against a live key, so `fish-tts` has `price_usd=None` in `models.py`
  (`--dry-run` shows the request plan, not a cost estimate). Wired into
  `nazca login` / `nazca config`.
  *Status: integrated per the published OpenAPI schema, unverified against a live
  key.*

## [0.13.2] — 2026-07-30

### Added
- **PyPI distribution (`nazca-cli`):** nazca is now published to PyPI under the
  project name `nazca-cli` (the plain `nazca` name is already taken by an
  unrelated library) — the `nazca` command, importable `nazca` module, and repo
  name are unaffected, only the PyPI project name differs. A GitHub Actions
  workflow (`.github/workflows/publish.yml`) builds and publishes on every
  `v*.*.*` tag push via a `PYPI_API_TOKEN` repo secret (trusted publishing/OIDC
  is the longer-term goal, blocked on a pending PyPI Organization approval).

## [0.13.1] — 2026-07-30

### Fixed
- **`nazca --version` reported the wrong version:** the 0.13.0 bump only updated
  `pyproject.toml`, not the hardcoded `__version__` in `src/nazca/__init__.py`
  (which the CLI's `--version` actually reads) — so a real 0.13.0 install still
  printed `0.12.0`. Both are now kept in sync.
- **README install examples pinned a stale `@v0.1.0` tag** (the very first
  release, missing every provider added since) instead of the current release;
  updated to `@v0.13.1` and added a pointer to the releases page so this doesn't
  silently go stale again.

## [0.13.0] — 2026-07-30

### Added
- **Worder speech backend (`worder-tts` / `worder:<voice_id>`):** new opt-in TTS
  provider (`WORDER_API_KEY`) alongside Atlas as a second `nazca speak` option — a
  marketplace of real, ethically-sourced human voice actors rather than a house
  model. Synchronous `POST /api/v1/generate` (no submit→poll), text supports
  direction tags (`[happy]`), pause tags (`[pause N]`), emphasis tags, and
  pronunciation overrides (`{written|spoken}`). No default voice exists — callers
  must pass `--voice <voice_id>` (from `GET /api/v1/voices`) or use the
  `worder:<voice_id>` prefix. Pricing is per-second and set per voice actor, so
  it's left unpriced in `cost.py` (`--dry-run` shows the request plan, not a cost
  estimate) rather than guessing a flat rate. Wired into `nazca login` /
  `nazca config`. *Status: integrated per the published API docs, unverified
  against a live key.*

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
