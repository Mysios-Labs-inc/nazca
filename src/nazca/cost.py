"""Per-model cost estimation — make the bill visible at the point of use.

Every figure here is an *estimate*. Provider pricing drifts and gpt-image-2 is
token-billed (cost scales with size × quality), so we never claim precision: an
estimate carries an `approx` flag and the CLI prints it with a leading "~".

Two kinds of pricing:
  - flat per-image (most models) — a known constant per generated image.
  - token-billed (gpt-image-2) — no flat rate; we estimate from OpenAI's
    published output-image-token counts × the $30 / 1M output-token rate, and
    surface the actual cost from the `usage` block after a real run.

Prices sourced 2026-06-22; treat as ballpark, not contractual.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- flat prices
# USD per generated image. flux-schnell is billed ~$0.003/MP — at a typical 1MP
# output that's ~$0.003, which we use as the flat estimate.
_FLAT_USD: dict[str, float] = {
    "imagen-4-fast":   0.02,
    "imagen-4":        0.04,
    "imagen-3":        0.02,    # same family as imagen-4-fast tier
    "nano-banana":     0.039,
    "nano-banana-2":   0.039,
    "nano-banana-pro": 0.134,   # @1K/2K; ~$0.24 @4K (see _nano_banana_pro)
    "seedream":        0.035,
    "flux-schnell":    0.003,   # ~$0.003/MP, ~1MP typical
}

# --------------------------------------------------------------------------- OpenAI tokens
# OpenAI's published output-image-token counts for gpt-image (high quality), keyed
# by pixel size. medium ≈ 1/4 of high, low ≈ 1/16 of high.
_OPENAI_HIGH_TOKENS: dict[str, int] = {
    "1024x1024": 4160,
    "1024x1536": 6240,
    "1536x1024": 6208,
}
_OPENAI_QUALITY_FACTOR: dict[str, float] = {
    "high": 1.0,
    "medium": 0.25,
    "low": 0.0625,
    "auto": 1.0,  # "auto" usually lands high — estimate conservatively as high
}
_OPENAI_OUTPUT_USD_PER_TOKEN = 30.0 / 1_000_000  # $30 per 1M output tokens


@dataclass(frozen=True)
class CostEstimate:
    """A cost figure with provenance.

    `usd`      the dollar amount.
    `approx`   True if this is an estimate (always True for token-billed; flat
               per-image prices are "known" but still drift, so we keep the flag
               honest and only clear it for actual usage-derived costs).
    `basis`    short human label, e.g. "flat per-image" or "est 4160 out-tokens".
    """

    usd: float
    approx: bool = True
    basis: str = ""

    def label(self) -> str:
        """Render like '~$0.05' (approx) or '$0.04' (known/actual)."""
        prefix = "~" if self.approx else ""
        return f"{prefix}${self.usd:.4f}".rstrip("0").rstrip(".") if self.usd else f"{prefix}$0"


def _nano_banana_pro(size: str | None) -> float:
    """nano-banana-pro is ~$0.134 @1K/2K, ~$0.24 @4K."""
    if isinstance(size, str) and size.upper() == "4K":
        return 0.24
    return 0.134


def estimate_image_cost(
    model_shorthand: str | None,
    *,
    size: str | None = None,
    aspect_size: str | None = None,
    quality: str | None = None,
) -> CostEstimate | None:
    """Estimate the cost of one image for `model_shorthand`.

    `size`         nazca --size (1K/2K/4K) — used for nano-banana-pro tiering.
    `aspect_size`  the resolved OpenAI pixel size ("1024x1024" etc.) for gpt-image-2.
    `quality`      gpt-image-2 cost lever (low|medium|high|auto).

    Returns None when we have no pricing for the model (raw ids, fal modify ops,
    overrides) — the caller should treat that as "cost unknown".
    """
    if not model_shorthand:
        return None

    if model_shorthand == "nano-banana-pro":
        return CostEstimate(_nano_banana_pro(size), approx=True, basis="flat per-image")

    if model_shorthand == "gpt-image-2":
        return _estimate_gpt_image(aspect_size, quality)

    flat = _FLAT_USD.get(model_shorthand)
    if flat is None:
        return None
    return CostEstimate(flat, approx=True, basis="flat per-image")


def _estimate_gpt_image(aspect_size: str | None, quality: str | None) -> CostEstimate:
    """Estimate gpt-image-2 from output-image tokens × $30/1M (+ negligible text in)."""
    size_key = aspect_size if aspect_size in _OPENAI_HIGH_TOKENS else "1024x1024"
    high_tokens = _OPENAI_HIGH_TOKENS[size_key]
    factor = _OPENAI_QUALITY_FACTOR.get((quality or "high").lower(), 1.0)
    out_tokens = round(high_tokens * factor)
    usd = out_tokens * _OPENAI_OUTPUT_USD_PER_TOKEN
    return CostEstimate(usd, approx=True, basis=f"est {out_tokens} out-tokens @{size_key}")


def cost_from_openai_usage(usage: dict | None) -> CostEstimate | None:
    """Compute the ACTUAL gpt-image-2 cost from an OpenAI `usage` block.

    OpenAI /images responses include `usage` with `output_tokens` (image) and
    `input_tokens` (text/image in). Output image tokens dominate; we price output
    at $30/1M and text input at the standard $5/1M — small but not invented.
    Returns None if no usable usage block is present.
    """
    if not isinstance(usage, dict):
        return None
    out_tokens = usage.get("output_tokens")
    if out_tokens is None:
        return None
    in_tokens = usage.get("input_tokens") or 0
    usd = out_tokens * _OPENAI_OUTPUT_USD_PER_TOKEN + in_tokens * (5.0 / 1_000_000)
    # Derived from real token counts → not an estimate.
    return CostEstimate(usd, approx=False, basis=f"{out_tokens} out + {in_tokens} in tokens")
