# Media generation modalities — the map

The modality of a model is **`inputs → output`**. It's the axis that decides
routing, which CLI flags are even legal, and where validation belongs. This doc
is the human-facing version of `src/nazca/capabilities.py` (the machine-readable
descriptor). Keep them in sync — a test asserts every model has a `Caps` entry.

## Operation vocabulary

A closed set. Adding a modality = a new entry here + a body-builder on the
backends that support it, not a new ad-hoc code path.

### Image out
| op | inputs → output | meaning |
|---|---|---|
| `t2i` | text → image | text-to-image |
| `i2i` | text + ref[1] → image | restyle / edit from one reference |
| `compose` | text + ref[2..N] → image | multi-subject blend |
| `inpaint` | source + mask + text → image | edit a masked region |
| `outpaint` | source (+text) → image | extend the canvas |
| `upscale` | source → image | enhance / increase resolution |
| `bg_remove` | source → image+alpha | cutout / transparent background |

### Video out
| op | inputs → output | meaning |
|---|---|---|
| `t2v` | text → video | text-to-video |
| `i2v` | text + start → video | animate from a start frame |
| `keyframe` | text + start + end → video | first-last frame interpolation |
| `v2v` | source video (+text) → video | restyle / motion-transfer |
| `reframe` | source video + aspect → video | change aspect ratio |
| `extend` | source video → video | lengthen a clip |

### Audio out
| op | inputs → output | meaning |
|---|---|---|
| `tts` | text + voice → audio | text-to-speech — **the only op nazca drives today** |
| `voice_clone` | audio sample(s) → voice_id | derive a reusable voice from a recording |
| `voice_design` | text description → 3× voice_id | generate new voice candidates from a prompt |
| `speech_to_speech` | source audio + voice_id → audio | voice changer — recast an existing recording in another voice |
| `stt` | audio → text | transcription |
| `sfx` | text → audio | sound effect / Foley generation |
| `music` | text → audio | music generation |
| `dub` | video/audio + target language → audio/video | cross-language dubbing |
| `separate` | audio → stems | split into vocals/instruments/etc |
| `align` | audio + text → timed transcript | forced alignment (subtitle/caption timing) |

`music`, `dub` are named here as vocabulary but **out of scope for nazca today** — no
model wires them yet. `voice_clone` / `voice_design` / `speech_to_speech` / `stt` /
`sfx` / `separate` / `align` are newly named (were previously undocumented); none are
wired either. This is deliberately the full vocabulary across *all* audio providers
nazca could reach, not just what's implemented — see the capability matrix below for
which provider's public API actually offers which op.

## Ref roles (P1 — descriptive)

A second axis on `i2i`/`compose`: not just *that* a reference was passed, but *what it
is*. Today refs are untyped/positional (count alone picks `i2i` vs `compose`, and the
backend blends them). `REF_ROLES` is the closed vocabulary that will change that:

| role | meaning |
|---|---|
| `ref` | generic / untyped — **current behavior**, the default for a bare `--ref x.png` |
| `subject` | the primary thing to keep or edit (source content) |
| `style` | match this aesthetic / look, not its content |
| `identity` | this face / character / wordmark — preserve identity |

`Caps.ref_roles` declares which roles each model accepts: every ref-capable model takes
the generic `ref`; the multi-semantic-ref models (nano-banana family, `seedream`,
`gpt-image-2`) additionally accept the typed roles. Single-ref FLUX is generic-only.

**CLI surface (live):** `--ref PATH:role`, repeatable — e.g.
`--ref hero.png:subject --ref look.png:style --ref face.png:identity`. A bare `--ref x.png`
is untyped (role `ref`) and behaves exactly as before. Unknown roles, and typed roles on a
model that doesn't accept them, are rejected up front.

**How a role changes output:** no backend exposes a per-ref role field, so roles steer the
model through the **prompt** — `role_annotation()` appends an ordered legend ("image 1 is
the subject…; image 2 is a style reference…") to the prompt before dispatch. Untyped refs
add nothing, so the prompt sent is byte-identical to today. This is provider-agnostic (every
image backend forwards the prompt). Backends do **not** yet treat the images differently at
the API level — that's a later, per-provider step where native role fields exist.

## Models today (P1 — descriptive, what nazca drives now)

### Image
| shorthand | backend | ops | notes |
|---|---|---|---|
| `nano-banana` | vertex/gemini | t2i, i2i, compose | 2.5-flash-image; ref count unpinned |
| `nano-banana-2` | vertex/gemini | t2i, i2i, compose | 3.1-flash-image |
| `nano-banana-2-lite` | vertex/gemini | t2i, i2i | 3.1-flash-lite-image; **single ref only**, no compose, fastest/cheapest tier |
| `nano-banana-pro` | vertex/gemini | t2i, i2i, compose | 3-pro-image; **up to 14 refs**, legible text |
| `imagen-4-fast` | vertex/imagen | t2i | **t2i only** — rejects refs |
| `imagen-4` | vertex/imagen | t2i | t2i only |
| `imagen-3` | vertex/imagen | t2i | t2i only |
| `flux-schnell` | fal | t2i, i2i | **single ref only**; fal id unverified |
| `flux-2-dev` | fal | t2i, i2i | single ref only; fal id unverified |
| `seedream` | modelark | t2i, i2i, compose | up to 14 refs; needs BytePlus activation; `group` (N/call) not wired |
| `gpt-image-2` | openai | t2i, i2i, compose | **≤5 refs** via `/images/edits`; legible text/ads; `--quality` lever; token-billed; slow (~30–105s) |
| `upscale` | fal | upscale | clarity-upscaler, `--scale 1-4`, $0.03/MP (verified id) |
| `rmbg` | fal | bg_remove | birefnet/v2 → transparent PNG, free compute (verified id) |
| `inpaint` | fal | inpaint | flux-pro/v1/fill, `--mask` (white=edit) + prompt, $0.05/MP (verified id) |
| `outpaint` | fal | outpaint | flux-2-pro/outpaint, `--expand` px/side, no prompt/mask (verified id) |

### Video
| shorthand | backend | ops | notes |
|---|---|---|---|
| `veo-3.1-lite` | vertex | t2v, i2v, keyframe | `--start` optional (t2v) / one frame (i2v) / two (keyframe) |
| `veo-3.1-fast` | vertex | t2v, i2v, keyframe | |
| `veo-3.1` | vertex | t2v, i2v, keyframe | |
| `omni-flash` | vertex | t2v, i2v, ref2v, v2v | gemini-omni-flash-preview; `:generateContent` not `:predictLongRunning` — synchronous, no poll; fixed ~10s/720p+audio, ignores duration/resolution/audio/aspect flags (Vertex rejects `videoConfig.aspectRatio`); ref2v verified live to 2 imgs (`--ref`, max_refs=6 per Google's docs example, untested beyond 2); v2v takes a LOCAL file via `--v2v SOURCE` (opposite of fal's URL convention — `edit_video` branches on `spec.api == "omni"`); all verified against live Vertex calls 2026-06-30 |
| `seedance-2-fast` | fal | i2v | fal id unverified |
| `wan-2.6` | fal | **t2v** | fal id is `.../text-to-video`; reachable now (no `--start`) |
| `seedance-pro` | modelark | i2v | needs BytePlus activation |
| `seedance-lite` | modelark | i2v | needs BytePlus activation |
| `reframe` | fal | reframe | luma ray-2/reframe; SOURCE = **video URL**, `--aspect` target (verified id+field) |
| `v2v` | fal | v2v | wan-vace-apps/video-edit; SOURCE video URL + prompt (`video_url` field unverified) |
| `extend` | fal | extend | pixverse/extend; SOURCE video URL + prompt, `--duration 5|8` (`video_url` field unverified) |

### Audio
| shorthand | backend | ops | notes |
|---|---|---|---|
| `atlas-tts-grok` | atlas | tts | xai/tts-v1; 20 langs, 80+ voices; $0.015/1K chars |
| `atlas-tts-elevenlabs-v3` | atlas | tts | ElevenLabs `eleven_v3` proxied through Atlas — fixed model choice, no voice_settings/streaming/cloning; $0.10/1K chars |
| `worder-tts` | worder | tts | marketplace of real voice actors; direction/pause/emphasis/pronunciation tags; Whisper-verified ≥90% similarity or no charge; `--voice` required, per-voice pricing |
| `fish-tts` | fish | tts | `reference_id`-selected voice, `model` header picks quality tier (`s1`/`s2-pro`/`s2.1-pro`/`s2.1-pro-free`); `--voice` required; pricing unverified |
| `fish-voice-clone` | fish | voice_clone | `POST /model` (multipart); `--title` + 1-20 audio samples → `reference_id`; visibility defaults to **private** (Fish's own API default is public); pricing unverified |
| `fish-voice-design` | fish | voice_design | `POST /v1/voice-design`; text instruction → `n` (default 2) candidate voices with base64-encoded preview audio; pricing unverified |
| `elevenlabs-tts` | elevenlabs | tts | `POST /v1/text-to-speech/{voice_id}`; voice_id is a URL path param (not a body field); `eleven_multilingual_v2` by default; `xi-api-key` auth (not Bearer); `output_format` is a query param, not a body field; `--voice` required; pricing subscription-tier-based, unpriced here |
| `atlas-music-minimax` | atlas | music | `minimax/music-2.6`; style prompt + optional `--lyrics` (`[Verse]`/`[Chorus]` structure) + `--format`; $0.15/gen and request/response schema both confirmed via Atlas's live model-list API (each model links its own public OpenAPI fragment) — `is_instrumental`/`sample_rate`/`bitrate` are real confirmed fields with no CLI flag yet |
| `elevenlabs-sfx` | elevenlabs | sfx | `POST /v1/sound-generation`; no `voice_id` — text sound description in, raw audio out; optional `--duration` (0.5-30s, auto-guessed if omitted); pricing subscription-tier-based, unpriced here |
| `elevenlabs-voice-clone` | elevenlabs | voice_clone | `POST /v1/voices/add` (multipart); `--title` + 1+ audio samples → `voice_id`; no per-call sample cap in ElevenLabs' spec (only a workspace-wide 500-total-voices cap); `--visibility`/`--tags` (Fish-only concepts) rejected by `nazca.voice.clone_voice()` for this backend rather than silently ignored; pricing subscription-tier-based, unpriced here |
| `elevenlabs-voice-design` | elevenlabs | voice_design | `POST /v1/text-to-voice/design` — Step 1 of ElevenLabs' two-step voice-creation flow only (ephemeral previews; Step 2's permanent account-voice save, `POST /v1/text-to-voice`, is not wired); response reshaped to Fish's `{"candidates": [{"id", "audio_base64", ...}]}` shape so `nazca.voice.design_voice()` needs no changes; `n`/`language`/`speed` (no ElevenLabs equivalent) rejected by `nazca.voice.design_voice()` for this backend rather than silently ignored; `--reference-text` IS honored (maps to `text`); instruction must be 20-1000 chars; pricing subscription-tier-based, unpriced here |
| `elevenlabs-speech-to-speech` | elevenlabs | speech_to_speech | `POST /v1/speech-to-speech/{voice_id}`; **multipart request** — local source audio FILE + `model_id` (defaults `eleven_english_sts_v2`) in, raw audio out (still not multipart — same shape as tts/sfx); `voice_id` is a URL path param; `output_format` is a query param; `--voice` required; pricing subscription-tier-based, unpriced here |
| `elevenlabs-stt` | elevenlabs | stt | `POST /v1/speech-to-text` (multipart); the first wired op whose real output is JSON, not audio — local audio file in, `{text, words: [{text,start,end,type,...}], language_code, ...}` out; `model_id` hardcoded to `scribe_v2`; optional `--language` (auto-detected if omitted); pricing subscription-tier-based, unpriced here |
| `elevenlabs-align` | elevenlabs | align | `POST /v1/forced-alignment` (multipart); LOCAL audio file + text transcript in, character/word timestamps (+ confidence `loss`) out as JSON, not audio bytes; no `voice_id`/model concept; endpoint/schema verified live against ElevenLabs' own API reference; pricing subscription-tier-based, unpriced here |

Every wired audio model does `tts` only, except the seven voice_clone/voice_design/
speech_to_speech/stt/align entries (Fish Audio's pair, issue #122 phase A2, plus
ElevenLabs' quintet, phase A3), `atlas-music-minimax` (phase A4, `music`), and
`elevenlabs-sfx` (phase A3, `sfx`). No `dub` or `separate` model is wired
anywhere in nazca today.

### Audio capability matrix (what each provider's *own* API offers — not what's wired)

Researched against each provider's public developer docs on 2026-07-30. This is
deliberately broader than "Models today" above — it's the map to plan future backend
work against, the audio equivalent of the fal/ModelArk/OpenAI image-ops survey that
led to the modify-op backends.

| provider | tts | voice_clone | voice_design | speech_to_speech | stt | sfx | music | dub | separate | align |
|---|---|---|---|---|---|---|---|---|---|---|
| **Atlas Cloud** *(surveyed via a live, no-auth call to `GET api.atlascloud.ai/api/v1/models`, 2026-07-30 — issue [#122 A4](https://github.com/Mysios-Labs-inc/nazca/issues/122))* | ✅ **8 real TTS models**, 2 wired (`atlas-tts-grok`=`xai/tts-v1`, `atlas-tts-elevenlabs-v3`=`elevenlabs/v3/text-to-speech`), 6 unwired (`bytedance/seed-audio-1.0`, 3× `google/gemini-*-tts`, 2× `minimax/speech-2.6-*` — deferred, low marginal value given 4 direct TTS providers already exist) | ❌ not offered by any Atlas model found | ❌ not offered by any Atlas model found | ❌ not offered by any Atlas model found (the 4 "AUDIO-TO-VIDEO" models are avatar/lip-sync — video output, not voice-changed audio; see `Models today` → Video) | ✅ 2 models (`bytedance/seed-asr-2.0`, `xai/stt-v1`), deliberately unwired — analysis, not generation, per the standing #121 scoping decision | ❌ not offered by any Atlas model found | ✅ **9 real models, 1 wired**: `minimax/music-2.6` wired as `atlas-music-minimax` ($0.15/gen, confirmed price); 8× `suno/chirp-*` variants deferred as a batch fast-follow (near-duplicates, not wired individually this pass) | ❌ not offered by any Atlas model found | ❌ not offered by any Atlas model found | ❌ not offered by any Atlas model found |
| **Worder** | ✅ (rich prosody control; no other audio capability offered — a TTS-only marketplace by design) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Fish Audio** | ✅ REST + WebSocket streaming | ✅ `POST /model` (instant clone or persistent trained model) | ✅ `POST /v1/voice-design` | ⚠️ marketing-only — "voice transformation" is a web-app feature, not in the public API/OpenAPI index | ✅ `POST /v1/speech-to-text`, per-segment timestamps | ⚠️ marketing-only — not in the public API index | ⚠️ marketing-only — not in the public API index | ❌ | ⚠️ marketing-only ("audio separation") — not in the public API index | ❌ |
| **ElevenLabs** *(`tts` + `sfx` + `voice_clone` + `voice_design` + `speech_to_speech` + `stt` + `align` integrated — issue [#122 A3](https://github.com/Mysios-Labs-inc/nazca/issues/122), absorbs [#121](https://github.com/Mysios-Labs-inc/nazca/issues/121); the full vocabulary except `dub`)* | ✅ **wired** (`elevenlabs-tts`); 3 model tiers exist (`eleven_v3`/`eleven_multilingual_v2`/`eleven_flash_v2_5`) but only the default (`eleven_multilingual_v2`) is used — no `--model-id` flag yet | ✅ **wired** (`elevenlabs-voice-clone`); `POST /v1/voices/add` (Instant Voice Clone) — professional cloning (a separate, longer-form workflow) not wired | ✅ **wired** (`elevenlabs-voice-design`); `POST /v1/text-to-voice/design` — Step 1 of a two-step flow only (preview generation); Step 2 (`POST /v1/text-to-voice`, permanently saving a chosen preview as an account voice) deliberately not wired, see Audio roadmap below | ✅ **wired** (`elevenlabs-speech-to-speech`); `POST /v1/speech-to-speech/{voice_id}` — multipart request, raw-audio response; `model_id` defaults `eleven_english_sts_v2`; `voice_settings`/`seed`/`remove_background_noise`/`file_format` real but not exposed via CLI yet | ✅ **wired** (`elevenlabs-stt`); `POST /v1/speech-to-text`, `model_id` enum `scribe_v2`/`scribe_v1` (nazca hardcodes `scribe_v2`), multipart `file` upload, JSON response with word-level timestamps + language detection — diarization/entity-redaction/webhook-async are real, unwired fields | ✅ **wired** (`elevenlabs-sfx`); `POST /v1/sound-generation` — confirmed via ElevenLabs' live `openapi.json` (the `/v1/text-to-sound-effects/convert` path assumed in an earlier survey pass was wrong) | ✅ Eleven Music (not wired) | 🚧 announced, **API not live yet** per their own docs | ❌ not offered | ✅ **wired** (`elevenlabs-align`); `POST /v1/forced-alignment` — request fields and response schema verified live against ElevenLabs' own API reference (`elevenlabs.io/docs/api-reference/forced-alignment/create`) |

**Reading this table:** ⚠️ rows are claims from marketing copy that don't appear in
the provider's own API reference/OpenAPI index — treat as "web-app only, unverified
as a callable endpoint" until checked against the actual OpenAPI schema, the same
posture nazca already takes for unverified fal/ModelArk ids. Atlas's row (unlike the
others) comes from a live API response, not docs prose — `GET
api.atlascloud.ai/api/v1/models` needs no auth and returns all 446 models with a
`categories` tag per model, so "❌ not offered" for Atlas means "no model in the live
catalog carries that category tag", a stronger claim than the ❓/unconfirmed posture
this row used to have (see #122 A4). One caveat: Atlas's own `TEXT-TO-SPEECH`
category tag is overloaded — it conflates real speech synthesis with music
generation (the 8 `suno/chirp-*` + `minimax/music-2.6` models are tagged
`TEXT-TO-SPEECH` on Atlas's side despite being song/music generators, not TTS) — the
table above reclassifies them into nazca's `music` column rather than trusting
Atlas's tag literally.

**Where this points:** ElevenLabs is the only provider with a fully public,
documented API across nearly the whole audio ops vocabulary (missing only `dub`,
not live yet anywhere, and `separate`) — `tts`/`sfx`/`voice_clone`/`voice_design`/
`speech_to_speech`/`stt`/`align` are all wired now (A3 complete). Fish Audio is second — genuinely has `voice_clone`/
`voice_design`/`stt` as real endpoints; nazca wires the first two through Fish
(A2), but `stt` was wired through ElevenLabs instead, this phase — Fish's own
`stt` endpoint remains unused. Worder is intentionally narrow. Atlas turned
out to be TTS-and-music (not TTS-only as originally guessed) — `music` is now wired
(A4, this pass) via `minimax/music-2.6`; the remaining 6 unwired TTS variants and 8
unwired Suno music variants are documented, explicit deferrals, not gaps nobody
looked at.

## Mismatches (1 & 2 fixed in P2)

1. ✅ **`nazca video` no longer forces `--start`.** Omit it for pure `t2v` (wan-2.6,
   and Veo's start-less body); one frame → `i2v`; two → `keyframe`. The op is
   inferred and validated against the model.
2. ✅ **Imagen + `--ref` is rejected up front**, not mid-dispatch: the CLI infers
   the op from flags and checks `op ∈ caps.ops`, erroring with a suggested model.
3. ⬜ **Seedream `group` mode** (1 call → up to 15 related images) is a real distinct
   axis and is still unwired.

**P3 (done): all four image modify ops** wired on fal via the positional `SOURCE`
slot — `upscale` (clarity-upscaler), `bg_remove` (birefnet/v2), `inpaint`
(flux-pro/v1/fill, `--mask` + prompt) and `outpaint` (flux-2-pro/outpaint,
`--expand`). Remaining: video `v2v`/`reframe`/`extend` (P4).

## CLI surface (decided: infer op from flags)

The command stays `nazca image` / `nazca video`; the op is inferred from the flags
you pass, then validated against the model's `ops`. A positional `SOURCE` is the
image/video being *modified* (inpaint/outpaint/upscale/bg_remove, v2v/reframe/
extend), kept distinct from `--ref` (style/subject references). `--prompt` becomes
optional for ops that don't need it (upscale, bg_remove, reframe).

```
# image
nazca image -p "..."                       # t2i
nazca image -p "..." --ref a.png           # i2i
nazca image -p "..." --ref a.png --ref b.png   # compose
nazca image SOURCE --mask m.png -p "..."   # inpaint
nazca image SOURCE --upscale               # upscale (no prompt)
# video
nazca video -p "..."                       # t2v
nazca video -p "..." --start s.png         # i2v
nazca video -p "..." --start s.png --end e.png  # keyframe
nazca video SOURCE -p "restyle ..."        # v2v
```

## Roadmap

- ✅ **P1** — `Caps` descriptor + this doc; encode existing models; `nazca models`
  shows ops. No behavior change.
- ✅ **P2** — derive op from flags + validate against `CAPS`; make `--start`
  optional (unblocks `t2v`); reject imagen+ref up front. Fixes mismatches #1, #2.
- ✅ **P3** — all four image modify ops via the `SOURCE` slot: `upscale`,
  `bg_remove`, `inpaint` (`--mask` + prompt), `outpaint` (`--expand`) — fal, all ids
  verified.
- ✅ **P4** — all three video-edit ops via a positional `SOURCE` (a **video URL** —
  fal needs a URL, not an inlined data-URI; `gs://` unsupported): `reframe` (luma
  ray-2, verified field), `v2v` (wan-vace-apps/video-edit) and `extend`
  (pixverse/extend). For v2v/extend the `video_url` input field is fal's
  convention but **UNVERIFIED live** — dry-run safe; verify with a real call
  before spend (same posture as the existing fal video ids). Local-file SOURCE
  (→ fal-storage upload) is the one remaining follow-up.

## Audio roadmap (issue [#122](https://github.com/Mysios-Labs-inc/nazca/issues/122))

Audio didn't go through P1–P4 the way image/video did — `AudioRequest.op` was
hardcoded to `"tts"` and the ops vocabulary named only `tts` until now. Same
sequencing as image/video: name the vocabulary + widen the spine first (A1),
*then* wire it per provider (A2+) — not "add a provider" in isolation.

- ✅ **A1** — `AUDIO_OPS` names the full 10-op vocabulary (the "Audio out" table
  above); `audio.speak()`'s `op` is a real parameter (default `"tts"`, unchanged
  for every existing caller) instead of hardcoded; `nazca speak`'s CLI now runs
  the same `validate_op` check image/video already had. No model declares
  anything but `tts` yet — this phase is descriptive plumbing, not new
  capability, exactly like image/video's P1.
- ✅ **A2** — Fish Audio: wired `voice_clone` (`POST /model`, multipart via the
  new `retry.post_multipart`) and `voice_design` (`POST /v1/voice-design`) as
  `FishBackend.voice_clone`/`.voice_design`, dispatched through the new
  `nazca.voice` orchestrator (`clone_voice`/`design_voice`) and the `nazca
  voice-clone`/`nazca voice-design` CLI commands — not through `audio.speak()`/
  `AudioRequest`, since neither fits that text→single-audio-file shape.
- ✅ **A3** — ElevenLabs backend (absorbs issue #121), now complete: ✅ `tts`
  wired (`elevenlabs-tts` / `elevenlabs:<voice_id>` — `xi-api-key` auth,
  voice_id-in-URL, output_format-as-query-param); ✅ `sfx` wired
  (`elevenlabs-sfx` — `POST /v1/sound-generation`, no `voice_id`, new
  `AudioRequest.duration_seconds` field, `audio.generate_sfx()` wrapper); ✅
  `voice_clone` wired (`elevenlabs-voice-clone` — `POST /v1/voices/add`,
  multipart via the same `retry.post_multipart` Fish's A2 `voice_clone`
  uses; plugs into the existing `SupportsVoiceClone` protocol with only a new
  `ModelSpec`/`Caps` entry plus the backend method — `nazca.voice.
  clone_voice()` itself later gained a small normalization/validation layer,
  see Fixed below); ✅ `voice_design` wired (`elevenlabs-voice-design` —
  `POST /v1/text-to-voice/design`, Step 1 of ElevenLabs' two-step
  voice-creation flow only; response reshaped to Fish's exact
  `candidates`/`audio_base64` shape so `nazca.voice.design_voice()` — the
  orchestrator Fish's A2 version established — needed zero changes;
  ElevenLabs' Step 2, permanently saving a chosen preview as a durable
  account voice via `POST /v1/text-to-voice`, is deliberately NOT wired — see
  `backends/elevenlabs.py`'s module docstring for the full reasoning); ✅
  `speech_to_speech` wired (`elevenlabs-speech-to-speech` — `POST
  /v1/speech-to-speech/{voice_id}`, multipart request/raw-audio response, new
  `request.SpeechToSpeechRequest` dataclass + `backends.base.
  SupportsSpeechToSpeech` protocol + `nazca.voice.speech_to_speech()`
  orchestrator + new `retry.post_multipart_bytes` helper — genuinely
  different shape from every prior op: local audio FILE in, not text); ✅
  `stt` wired (`elevenlabs-stt` — `POST /v1/speech-to-text`, multipart via
  `retry.post_multipart`, the same helper Fish's `voice_clone` introduced in
  A2; a new `TranscriptionRequest` dataclass, `ElevenLabsBackend.run_stt`, the
  new `SupportsStt` protocol, and a new `nazca.transcribe` orchestrator +
  `nazca transcribe SOURCE -o out.json` CLI command — genuinely a different
  shape from `speak()`'s text-in/audio-out, the first op in the audio
  modality whose real output is JSON, not media bytes; `media.write_result`
  was generalized minimally to write JSON for a non-bytes real-run result
  instead of only handling the dry-run-plan case, with zero behavior change
  for every existing bytes-producing caller); ✅ `align` wired
  (`elevenlabs-align` — `POST /v1/forced-alignment`, multipart; LOCAL audio
  file + transcript in, JSON word/character timestamps out; new
  `backends.base.SupportsAlign` protocol and `align.py` orchestrator module —
  same different-shape reasoning as `voice_clone`/`voice_design`; new `nazca
  align SOURCE (--text|--text-file) -o out.json` CLI command). Only `dub`
  remains named-but-unwired in the whole audio ops
  vocabulary, with no ElevenLabs endpoint live yet to wire it against.
  **Correction, twice over:** `music` (A4) and `sfx` both turned out to fit
  `speak()`'s text→single-audio-file shape directly, same as TTS — the
  original "no existing `speak`-shaped input to reuse" framing was wrong for
  both; `voice_design`/`speech_to_speech`/`stt`/`align` (like Fish's A2
  `voice_clone`/`voice_design`) each needed new plumbing outside
  `speak()`/`AudioRequest` — none of their inputs/outputs fit that
  text-in/single-audio-file-out shape.
- ✅ **A4** — surveyed Atlas Cloud's full audio catalog via a live, no-auth
  `GET api.atlascloud.ai/api/v1/models` call (446 models total): 17
  `TEXT-TO-SPEECH`-tagged (8 real TTS — 2 wired, 6 deferred; 9 actually
  music, mistagged — 1 wired as `atlas-music-minimax`, 8 Suno variants
  deferred as a batch fast-follow), 2 `SPEECH-TO-TEXT` (deliberately unwired,
  out of scope per #121), 4 `AUDIO-TO-VIDEO` (avatar/lip-sync, 3 already
  wired as video models, 1 unwired `veed/fabric-1.0/fast` variant noted but
  not built). Wired `music` — named in `AUDIO_OPS` since A1, unimplemented
  until now — via `minimax/music-2.6` ($0.15/gen, a confirmed price) and the
  new `nazca music` command. See the capability matrix above for the full
  per-model breakdown.
- **A5** — Worder stays `tts`-only; a voice-actor marketplace by design, not a
  gap to fill.
