"""Actionable hints for common provider account / billing HTTP errors.

When a backend's HTTP call returns 401/402/403/404/429 the bare status dump
tells the user almost nothing useful.  This module maps (provider, code, body)
→ a short hint string that is *appended* to the existing error message so the
original detail is preserved for debugging and the user gets an immediate next
action.

Usage (in each backend's except-block)::

    from nazca.backends.error_hints import hint
    raise MyBackendError(f"HTTP {e.code}: {detail}{hint('openai', e.code, detail)}")

The hint is an empty string when no mapping matches, so the call-site logic is
always the same regardless of status code.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Per-provider hint tables: (code, body_substring) → hint text.
# Body substrings are checked case-insensitively; first match wins.
# A None body key means "match any body for this code".
# ---------------------------------------------------------------------------

_HINTS: dict[str, list[tuple[int, str | None, str]]] = {
    "openai": [
        (
            401,
            None,
            " — invalid or missing OPENAI_API_KEY; check platform.openai.com/api-keys",
        ),
        (
            402,
            None,
            " — out of credit or billing issue; check Billing → Credit grants + Limits"
            " (promo grants deplete silently)",
        ),
        (
            403,
            None,
            " — access denied; verify your API key has the right permissions at"
            " platform.openai.com/api-keys",
        ),
        (
            429,
            "insufficient_quota",
            " — out of credit or hit a quota limit; check Billing → Credit grants + Limits"
            " (promo grants deplete silently)",
        ),
        (
            429,
            None,
            " — rate-limited; slow down requests or check Usage limits at"
            " platform.openai.com/settings/organization/limits",
        ),
    ],
    "fal": [
        (
            401,
            None,
            " — invalid or missing FAL_KEY; go to fal.ai dashboard → API Keys",
        ),
        (
            403,
            None,
            " — access denied; verify your FAL_KEY at fal.ai dashboard → API Keys",
        ),
        (
            402,
            None,
            " — billing issue; check your fal.ai account balance",
        ),
        (
            429,
            None,
            " — rate-limited by fal; reduce request concurrency or check fal.ai plan limits",
        ),
    ],
    "modelark": [
        (
            401,
            None,
            " — invalid or missing ARK_API_KEY; check the BytePlus Ark console credentials",
        ),
        (
            403,
            None,
            " — access denied; verify your ARK_API_KEY in the BytePlus Ark console",
        ),
        (
            404,
            "InvalidEndpointOrModel",
            " — model not activated for this account; activate it in the BytePlus Ark"
            " console (region ap-southeast) before calling it",
        ),
        (
            404,
            None,
            " — endpoint or model not found; verify the model ID and that it is"
            " activated in the BytePlus Ark console (region ap-southeast)",
        ),
        (
            429,
            None,
            " — rate-limited by ModelArk; reduce concurrency or check your Ark plan limits",
        ),
    ],
    "atlas": [
        (
            401,
            None,
            " — invalid or missing ATLAS_API_KEY; get one at"
            " atlascloud.ai/console/api-keys",
        ),
        (
            402,
            None,
            " — out of credit or billing issue; top up at atlascloud.ai/console",
        ),
        (
            403,
            None,
            " — access denied; verify your ATLAS_API_KEY (and that it is a"
            " pay-as-you-go key, not a Coding-Plan-scoped key) at"
            " atlascloud.ai/console/api-keys",
        ),
        (
            404,
            None,
            " — model not found; verify the model slug (provider/model/operation)"
            " against atlascloud.ai/models",
        ),
        (
            429,
            None,
            " — rate-limited by Atlas Cloud; reduce concurrency or check plan limits",
        ),
    ],
}


def hint(provider: str, code: int, body: str) -> str:
    """Return an actionable hint string (possibly empty) for *provider* + HTTP *code*.

    The returned string always starts with " — " when non-empty so callers can
    simply append it to their existing error message without extra formatting.
    """
    body_lower = body.lower()
    for entry_code, body_key, text in _HINTS.get(provider, []):
        if entry_code != code:
            continue
        if body_key is None or body_key.lower() in body_lower:
            return text
    return ""
