"""Worder backend — text-to-speech using verified human voice actors.

Worder (www.worder.com) is a TTS marketplace: every "voice" is a real actor's voice,
not a house model, so there is no single default voice id the way Atlas has
`xai/tts-v1`. Callers must pick a `voice_id` (via `--voice`) discovered from
`GET /voices`; the `worder-tts` registry entry is a routing placeholder only.

    GET  /api/v1/voices   -> {"voices": [...], "total", "limit", "offset"}
    POST /api/v1/generate -> {"audio_url", "seconds_generated", "cost_cents", ...}

Synchronous (no submit→poll): `generate` returns the audio URL directly.
Text supports direction tags (`[happy]`), pause tags (`[pause N]`), emphasis
tags, and pronunciation overrides (`{written|spoken}`) — passed through verbatim
in `req.text`, no transformation needed here.

Quality gate: a Whisper transcript of the output must match the input text at
>=90% similarity or the call fails with 422 (no charge) — surfaced as a
WorderError via error_hints.
"""

from __future__ import annotations

import urllib.request
from typing import TYPE_CHECKING

from nazca import config, retry
from nazca.backends.base import Backend
from nazca.backends.error_hints import hint
from nazca.errors import BackendError
from nazca.errors import RateLimitError as _SharedRateLimitError

if TYPE_CHECKING:
    from nazca.request import AudioRequest

WORDER_BASE = "https://www.worder.com/api/v1"


class WorderError(BackendError):
    """Raised when a Worder request fails (missing key, missing voice, HTTP error)."""


class WorderRateLimitError(WorderError, _SharedRateLimitError):
    """429/503 that persisted past NAZCA_MAX_RETRIES retries."""


class WorderBackend(Backend):
    name = "worder"

    def generate_endpoint(self) -> str:
        return f"{WORDER_BASE}/generate"

    def voices_endpoint(self) -> str:
        return f"{WORDER_BASE}/voices"

    # ------------------------------------------------------------------ auth/http
    def auth_token(self) -> str:
        """Read WORDER_API_KEY (env > config file) lazily — never called during dry-run."""
        key = config.WORDER_API_KEY
        if not key:
            raise WorderError(
                "WORDER_API_KEY is not set. Run `nazca login` (or `nazca config set "
                "worder_api_key <key>`) to save it, or export WORDER_API_KEY for this "
                "session. Get a key at https://www.worder.com/developers"
            )
        return key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.auth_token()}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        url = f"{WORDER_BASE}{path}"
        return retry.post_json(
            url,
            body,
            headers=self._headers(),
            timeout=30,
            on_http_error=lambda code, detail: WorderError(
                f"Worder HTTP {code}: {detail}{hint('worder', code, detail)}"
            ),
            on_rate_limited=lambda code, detail: WorderRateLimitError(
                f"Worder rate limit (HTTP {code}) persisted after retries: {detail}"
            ),
        )

    # ------------------------------------------------------------------ run seam
    def run_audio(self, resolved, req: AudioRequest):
        """Synchronous text-to-speech. `req.voice` (or the resolved provider_id,
        when it names a real voice) supplies the required `voice_id`.
        """
        voice_id = req.voice or (resolved.provider_id or None)
        if not voice_id:
            raise WorderError(
                "Worder requires a voice: pass --voice <voice_id> (look one up via "
                "GET https://www.worder.com/api/v1/voices)."
            )
        body: dict = {"voice_id": voice_id, "text": req.text}

        if req.dry_run:
            return {
                "url": self.generate_endpoint(),
                "backend": self.name,
                "est_cost_usd": req.est_cost_usd,
                "body": dict(body),
            }

        resp = self._post("/generate", body)
        audio_url = resp.get("audio_url")
        if not audio_url:
            raise WorderError(f"No audio_url in response: {resp}")
        with urllib.request.urlopen(audio_url, timeout=60) as r:  # noqa: S310
            return r.read()
