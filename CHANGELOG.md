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
  `nazca music "style prompt" [--lyrics "[Verse]\n..."] [--format mp3|wav] -o
  track.mp3`. A new `AudioRequest.lyrics` field and `audio.generate_music()` (a
  thin wrapper over `speak(..., op="music")`) carry the music-specific state.
  `$0.15/gen` is a **confirmed** price (unlike most Atlas entries, which are
  priced from marketing copy) — pulled directly from Atlas's live, public,
  no-auth `GET https://api.atlascloud.ai/api/v1/models` endpoint. The
  request/response *schema* is confirmed too, not a guess: every model in that
  same API response links its own public, no-auth OpenAPI fragment
  (`static.atlascloud.ai/model/schema/minimax-music-2.6.json`), which documents
  `model`/`prompt` (required) and `lyrics`/`is_instrumental`/`format`
  (`mp3`/`wav`/`pcm`)/`sample_rate`/`bitrate` (optional) — nazca wires
  `prompt`/`lyrics`/`format` today; the rest are real, confirmed fields with no
  CLI flag yet (a feature gap, not a schema gap).

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

- **ElevenLabs sound effects (`elevenlabs-sfx` / `nazca sfx`, issue #122 phase A3,
  second sub-phase after `tts`):** wires `sfx` — named in `AUDIO_OPS` since phase
  A1, unwired until now. `POST /v1/sound-generation` has no `voice_id` concept
  (no `--voice`, no URL path segment) — a text *description* of a sound in, raw
  audio bytes out, same synchronous shape as `elevenlabs-tts` otherwise (same
  `xi-api-key` auth, same `output_format` query-param convention). New
  `AudioRequest.duration_seconds` field (optional target length, 0.5-30s;
  ElevenLabs auto-guesses when omitted) and `audio.generate_sfx()` (a thin
  wrapper over `speak(..., op="sfx")`), mirroring how `duration_seconds` and
  `lyrics` are each op-specific fields on the shared `AudioRequest`, ignored by
  ops that don't use them. New CLI command: `nazca sfx "sound description"
  [--duration 8] [--format mp3|wav] -o effect.mp3`. Pricing is
  subscription-tier-based like `elevenlabs-tts`, so `elevenlabs-sfx` is
  likewise unpriced (`price_usd=None`).

  *Status: integrated per the published OpenAPI schema, unverified against a
  live key.*

- **ElevenLabs voice cloning (`elevenlabs-voice-clone` / `nazca voice-clone
  --model elevenlabs-voice-clone`, issue #122 phase A3, third sub-phase after
  `tts`/`sfx`):** wires `POST /v1/voices/add` (Instant Voice Clone) as a
  second `voice_clone`-capable backend alongside Fish Audio's
  `fish-voice-clone` (phase A2). `ElevenLabsBackend.voice_clone` plugs
  straight into the existing `SupportsVoiceClone` protocol; only a new
  `ModelSpec` (`models.py`), a new `Caps` entry (`capabilities.py`), and the
  backend method itself were needed there. Multipart upload via the same
  `retry.post_multipart` Fish's `voice_clone` already uses; 1+ audio sample
  files under a `files` field (ElevenLabs' name, vs Fish's `voices`) plus a
  required `name` field (`--title`) and optional `description`. Unlike Fish
  (hard caps at 20 samples/call), ElevenLabs' published OpenAPI spec
  documents no per-call sample-count limit — only a workspace-wide cap of
  500 *total* voices, which nazca can't check client-side, so only "at least
  one sample" is validated here. Fish-only concepts (`--visibility`,
  `--tags`) have no ElevenLabs equivalent — the *backend method* accepts
  them (satisfying `SupportsVoiceClone`'s uniform call shape) but never
  forwards them to the API; `nazca.voice.clone_voice()` (the orchestrator,
  which every caller actually goes through) now rejects them outright with a
  clean error for any non-Fish backend, rather than silently discarding
  values a caller would reasonably expect to take effect (see Fixed below).
  Response's `voice_id` — normalized by `clone_voice()` across both backends
  (Fish's own key is `_id`) — is what `nazca speak --model elevenlabs-tts
  --voice <voice_id>` then consumes. Pricing is subscription-tier-based like
  `elevenlabs-tts`/`elevenlabs-sfx`, so `elevenlabs-voice-clone` is likewise
  unpriced (`price_usd=None`).

  *Status: integrated per the published OpenAPI schema, unverified against a
  live key.*

- **ElevenLabs voice design (`elevenlabs-voice-design` / `nazca voice-design
  --model elevenlabs-voice-design`, issue #122 phase A3, third sub-phase after
  `tts`/`sfx`):** wires `voice_design` for a second backend — `fish-voice-design`
  already existed (phase A2); `nazca.voice.design_voice()` and its return
  contract (`{"candidates": [{"id", "audio_base64", ...}]}`, each candidate's
  base64 preview audio decoded to `audio_bytes` for the caller) needed **zero**
  changes to support it, since `ElevenLabsBackend.voice_design()` reshapes
  ElevenLabs' native response into that exact shape before returning.

  ElevenLabs splits voice creation into two real HTTP calls where Fish does it
  in one: Step 1 `POST /v1/text-to-voice/design` returns *ephemeral* preview
  candidates (`generated_voice_id` + inline `audio_base_64`, nothing saved to
  the account); Step 2 `POST /v1/text-to-voice` takes one chosen
  `generated_voice_id` and *permanently* saves it as a durable account voice.
  **Only Step 1 is wired here, deliberately.** Reasoning (see
  `backends/elevenlabs.py`'s module docstring for the full writeup): (1)
  `design_voice()`'s existing contract — return N candidates with inline
  preview audio for the caller to listen to and pick from, without persisting
  anything — is already satisfied exactly by Step 1 alone; (2) Step 2 needs a
  `voice_name` chosen *after* hearing the previews, which nazca's synchronous,
  single-invocation CLI model has no way to thread through a follow-up call
  without inventing new two-step orchestration nothing else in nazca has; (3)
  doing Step 2 automatically would silently turn `voice-design` into a command
  that creates a persistent, possibly-billed account resource, breaking parity
  with Fish's (and every other backend's) `voice-design` being a pure preview.
  Saving a chosen candidate permanently is left as an explicit fast-follow —
  its own command backed by Step 2, not a hidden side effect of this one.

  Response reshaping specifics: `generated_voice_id` -> `id`, `audio_base_64`
  -> `audio_base64` (note the underscore-before-64 rename — an easy typo to
  miss since both spellings look like plausible English at a glance); other
  preview fields (`media_type`, `duration_secs`, `language`) pass through
  unchanged as extra candidate keys. `instruction` maps to ElevenLabs' required
  `voice_description` (20-1000 chars, validated locally so a too-short
  instruction fails fast with a clear `ElevenLabsError` instead of a round-trip
  422); `reference_text` maps to the optional `text` field, or
  `auto_generate_text: true` is sent when omitted so ElevenLabs still produces
  a spoken preview. `n`/`language`/`speed` are accepted (same call signature
  `design_voice()` uses for every backend) but silently ignored for
  ElevenLabs — `/design` has no request-level knob for a candidate count,
  language, or speech rate, unlike Fish's explicit `n`/`language`/`speed` body
  fields; ElevenLabs' model simply returns however many previews it produces.
  `model_id` is pinned to `eleven_multilingual_ttv_v2` (ElevenLabs' own
  default for this endpoint, sent explicitly — same "don't rely on the
  provider's implicit default" posture as `elevenlabs-tts`'s `model_id`).
  `POST /v1/text-to-voice/design` returns a JSON envelope (not raw audio
  bytes like TTS/sfx), so this reuses `retry.post_json`, not `post_bytes`.
  `elevenlabs-voice-design` has `price_usd=None`, same unpriced posture as
  every other `elevenlabs-*` entry.

  *Status: integrated per the published OpenAPI schema and public docs,
  unverified against a live key.*

### Fixed
- **`nazca voice-clone`'s success output silently dropped the created voice's
  id when the backend was ElevenLabs.** The CLI's success-path formatting
  read Fish's response field name (`_id`) unconditionally and hard-coded the
  follow-up hint's `--model` to `fish-tts` — a real, successful
  `elevenlabs-voice-clone` run printed `✅ Voice cloned: ?` (ElevenLabs
  returns `voice_id`, not `_id`) and pointed the follow-up command at the
  wrong backend, with no error or warning. Caught independently by two
  review passes on the same PR. Fixed at the source: `nazca.voice.
  clone_voice()` now normalizes every backend's response to always include a
  `voice_id` key (Fish's `_id` is copied across), so the CLI — and any other
  caller — no longer needs to know which backend it's talking to. Also
  closes a related gap the same reviews raised: `--visibility`/`--tags` used
  to be silently accepted-then-ignored by non-Fish backends; `clone_voice()`
  now rejects them outright for any backend but Fish, rather than letting a
  caller believe they took effect.
- **`retry.post_bytes` never validated the Content-Type of a 2xx response**
  (found during `elevenlabs-sfx`'s PR review, but pre-existing — shared by
  every raw-bytes backend: Fish's `/v1/tts`, ElevenLabs' `/v1/text-to-speech`
  and `/v1/sound-generation`). An expired signed URL or partial-failure
  response can return an error body (JSON/XML/HTML) at HTTP 200, which was
  silently written straight to the output file as if it were real audio.
  Atlas's async `_poll()` already had this exact guard (phase A4) but the
  synchronous `post_bytes` path didn't; now it rejects the same known
  non-media Content-Types (`application/json`/`xml`, `text/xml`/`html`) via
  `on_http_error`, same negative-whitelist approach as Atlas.
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
