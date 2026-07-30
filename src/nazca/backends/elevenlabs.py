"""ElevenLabs backend — text-to-speech and sound effects via ElevenLabs' own
API (issue #122 phase A3).

Today ElevenLabs is only reachable indirectly through one Atlas-proxied model
(`atlas-tts-elevenlabs-v3`), which hides ElevenLabs' real model catalog,
`voice_settings`, and output-format control. This module adds ElevenLabs as a
fourth *direct* speech provider, parallel to Worder/Fish. TTS and sound
effects (`sfx`) are wired; voice design/speech-to-speech/dubbing/etc are
still a later follow-up per `docs/media-modalities.md`'s "Audio roadmap" (A3
sub-phases). This also absorbs issue #121 (a full-integration proposal).

    POST /v1/text-to-speech/{voice_id}?output_format=...  -> raw audio bytes
    POST /v1/sound-generation?output_format=...           -> raw audio bytes
    GET  /v2/voices                                       -> {"voices": [...], "next_page_token", ...}

Three structural differences from every other backend in this codebase worth
flagging explicitly, since a future maintainer would reasonably assume this
backend follows the same shape as Fish/Worder/Atlas/OpenAI (it doesn't):

1. **Auth header is `xi-api-key: <key>`, NOT `Authorization: Bearer <key>`.**
   Every other backend here (Fish, Worder, Atlas, OpenAI) uses Bearer auth;
   ElevenLabs does not. Get this wrong and every request 401s.
2. **(TTS only) the voice is a URL *path* parameter**, not a body field —
   unlike Fish's `reference_id` body field or Worder's `voice_id` body field.
   `tts_endpoint` therefore takes `voice_id` and bakes it into the URL. `sfx`
   has no voice concept at all — `sfx_endpoint` takes no id.
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
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from nazca import config, retry
from nazca.backends.base import Backend
from nazca.backends.error_hints import hint
from nazca.errors import BackendError
from nazca.errors import RateLimitError as _SharedRateLimitError

if TYPE_CHECKING:
    from nazca.request import AudioRequest

ELEVENLABS_BASE = "https://api.elevenlabs.io"
ELEVENLABS_DEFAULT_MODEL = "eleven_multilingual_v2"

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

    def voices_endpoint(self) -> str:
        """`GET /v2/voices` — the modern, paginated voice listing. `GET /v1/voices`
        also exists but ElevenLabs' own docs say it stops working once a
        workspace exceeds 500 voices, so `/v2` is preferred here.
        """
        return f"{ELEVENLABS_BASE}/v2/voices"

    def _resolve_output_format(self, req: AudioRequest) -> str | None:
        """Map `req.output_format` to ElevenLabs' query-param enum, or raise.

        `AudioRequest.output_format` is an unvalidated `str` (the CLI restricts
        it to `mp3`/`wav` via `click.Choice`, but a direct library caller could
        pass anything) — a value outside `_OUTPUT_FORMAT_MAP` must raise here
        rather than silently falling back to ElevenLabs' own default format.
        """
        if not req.output_format:
            return None
        mapped = _OUTPUT_FORMAT_MAP.get(req.output_format)
        if mapped is None:
            raise ElevenLabsError(
                f"Unsupported output_format {req.output_format!r} for ElevenLabs; "
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
        output_format = self._resolve_output_format(req)

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
