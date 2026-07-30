"""Fish Audio backend — text-to-speech via Fish's hosted + community voice models.

Fish Audio (fish.audio) is a TTS platform: every "voice" is a `reference_id`
naming a specific model (Fish's own hosted models, or ones the community
publishes), so — like Worder — there is no single default voice id. Callers
must pick a `reference_id` (via `--voice`) discovered from `GET /model`; the
`fish-tts` registry entry is a routing placeholder only.

    GET  /model              -> {"total", "items": [ModelEntity], "has_more"}
    POST /v1/tts             -> raw audio bytes (chunked transfer encoding, NOT JSON)
    POST /model              -> {"_id", "title", "state", "visibility", ...} (voice_clone)
    POST /v1/voice-design    -> {"candidates": [{"id", "audio_base64", ...}]} (voice_design)

    The two response shapes above are per Fish's published OpenAPI schema, not
    verified against a live key — same posture as the rest of this file's
    "schema unverified" notes. `nazca.voice.design_voice` raises a clear error
    rather than guessing if a candidate turns out to be missing `audio_base64`.

Synchronous (no submit→poll): `/v1/tts` streams the synthesized audio directly
in the response body. The TTS *model* to run (voice-quality tier, distinct
from the `reference_id` voice) is selected via a required `model` HTTP header
— one of `s1`, `s2-pro`, `s2.1-pro`, `s2.1-pro-free` — not a body field;
defaulting to `s2-pro` (nazca's own default choice — Fish's header itself
defaults to `s2.1-pro-free` and Fish recommends `s2.1-pro` for production)
unless overridden.

Because the success response is raw bytes rather than a JSON envelope,
`retry.post_bytes` (not `retry.post_json`) is used to POST it.

`voice_clone`/`voice_design` (issue #122 phase A2) are a different shape — one
uploads files and returns model metadata, the other returns several audio
candidates — so they don't go through `run_audio`/`AudioRequest`; see
`FishBackend.voice_clone`/`.voice_design` and the `nazca.voice` orchestrator.
`POST /model` is the first multipart/form-data call in nazca (every other
backend POSTs JSON or receives raw bytes), so it goes through the new
`retry.post_multipart`. `POST /v1/voice-design` reuses `retry.post_json` but
with a *different* required `model` header value (`voice-design-1`, not one of
the TTS quality tiers).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

from nazca import config, retry
from nazca.backends.base import Backend
from nazca.backends.error_hints import hint
from nazca.errors import BackendError
from nazca.errors import RateLimitError as _SharedRateLimitError

if TYPE_CHECKING:
    from nazca.request import AudioRequest

FISH_BASE = "https://api.fish.audio"
FISH_DEFAULT_MODEL = "s2-pro"
FISH_VOICE_DESIGN_MODEL = "voice-design-1"


class FishError(BackendError):
    """Raised when a Fish Audio request fails (missing key, missing voice, HTTP error)."""


class FishRateLimitError(FishError, _SharedRateLimitError):
    """429/503 that persisted past NAZCA_MAX_RETRIES retries."""


class FishBackend(Backend):
    name = "fish"

    def tts_endpoint(self) -> str:
        return f"{FISH_BASE}/v1/tts"

    def voices_endpoint(self) -> str:
        """Fish has no dedicated voices-list endpoint; `GET /model` (paginated,
        filterable by title/tags/author/language) is the closest equivalent —
        each returned model's id is a usable `reference_id`.
        """
        return f"{FISH_BASE}/model"

    def voice_clone_endpoint(self) -> str:
        """`POST /model` — the same collection endpoint `GET /model` reads from;
        a POST here creates a new voice-clone model instead of listing existing ones.
        """
        return f"{FISH_BASE}/model"

    def voice_design_endpoint(self) -> str:
        return f"{FISH_BASE}/v1/voice-design"

    # ------------------------------------------------------------------ auth/http
    def auth_token(self) -> str:
        """Read FISH_API_KEY (env > config file) lazily — never called during dry-run."""
        key = config.FISH_API_KEY
        if not key:
            raise FishError(
                "FISH_API_KEY is not set. Run `nazca login` (or `nazca config set "
                "fish_api_key <key>`) to save it, or export FISH_API_KEY for this "
                "session. Get a key at https://fish.audio/"
            )
        return key

    def _headers(self, model: str) -> dict:
        return {
            "Authorization": f"Bearer {self.auth_token()}",
            "Content-Type": "application/json",
            "model": model,
        }

    def _post(self, body: dict, model: str) -> bytes:
        return retry.post_bytes(
            self.tts_endpoint(),
            body,
            headers=self._headers(model),
            timeout=60,
            on_http_error=lambda code, detail: FishError(
                f"Fish Audio HTTP {code}: {detail}{hint('fish', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: FishRateLimitError(
                f"Fish Audio rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    # ------------------------------------------------------------------ run seam
    def run_audio(self, resolved, req: AudioRequest):
        """Synchronous text-to-speech. `req.voice` (or the resolved provider_id,
        when it names a real voice) supplies the required `reference_id`. The TTS
        `model` header defaults to `s2-pro` — nazca's own default, not user-
        configurable today (nazca has no per-request knob for it beyond the
        routed `--model`). `req.output_format` (`--format mp3|wav`) is forwarded
        as the `format` body field, same as the Atlas backend.
        """
        reference_id = req.voice or (resolved.provider_id or None)
        if not reference_id:
            raise FishError(
                "Fish Audio requires a voice: pass --voice <reference_id> (look one up "
                "via GET https://api.fish.audio/model)."
            )
        model = FISH_DEFAULT_MODEL
        body: dict = {"reference_id": reference_id, "text": req.text}
        if req.output_format:
            body["format"] = req.output_format

        if req.dry_run:
            return {
                "url": self.tts_endpoint(),
                "backend": self.name,
                "est_cost_usd": req.est_cost_usd,
                "body": dict(body),
                "headers": {"model": model},
            }

        return self._post(body, model)

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
        """Create a reusable voice model from 1-20 audio samples (`POST /model`).

        Fish's own API default for `visibility` is `"public"` — nazca defaults to
        `"private"` instead (a CLI shouldn't silently publish someone's cloned
        voice). `train_mode` is always `"fast"` (instant availability); the other
        documented mode is not covered here. Returns the decoded JSON response
        (key field `_id` — the `reference_id` to pass to `nazca speak --voice`).

        Validates each path itself rather than relying on the CLI's
        `click.Path(exists=True)` — a direct library caller of this method (or a
        TOCTOU race between the CLI's parse-time check and this call) would
        otherwise hit a raw `OSError` instead of a clean `FishError`.
        """
        if not audio_paths:
            raise FishError("voice_clone requires at least one audio sample path")
        if len(audio_paths) > 20:
            raise FishError(
                f"voice_clone accepts at most 20 audio samples, got {len(audio_paths)}"
            )
        for p in audio_paths:
            if not Path(p).is_file():
                raise FishError(f"voice_clone: not a file: {p}")

        fields: dict[str, str] = {
            "type": "tts",
            "title": title,
            "train_mode": "fast",
            "visibility": visibility,
        }
        if description:
            fields["description"] = description
        if tags:
            # `post_multipart`'s `fields` is single-value-per-key (see its docstring);
            # Fish's `tags` is documented as a string array but the exact multipart
            # array encoding isn't specified in the OpenAPI schema, so this sends a
            # single comma-joined `tags` field — UNVERIFIED against a live key.
            fields["tags"] = ",".join(tags)

        try:
            if dry_run:
                return {
                    "url": self.voice_clone_endpoint(),
                    "backend": self.name,
                    "fields": dict(fields),
                    "files": [
                        {"field": "voices", "filename": Path(p).name, "size_bytes": Path(p).stat().st_size}
                        for p in audio_paths
                    ],
                }

            files: list[tuple[str, str, bytes, str]] = [
                (
                    "voices",
                    Path(p).name,
                    Path(p).read_bytes(),
                    mimetypes.guess_type(p)[0] or "application/octet-stream",
                )
                for p in audio_paths
            ]
        except OSError as e:
            # A TOCTOU race (deleted/permission-changed between the is_file()
            # check above and the actual read) — clean FishError, not a raw
            # traceback, so a library caller doesn't need to catch OSError too.
            raise FishError(f"voice_clone: couldn't read an audio sample: {e}") from e

        return retry.post_multipart(
            self.voice_clone_endpoint(),
            fields,
            files,
            headers={"Authorization": f"Bearer {self.auth_token()}"},
            timeout=120,
            on_http_error=lambda code, detail: FishError(
                f"Fish Audio HTTP {code}: {detail}{hint('fish', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: FishRateLimitError(
                f"Fish Audio rate limit (HTTP {code}) persisted after retries: {detail}"
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
        """Generate `n` candidate voices from a text description (`POST /v1/voice-design`).

        Returns the decoded JSON response with `candidates`: each has `audio_base64`
        (raw synthesized preview audio, base64-encoded — NOT a URL to fetch).
        """
        if not instruction:
            raise FishError("voice_design requires a non-empty instruction")
        if not 1 <= n <= 4:
            raise FishError(f"voice_design: n must be 1-4, got {n}")
        if not 0 < speed <= 3.0:
            raise FishError(f"voice_design: speed must be >0-3.0, got {speed}")

        body: dict = {"instruction": instruction, "n": n, "speed": speed}
        if reference_text:
            body["reference_text"] = reference_text
        if language:
            body["language"] = language

        if dry_run:
            return {
                "url": self.voice_design_endpoint(),
                "backend": self.name,
                "body": dict(body),
                "headers": {"model": FISH_VOICE_DESIGN_MODEL},
            }

        headers = {
            "Authorization": f"Bearer {self.auth_token()}",
            "Content-Type": "application/json",
            "model": FISH_VOICE_DESIGN_MODEL,
        }

        return retry.post_json(
            self.voice_design_endpoint(),
            body,
            headers=headers,
            timeout=60,
            on_http_error=lambda code, detail: FishError(
                f"Fish Audio HTTP {code}: {detail}{hint('fish', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: FishRateLimitError(
                f"Fish Audio rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )
