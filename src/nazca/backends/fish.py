"""Fish Audio backend — text-to-speech via Fish's hosted + community voice models.

Fish Audio (fish.audio) is a TTS platform: every "voice" is a `reference_id`
naming a specific model (Fish's own hosted models, or ones the community
publishes), so — like Worder — there is no single default voice id. Callers
must pick a `reference_id` (via `--voice`) discovered from `GET /model`; the
`fish-tts` registry entry is a routing placeholder only.

    GET  /model      -> {"total", "items": [ModelEntity], "has_more"}
    POST /v1/tts     -> raw audio bytes (chunked transfer encoding, NOT JSON)

Synchronous (no submit→poll): `/v1/tts` streams the synthesized audio directly
in the response body. The TTS *model* to run (voice-quality tier, distinct
from the `reference_id` voice) is selected via a required `model` HTTP header
— one of `s1`, `s2-pro`, `s2.1-pro`, `s2.1-pro-free` — not a body field;
defaulting to `s2-pro` (Fish's own recommended default) unless overridden.

Because the success response is raw bytes rather than a JSON envelope,
`retry.post_bytes` (not `retry.post_json`) is used to POST it.
"""

from __future__ import annotations

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
        `model` header defaults to `s2-pro` and is not user-configurable today
        (nazca has no per-request knob for it beyond the routed `--model`).
        """
        reference_id = req.voice or (resolved.provider_id or None)
        if not reference_id:
            raise FishError(
                "Fish Audio requires a voice: pass --voice <reference_id> (look one up "
                "via GET https://api.fish.audio/model)."
            )
        model = FISH_DEFAULT_MODEL
        body: dict = {"reference_id": reference_id, "text": req.text}

        if req.dry_run:
            return {
                "url": self.tts_endpoint(),
                "backend": self.name,
                "est_cost_usd": req.est_cost_usd,
                "body": dict(body),
                "headers": {"model": model},
            }

        return self._post(body, model)
