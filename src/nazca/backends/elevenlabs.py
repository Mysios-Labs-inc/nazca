"""ElevenLabs backend — text-to-speech, sound effects, voice cloning/design,
and speech-to-speech voice conversion via ElevenLabs' own API (issue #122
phase A3).

Today ElevenLabs is only reachable indirectly through one Atlas-proxied model
(`atlas-tts-elevenlabs-v3`), which hides ElevenLabs' real model catalog,
`voice_settings`, and output-format control. This module adds ElevenLabs as a
fourth *direct* speech provider, parallel to Worder/Fish. TTS, sound effects
(`sfx`), voice cloning (`voice_clone`), voice design (`voice_design`), and
speech-to-speech voice conversion (`speech_to_speech`) are wired;
dubbing/etc are still a later follow-up per `docs/media-modalities.md`'s
"Audio roadmap" (A3 sub-phases). This also absorbs issue #121 (a full-
integration proposal).

    POST /v1/text-to-speech/{voice_id}?output_format=...    -> raw audio bytes
    POST /v1/sound-generation?output_format=...              -> raw audio bytes
    GET  /v2/voices                                          -> {"voices": [...], "next_page_token", ...}
    POST /v1/voices/add                                       -> {"voice_id", "requires_verification"} (voice_clone)
    POST /v1/text-to-voice/design                             -> {"previews": [...], "text": ...} (voice_design)
    POST /v1/speech-to-speech/{voice_id}?output_format=...    -> raw audio bytes (multipart request)

Three structural differences from every other backend in this codebase worth
flagging explicitly, since a future maintainer would reasonably assume this
backend follows the same shape as Fish/Worder/Atlas/OpenAI (it doesn't):

1. **Auth header is `xi-api-key: <key>`, NOT `Authorization: Bearer <key>`.**
   Every other backend here (Fish, Worder, Atlas, OpenAI) uses Bearer auth;
   ElevenLabs does not. Get this wrong and every request 401s.
2. **(TTS and speech_to_speech) the voice is a URL *path* parameter**, not a
   body field — unlike Fish's `reference_id` body field or Worder's
   `voice_id` body field. `tts_endpoint`/`speech_to_speech_endpoint`
   therefore take `voice_id` and bake it into the URL. `sfx`/`voice_clone`/
   `voice_design` have no voice concept at all — their endpoints take no id.
3. **`output_format` is a query-string parameter**, not a body field — unlike
   Fish/Atlas where format is inside the JSON body. nazca's `--format mp3|wav`
   maps to ElevenLabs' enum: `"mp3"` -> `"mp3_44100_128"` (ElevenLabs' own
   default), `"wav"` -> `"wav_44100"`. Omitted entirely when
   `req.output_format` is falsy (let ElevenLabs use its own default).

Like Fish/Worder, there is no single default voice every account is
guaranteed to have, so `--voice <voice_id>` is required (no default
`provider_id`) — look one up via `GET /v2/voices` (the modern, paginated
listing endpoint; the older `GET /v1/voices` also exists but ElevenLabs' own
docs say it "stops working once the workspace exceeds 500 voices", so `/v2`
is preferred here for `voices_endpoint()`).

Request body is intentionally minimal: `{"text": ..., "model_id": ...}`.
`model_id` defaults to `"eleven_multilingual_v2"` — ElevenLabs' own default,
which nazca reuses as-is (unlike Fish, where nazca overrode Fish's own TTS
default). Other real model ids exist (`eleven_v3`, `eleven_flash_v2_5`) but
this pass does NOT expose a `--model-id`-style CLI flag for them — the
default is hardcoded, not user-configurable today, same posture as Fish's
`FISH_DEFAULT_MODEL` (documented limitation / fast-follow). `voice_settings`
is also not exposed via CLI this pass — omitted from the body entirely so
ElevenLabs applies its own defaults (stability 0.5, similarity_boost 0.75,
style 0, use_speaker_boost true, speed 1).

Response is raw audio bytes (`audio/mpeg` typically, treated generically),
NOT JSON — same shape as Fish's `/v1/tts` — so `retry.post_bytes` is used,
not `retry.post_json`.

`sfx` (`POST /v1/sound-generation`) has no `voice_id` path segment at all —
it's `{"text": ...}` (+ optional `duration_seconds`, 0.5-30s, omitted to let
ElevenLabs auto-guess the duration) posted straight to a fixed URL, with
`output_format` as the same kind of query-string param as TTS. `text` here is
a sound description ("glass breaking on concrete"), not speech. `prompt_influence`
and `loop` (the latter model-id-gated) exist in ElevenLabs' schema but aren't
exposed via CLI this pass, same "don't expose every knob" posture as TTS's
`voice_settings`.

`voice_design` (issue #122 phase A3, `POST /v1/text-to-voice/design`) generates
preview candidates from a text description — the same job as Fish's
`voice_design`, but ElevenLabs splits voice creation into two real HTTP calls:
Step 1 `POST /v1/text-to-voice/design` returns *ephemeral* preview candidates
(a `generated_voice_id` + inline `audio_base_64` per candidate, nothing saved
to the account yet); Step 2 `POST /v1/text-to-voice` takes one chosen
`generated_voice_id` and *permanently saves* it as an actual account voice
(returning a durable `voice_id` usable with `nazca speak --voice`). This
backend implements Step 1 only, deliberately NOT Step 2, for three reasons:
(1) `nazca.voice.design_voice()`'s existing contract (established by Fish) is
"return N *candidates* with inline preview audio for the caller to listen to
and pick from" — it does not create/persist anything account-side, and Step 1
alone already satisfies that contract exactly; (2) Step 2 needs a `voice_name`
chosen *after* hearing the previews (an interactive step nazca's synchronous,
single dry-run/live CLI invocation model has no way to insert output into a
follow-up call) — plumbing it in would mean either an unwanted new required
flag guessed up front, or a genuinely new two-call orchestration nazca.voice
doesn't have for anything today; (3) it would silently make `voice-design`
create a persistent, possibly billed, account resource where every other
nazca command (Fish's especially) treats `voice-design` as a pure preview.
Saving a chosen candidate permanently is intentionally left as a fast-follow
(would need its own `nazca voice-save <generated_voice_id> --name ...`-shaped
command, backed by Step 2, not a hidden side effect of `voice-design`). Since
`generated_voice_id` previews expire, a human reading this: don't be
surprised a preview candidate can't be reused hours later — that is
ElevenLabs' design, not a nazca bug.

Response reshaping: ElevenLabs' `{"previews": [{"generated_voice_id",
"audio_base_64", "media_type", "duration_secs", "language"}, ...], "text":
...}` is translated in `voice_design()` into Fish's exact candidate shape —
`{"candidates": [{"id": ..., "audio_base64": ..., ...other preview fields}]}`
— *before* returning, so `nazca.voice.design_voice()` (which expects
Fish's `candidates[].audio_base64`/`.id` field names verbatim, per its own
docstring) needs zero changes to support this backend. Note the base64 field
name itself differs one-character-wise between providers: ElevenLabs'
`audio_base_64` (underscore before 64) is renamed to Fish's `audio_base64` on
the way through — an easy typo to miss since both spellings are "valid
English" at a glance.

Unlike Fish's `n` (1-4, an explicit request-body field), ElevenLabs' `/design`
endpoint does not take a candidate count — it returns however many previews
its own model produces for the given `voice_description`/`model_id`/
`guidance_scale`, so this backend does not invent a fake `n` to plumb through
the query-string or body; the caller finds out how many previews came back
from `len(result["candidates"])`, exactly like a Fish response might return
fewer than the requested `n`.

Error responses (verified against ElevenLabs' published docs, 2026-07-30):
401 covers both an invalid/missing key AND (confusingly) insufficient
credits/quota — ElevenLabs returns HTTP 401 with a body `status` of
`"quota_exceeded"` for the latter, NOT HTTP 402 as one might guess from other
providers' conventions (verified against ElevenLabs' own error-messages docs
and third-party integration write-ups; nazca does not special-case the body
here beyond the existing `error_hints` substring match). 429 is the genuine
rate-limit signal (`too_many_concurrent_requests` / `system_busy` in the
body) — this is what `ElevenLabsRateLimitError` corresponds to, matching
`retry.py`'s `RETRYABLE_STATUS = {429, 503}`. 422 is a validation error with
body shape `{"detail": [{"loc": [...], "msg": ..., "type": ...}]}` — an
object with a `detail` *list*, different from Fish's bare *list* body.

`voice_clone` (issue #122 phase A3, `POST /v1/voices/add`) is the closest 1:1
precedent to `FishBackend.voice_clone` (issue #122 A2) — same validate-then-
dry-run-then-multipart shape, reusing `retry.post_multipart` exactly like
Fish's `POST /model` does. It differs from Fish's version in the request
field names ElevenLabs itself defines: `name` (not `title`), no `type`/
`train_mode` (ElevenLabs has no train-mode choice), and no per-call sample
count cap in ElevenLabs' published OpenAPI spec (Fish caps at 20/call;
ElevenLabs' only documented limit is a workspace-wide 500 *total* voices,
which nazca cannot check client-side). `visibility`/`tags` are Fish-only
concepts with no ElevenLabs equivalent — `voice_clone` below accepts them
(so it satisfies the exact call shape `nazca.voice.clone_voice()` uses
uniformly for every backend) but never forwards them to the API.

`speech-to-speech` documents the same 422 shape (its own OpenAPI fragment
names it `HTTPValidationError`) and no other status beyond 200/422 — so it
inherits the same 401/429 posture as tts/sfx by convention, not by a
separately-published doc page.

`speech_to_speech` (`POST /v1/speech-to-speech/{voice_id}`, issue #122 phase
A3) is the one ElevenLabs op wired so far whose *request* is
`multipart/form-data`, not JSON — `req.source_audio_path` (a local file, read
by the backend itself, mirroring `FishBackend.voice_clone`'s file-handling
precedent) is the required `audio` field; `model_id` defaults to ElevenLabs'
own `eleven_english_sts_v2`. `voice_settings` (cloning_strength)/`seed`/
`remove_background_noise`/`file_format` are real optional fields in
ElevenLabs' schema but are NOT exposed via CLI this pass — same "don't
expose every knob" posture as tts's `voice_settings`/sfx's
`prompt_influence`. The *response* is still raw audio bytes like tts/sfx, so
this needs a request encoder that neither existing `retry` helper covers on
its own (`post_multipart` JSON-decodes its response; `post_bytes` JSON-
encodes its request) — `retry.post_multipart_bytes` fills that gap.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from nazca import config, retry
from nazca.backends.base import Backend
from nazca.backends.error_hints import hint
from nazca.errors import BackendError
from nazca.errors import RateLimitError as _SharedRateLimitError

if TYPE_CHECKING:
    from nazca.request import AudioRequest, SpeechToSpeechRequest

ELEVENLABS_BASE = "https://api.elevenlabs.io"
ELEVENLABS_DEFAULT_MODEL = "eleven_multilingual_v2"
# ElevenLabs' own default `model_id` for /v1/text-to-voice/design, pinned explicitly
# here (same "don't rely on the provider's implicit default" posture as
# ELEVENLABS_DEFAULT_MODEL above for TTS) rather than omitted from the body.
ELEVENLABS_VOICE_DESIGN_MODEL = "eleven_multilingual_ttv_v2"
ELEVENLABS_STS_DEFAULT_MODEL = "eleven_english_sts_v2"

# nazca's `--format mp3|wav` -> ElevenLabs' `output_format` query-param enum.
_OUTPUT_FORMAT_MAP = {
    "mp3": "mp3_44100_128",
    "wav": "wav_44100",
}


class ElevenLabsError(BackendError):
    """Raised when an ElevenLabs request fails (missing key, missing voice, HTTP error)."""


class ElevenLabsRateLimitError(ElevenLabsError, _SharedRateLimitError):
    """429/503 that persisted past NAZCA_MAX_RETRIES retries."""


class ElevenLabsBackend(Backend):
    name = "elevenlabs"

    def tts_endpoint(self, voice_id: str, output_format: str | None = None) -> str:
        """`POST /v1/text-to-speech/{voice_id}` — voice_id is a URL path segment
        (unlike Fish/Worder, where the voice is a body field), and
        `output_format` (when given) is appended as a query-string parameter.

        `voice_id` is user-controlled (`--voice`, or an `elevenlabs:<id>`
        passthrough prefix) and, unlike Fish/Worder's JSON body fields, lands
        directly in a URL — so it's percent-encoded here. Unescaped, a `--voice`
        containing `?`/`&` could hijack the query string (letting the voice
        string override `output_format`), and a space/control character would
        raise a raw `http.client.InvalidURL` that isn't a `BackendError`, so it
        wouldn't be caught by the CLI's `except BackendError` at all.
        """
        url = f"{ELEVENLABS_BASE}/v1/text-to-speech/{quote(voice_id, safe='')}"
        if output_format:
            url += "?" + urlencode({"output_format": output_format})
        return url

    def sfx_endpoint(self, output_format: str | None = None) -> str:
        """`POST /v1/sound-generation` — no `voice_id` path segment (sfx has no
        voice concept); `output_format` (when given) is a query-string param,
        same convention as `tts_endpoint`.
        """
        url = f"{ELEVENLABS_BASE}/v1/sound-generation"
        if output_format:
            url += "?" + urlencode({"output_format": output_format})
        return url

    def voice_design_endpoint(self) -> str:
        """`POST /v1/text-to-voice/design` — Step 1 of ElevenLabs' two-step voice
        creation flow (preview generation only; see module docstring for why
        Step 2, `POST /v1/text-to-voice`, is out of scope here). Unlike
        `tts_endpoint`/`sfx_endpoint`, this endpoint has no `output_format`
        query param — there's no `--format` flag on `nazca voice-design` and
        no caller ever resolves one, so the parameter isn't here to begin with
        rather than being accepted-but-always-`None`.
        """
        return f"{ELEVENLABS_BASE}/v1/text-to-voice/design"

    def voices_endpoint(self) -> str:
        """`GET /v2/voices` — the modern, paginated voice listing. `GET /v1/voices`
        also exists but ElevenLabs' own docs say it stops working once a
        workspace exceeds 500 voices, so `/v2` is preferred here.
        """
        return f"{ELEVENLABS_BASE}/v2/voices"

    def voice_clone_endpoint(self) -> str:
        """`POST /v1/voices/add` — creates a new Instant Voice Clone. A distinct
        "voices" collection endpoint, not a text-to-speech endpoint like
        `tts_endpoint`/`sfx_endpoint` — same relationship as Fish's
        `voice_clone_endpoint` (`POST /model`) to its `tts_endpoint`.
        """
        return f"{ELEVENLABS_BASE}/v1/voices/add"

    def speech_to_speech_endpoint(self, voice_id: str, output_format: str | None = None) -> str:
        """`POST /v1/speech-to-speech/{voice_id}` — voice_id is a URL path segment,
        same convention as `tts_endpoint` (and same percent-encoding rationale:
        `voice_id` is user-controlled via `--voice`); `output_format` (when
        given) is a query-string parameter, same convention as tts/sfx.
        """
        url = f"{ELEVENLABS_BASE}/v1/speech-to-speech/{quote(voice_id, safe='')}"
        if output_format:
            url += "?" + urlencode({"output_format": output_format})
        return url

    def _resolve_output_format(self, output_format: str | None) -> str | None:
        """Map an nazca `output_format` (`"mp3"`/`"wav"`) to ElevenLabs' query-param
        enum, or raise.

        Takes the raw string (not an `AudioRequest`) so `run_audio` and
        `speech_to_speech` — whose request dataclasses don't share a common
        base — can both call it. `output_format` is an unvalidated `str`
        coming from either dataclass (the CLI restricts it to `mp3`/`wav` via
        `click.Choice`, but a direct library caller could pass anything) — a
        value outside `_OUTPUT_FORMAT_MAP` must raise here rather than
        silently falling back to ElevenLabs' own default format.
        """
        if not output_format:
            return None
        mapped = _OUTPUT_FORMAT_MAP.get(output_format)
        if mapped is None:
            raise ElevenLabsError(
                f"Unsupported output_format {output_format!r} for ElevenLabs; "
                f"supported: {', '.join(sorted(_OUTPUT_FORMAT_MAP))}"
            )
        return mapped

    # ------------------------------------------------------------------ auth/http
    def auth_token(self) -> str:
        """Read ELEVENLABS_API_KEY (env > config file) lazily — never called during dry-run."""
        key = config.ELEVENLABS_API_KEY
        if not key:
            raise ElevenLabsError(
                "ELEVENLABS_API_KEY is not set. Run `nazca login` (or `nazca config set "
                "elevenlabs_api_key <key>`) to save it, or export ELEVENLABS_API_KEY for "
                "this session. Get a key at https://elevenlabs.io/"
            )
        return key

    def _headers(self) -> dict:
        # NOTE: `xi-api-key`, NOT `Authorization: Bearer ...` — see module docstring.
        return {
            "xi-api-key": self.auth_token(),
            "Content-Type": "application/json",
        }

    def _post(self, url: str, body: dict) -> bytes:
        return retry.post_bytes(
            url,
            body,
            headers=self._headers(),
            timeout=60,
            on_http_error=lambda code, detail: ElevenLabsError(
                f"ElevenLabs HTTP {code}: {detail}{hint('elevenlabs', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: ElevenLabsRateLimitError(
                f"ElevenLabs rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    # ------------------------------------------------------------------ run seam
    def run_audio(self, resolved, req: AudioRequest):
        """Synchronous text-to-speech (`op="tts"`) or sound-effect generation
        (`op="sfx"`, issue #122 phase A3).

        TTS: `req.voice` (or the resolved provider_id, when it names a real
        voice) supplies the required `voice_id`, baked into the URL path — not
        a body field. `model_id` defaults to `eleven_multilingual_v2`
        (ElevenLabs' own default, not user-configurable today beyond the
        routed `--model`).

        sfx: no voice concept at all — `req.text` is a sound description, not
        speech, posted to a fixed URL with no `voice_id`. `req.duration_seconds`
        (when set) is forwarded; ElevenLabs auto-guesses the duration otherwise.

        Both map `req.output_format` (`--format mp3|wav`) to ElevenLabs'
        `output_format` query-string enum via `_resolve_output_format` (which
        raises for anything outside `mp3`/`wav` rather than silently falling
        back to ElevenLabs' own default format).
        """
        output_format = self._resolve_output_format(req.output_format)

        if req.op == "sfx":
            url = self.sfx_endpoint(output_format)
            body: dict = {"text": req.text}
            if req.duration_seconds is not None:
                body["duration_seconds"] = req.duration_seconds
        else:
            voice_id = req.voice or (resolved.provider_id or None)
            if not voice_id:
                raise ElevenLabsError(
                    "ElevenLabs requires a voice: pass --voice <voice_id> (look one up "
                    "via GET https://api.elevenlabs.io/v2/voices)."
                )
            url = self.tts_endpoint(voice_id, output_format)
            body = {"text": req.text, "model_id": ELEVENLABS_DEFAULT_MODEL}

        if req.dry_run:
            return {
                "url": url,
                "backend": self.name,
                "est_cost_usd": req.est_cost_usd,
                "body": dict(body),
                "headers": {},  # `xi-api-key` deliberately redacted, same posture as Fish/Worder's Authorization
            }

        return self._post(url, body)

    # ------------------------------------------------------------------ voice_clone
    def voice_clone(
        self,
        title: str,
        audio_paths: list[str],
        *,
        description: str | None = None,
        visibility: str = "private",
        tags: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Create an Instant Voice Clone from 1+ audio samples (`POST /v1/voices/add`,
        issue #122 phase A3). Mirrors `FishBackend.voice_clone`'s validate-then-
        dry-run-then-multipart shape closely — see the module docstring for how
        the two backends' request fields differ.

        `title` maps to ElevenLabs' required `name` field. `visibility`/`tags`
        are accepted (so this method satisfies the exact call shape
        `nazca.voice.clone_voice()` uses uniformly for every voice_clone-capable
        backend) but silently ignored — ElevenLabs' `POST /v1/voices/add` has no
        equivalent of Fish's `visibility`/`tags` fields. ElevenLabs also has a
        real `remove_background_noise` field (off by default) with no parameter
        here — nothing in this codebase can set it yet (no CLI flag, no
        orchestrator kwarg), so it's left out entirely rather than added as a
        parameter no caller can reach.

        Unlike Fish (which caps at 20 samples/call), ElevenLabs' published
        OpenAPI spec documents no per-call sample-count limit — only a
        workspace-wide cap of 500 *total* voices, which nazca cannot check
        client-side — so only "at least one sample" is validated here.

        Validates each path itself (not just relying on the CLI's
        `click.Path(exists=True)`) for the same reason as Fish: a direct
        library caller, or a TOCTOU race between the CLI's parse-time check
        and this call, would otherwise hit a raw `OSError` instead of a clean
        `ElevenLabsError`.

        Returns the decoded JSON response — key field `voice_id` (the id to
        pass to `nazca speak --model elevenlabs-tts --voice <voice_id>`); also
        carries `requires_verification` (bool).
        """
        if not audio_paths:
            raise ElevenLabsError("voice_clone requires at least one audio sample path")
        for p in audio_paths:
            if not Path(p).is_file():
                raise ElevenLabsError(f"voice_clone: not a file: {p}")

        fields: dict[str, str] = {"name": title}
        if description:
            fields["description"] = description

        try:
            if dry_run:
                return {
                    "url": self.voice_clone_endpoint(),
                    "backend": self.name,
                    "fields": dict(fields),
                    "files": [
                        {"field": "files", "filename": Path(p).name, "size_bytes": Path(p).stat().st_size}
                        for p in audio_paths
                    ],
                }

            files: list[tuple[str, str, bytes, str]] = [
                (
                    "files",
                    Path(p).name,
                    Path(p).read_bytes(),
                    mimetypes.guess_type(p)[0] or "application/octet-stream",
                )
                for p in audio_paths
            ]
        except OSError as e:
            # A TOCTOU race (deleted/permission-changed between the is_file()
            # check above and the actual read) — clean ElevenLabsError, not a
            # raw traceback, mirroring Fish's voice_clone.
            raise ElevenLabsError(f"voice_clone: couldn't read an audio sample: {e}") from e

        return retry.post_multipart(
            self.voice_clone_endpoint(),
            fields,
            files,
            headers={"xi-api-key": self.auth_token()},
            timeout=120,
            on_http_error=lambda code, detail: ElevenLabsError(
                f"ElevenLabs HTTP {code}: {detail}{hint('elevenlabs', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: ElevenLabsRateLimitError(
                f"ElevenLabs rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    # ------------------------------------------------------------------ speech_to_speech
    def speech_to_speech(self, resolved, req: SpeechToSpeechRequest) -> bytes | dict:
        """Voice conversion (`op="speech_to_speech"`, issue #122 phase A3): convert
        `req.source_audio_path`'s speech into `req.voice`'s voice via
        `POST /v1/speech-to-speech/{voice_id}`.

        Unlike every other ElevenLabs op wired so far, the request is
        `multipart/form-data` (the source audio file is the required `audio`
        field), not JSON — mirroring `FishBackend.voice_clone`'s file-handling
        precedent (validate the path, dry-run needs only its size, a real run
        reads its bytes) rather than `run_audio`'s JSON-body precedent.
        `model_id` defaults to ElevenLabs' own `eleven_english_sts_v2`;
        `voice_settings`/`seed`/`remove_background_noise`/`file_format` are
        real optional fields in ElevenLabs' schema but are NOT exposed via CLI
        this pass — same "don't expose every knob" posture as tts's
        `voice_settings`. `req.output_format` maps through the same
        `_resolve_output_format` helper as tts/sfx.

        The response is raw audio bytes, same as tts/sfx, but arrives over a
        multipart POST — `retry.post_multipart` can't return it (it always
        JSON-decodes), so this uses `retry.post_multipart_bytes` instead.

        Critically, `dry_run` is checked BEFORE the source file is read AND
        before `auth_token()`/headers are built — same invariant every other
        method in this module enforces (see the module docstring's warning
        about the A2 bug class where dry-run touched auth first).
        """
        voice_id = req.voice or (resolved.provider_id or None)
        if not voice_id:
            raise ElevenLabsError(
                "ElevenLabs speech-to-speech requires a target voice: pass --voice "
                "<voice_id> (look one up via GET https://api.elevenlabs.io/v2/voices)."
            )
        if not req.source_audio_path:
            raise ElevenLabsError("speech_to_speech requires a source_audio_path")

        source = Path(req.source_audio_path)
        if not source.is_file():
            raise ElevenLabsError(f"speech_to_speech: not a file: {source}")

        output_format = self._resolve_output_format(req.output_format)
        url = self.speech_to_speech_endpoint(voice_id, output_format)
        fields = {"model_id": ELEVENLABS_STS_DEFAULT_MODEL}

        try:
            if req.dry_run:
                return {
                    "url": url,
                    "backend": self.name,
                    "est_cost_usd": req.est_cost_usd,
                    "fields": dict(fields),
                    "files": [
                        {"field": "audio", "filename": source.name, "size_bytes": source.stat().st_size}
                    ],
                    "headers": {},  # `xi-api-key` deliberately redacted, same posture as run_audio/voice_clone
                }

            audio_bytes = source.read_bytes()
        except OSError as e:
            # A TOCTOU race (deleted/permission-changed between the is_file()
            # check above and either stat() or read_bytes()) — clean
            # ElevenLabsError, not a raw traceback, same posture as Fish's
            # voice_clone.
            raise ElevenLabsError(f"speech_to_speech: couldn't read source audio: {e}") from e

        files: list[tuple[str, str, bytes, str]] = [
            (
                "audio",
                source.name,
                audio_bytes,
                mimetypes.guess_type(str(source))[0] or "application/octet-stream",
            )
        ]

        return retry.post_multipart_bytes(
            url,
            fields,
            files,
            headers={"xi-api-key": self.auth_token()},
            timeout=120,
            on_http_error=lambda code, detail: ElevenLabsError(
                f"ElevenLabs HTTP {code}: {detail}{hint('elevenlabs', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: ElevenLabsRateLimitError(
                f"ElevenLabs rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    # ------------------------------------------------------------------ voice_design
    def voice_design(
        self,
        instruction: str,
        *,
        reference_text: str | None = None,
        language: str | None = None,
        n: int = 2,
        speed: float = 1.0,
        dry_run: bool = False,
    ) -> dict:
        """Generate preview candidate voices from a text description (Step 1 of
        `POST /v1/text-to-voice/design` — see the module docstring for why Step
        2, `POST /v1/text-to-voice`, is deliberately out of scope).

        `instruction` maps to ElevenLabs' required `voice_description` (20-1000
        chars — validated here so a too-short instruction fails fast with a
        clear `ElevenLabsError` instead of a round-trip 422). `reference_text`
        maps to ElevenLabs' optional `text` (sample preview text); when omitted,
        `auto_generate_text: true` is sent instead so ElevenLabs still produces
        a spoken preview rather than erroring on a missing `text`.

        `n`, `language`, and `speed` are accepted (same signature `nazca.voice
        .design_voice()` calls on every backend) but have no ElevenLabs
        equivalent for this endpoint: ElevenLabs' `/design` always returns
        however many previews its model produces (typically a handful) rather
        than taking a requested count, and has no request-level language or
        speed knob here (`language` is Fish-specific TTS routing, `speed` is a
        Fish-specific speech-rate control) — so all three are silently ignored
        for parity with the shared call site rather than raising on ElevenLabs'
        behalf. This mirrors how Fish's `voice_design` may itself return fewer
        than the requested `n` candidates: callers must already read the
        actual returned count off the response rather than assume it.

        Returns `{"candidates": [...]}` reshaped from ElevenLabs' native
        `{"previews": [...], "text": ...}` into Fish's exact candidate shape —
        `generated_voice_id` -> `id`, `audio_base_64` -> `audio_base64` (note
        the underscore-before-64 rename) — so `nazca.voice.design_voice()`
        (which expects Fish's field names verbatim) needs no changes to
        support this backend. Other preview fields (`media_type`,
        `duration_secs`, `language`) pass through unchanged as extra keys.
        """
        if not instruction:
            raise ElevenLabsError("voice_design requires a non-empty instruction")
        if not 20 <= len(instruction) <= 1000:
            raise ElevenLabsError(
                "voice_design: instruction (ElevenLabs' voice_description) must be "
                f"20-1000 characters, got {len(instruction)}"
            )

        body: dict = {
            "voice_description": instruction,
            "model_id": ELEVENLABS_VOICE_DESIGN_MODEL,
        }
        if reference_text:
            body["text"] = reference_text
        else:
            body["auto_generate_text"] = True

        url = self.voice_design_endpoint()

        if dry_run:
            return {
                "url": url,
                "backend": self.name,
                "body": dict(body),
                "headers": {},  # `xi-api-key` deliberately redacted, same posture as run_audio's plan
            }

        raw = retry.post_json(
            url,
            body,
            headers=self._headers(),
            timeout=60,
            on_http_error=lambda code, detail: ElevenLabsError(
                f"ElevenLabs HTTP {code}: {detail}{hint('elevenlabs', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: ElevenLabsRateLimitError(
                f"ElevenLabs rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

        previews = raw.get("previews")
        # Covers both a missing key and an empty/wrong-typed value — an empty
        # list would otherwise pass a bare `is None` check and silently return
        # zero candidates (exit 0, no files written, no explanation).
        if not isinstance(previews, list) or not previews:
            raise ElevenLabsError(
                f"ElevenLabs voice-design response has no previews: {raw!r}"
            )

        candidates = []
        for preview in previews:
            if not isinstance(preview, dict):
                raise ElevenLabsError(
                    f"ElevenLabs voice-design response has a non-object preview: {preview!r}"
                )
            candidate = dict(preview)
            generated_voice_id = candidate.pop("generated_voice_id", None)
            if generated_voice_id is not None:
                candidate["id"] = generated_voice_id
            audio_b64 = candidate.pop("audio_base_64", None)
            if audio_b64 is not None:
                candidate["audio_base64"] = audio_b64
            candidates.append(candidate)

        return {"candidates": candidates}
