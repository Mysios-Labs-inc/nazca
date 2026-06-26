"""Nazca bundled CC0 looks generator.

Deterministically builds five 17x17x17 3-D LUTs and writes them to
src/nazca/luts/<name>.cube.  PIL and stdlib only; no numpy; no randomness.

Run from the repo root::

    python scripts/gen_looks.py

Re-running must produce bit-for-bit identical output (the only knobs are the
hard-coded parameters below; there is no time, randomness, or external state).

Each .cube file:
  - one header comment line:  # nazca bundled look — CC0
  - TITLE "<name>"
  - LUT_3D_SIZE 17
  - blank line
  - 17**3 data lines ``R G B`` (six decimal places), R index varying fastest

This matches the loop order expected by PIL's Color3DLUT / load_cube().
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = _REPO_ROOT / "src" / "nazca" / "luts"
SIZE = 17
STEP = 1.0 / (SIZE - 1)  # 0.0625


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else (hi if x > hi else x)


def _s_curve(x: float, strength: float) -> float:
    """Symmetric cubic S-curve passing through (0,0), (0.5,0.5), (1,1).

    With *strength* > 0 contrast is boosted (shadows darker, highlights
    brighter).  With *strength* < 0 contrast is reduced (matte/faded look).
    The formula is::

        y = x + strength * x * (1 − x) * (x − 0.5)
    """
    return x + strength * x * (1.0 - x) * (x - 0.5)


def _lift(x: float, lift: float) -> float:
    """Rescale [0, 1] to [lift, 1]: lifts the black point without compressing whites."""
    return lift + (1.0 - lift) * x


# ---------------------------------------------------------------------------
# Per-look transform functions
# ---------------------------------------------------------------------------


def _neutral_contrast(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Pure tone S-curve, no colour shift.

    Parameters
    ----------
    S-curve strength: 0.9  (boosts contrast symmetrically)
    """
    R = _clamp(_s_curve(r, 0.9))
    G = _clamp(_s_curve(g, 0.9))
    B = _clamp(_s_curve(b, 0.9))
    return R, G, B


def _warm_editorial(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Slight warm white balance, gentle S-curve, tiny lifted blacks.

    Parameters
    ----------
    White balance:  R×1.00  G×1.00  B×0.93  (eases blue slightly)
    Black lift:     R 0.015  G 0.012  B 0.010
    S-curve:        strength 0.5  (gentler than neutral)
    """
    WB_R, WB_G, WB_B = 1.00, 1.00, 0.93
    LFT_R, LFT_G, LFT_B = 0.015, 0.012, 0.010
    STRENGTH = 0.5
    R = _clamp(_lift(_s_curve(_clamp(r * WB_R), STRENGTH), LFT_R))
    G = _clamp(_lift(_s_curve(_clamp(g * WB_G), STRENGTH), LFT_G))
    B = _clamp(_lift(_s_curve(_clamp(b * WB_B), STRENGTH), LFT_B))
    return R, G, B


def _golden_hour(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Stronger warm cast, boosted R and warm highlights, lowered blue.

    Parameters
    ----------
    White balance:  R×1.10  G×1.00  B×0.80  (warm-orange cast)
    Black lift:     R 0.018  G 0.010  B 0.004
    S-curve:        strength 0.7  (moderate contrast boost)
    """
    WB_R, WB_G, WB_B = 1.10, 1.00, 0.80
    LFT_R, LFT_G, LFT_B = 0.018, 0.010, 0.004
    STRENGTH = 0.7
    R = _clamp(_lift(_s_curve(_clamp(r * WB_R), STRENGTH), LFT_R))
    G = _clamp(_lift(_s_curve(_clamp(g * WB_G), STRENGTH), LFT_G))
    B = _clamp(_lift(_s_curve(_clamp(b * WB_B), STRENGTH), LFT_B))
    return R, G, B


def _cool_matte(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Lifted (matte) blacks, mild desaturation, slightly cool shadows.

    Parameters
    ----------
    Desaturation:  10 % toward channel mean (reduces colour saturation mildly)
    Black lift:    R 0.035  G 0.055  B 0.070  (cool blue bias in shadows)
    Tone:          none (linear — no S-curve, preserves the soft matte quality)
    """
    LFT_R, LFT_G, LFT_B = 0.035, 0.055, 0.070
    DESAT = 0.10
    mean = (r + g + b) / 3.0
    r2 = r + DESAT * (mean - r)
    g2 = g + DESAT * (mean - g)
    b2 = b + DESAT * (mean - b)
    R = _clamp(_lift(r2, LFT_R))
    G = _clamp(_lift(g2, LFT_G))
    B = _clamp(_lift(b2, LFT_B))
    return R, G, B


def _faded_film(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Lifted blacks, reduced contrast, subtle warm/green cast.

    Parameters
    ----------
    White balance:  R×1.00  G×1.02  B×0.97  (subtle warm-green tint)
    Black lift:     R 0.080  G 0.080  B 0.078
    S-curve:        strength −0.40  (negative → anti-contrast / faded look)
    """
    WB_R, WB_G, WB_B = 1.00, 1.02, 0.97
    LFT_R, LFT_G, LFT_B = 0.080, 0.080, 0.078
    STRENGTH = -0.40
    R = _clamp(_lift(_s_curve(_clamp(r * WB_R), STRENGTH), LFT_R))
    G = _clamp(_lift(_s_curve(_clamp(g * WB_G), STRENGTH), LFT_G))
    B = _clamp(_lift(_s_curve(_clamp(b * WB_B), STRENGTH), LFT_B))
    return R, G, B


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

_LOOKS: list[tuple[str, object]] = [
    ("neutral-contrast", _neutral_contrast),
    ("warm-editorial", _warm_editorial),
    ("golden-hour", _golden_hour),
    ("cool-matte", _cool_matte),
    ("faded-film", _faded_film),
]


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def _write_cube(name: str, fn: object, out_dir: Path) -> Path:
    """Build a 17³ LUT from *fn* and write an Adobe .cube file.

    The loop order matches PIL's Color3DLUT layout: R index varies fastest,
    then G, then B (outermost).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{name}.cube"
    lines: list[str] = [
        "# nazca bundled look — CC0",
        f'TITLE "{name}"',
        "LUT_3D_SIZE 17",
        "",
    ]
    for b_idx in range(SIZE):
        b = b_idx * STEP
        for g_idx in range(SIZE):
            g = g_idx * STEP
            for r_idx in range(SIZE):
                r = r_idx * STEP
                R, G, B = fn(r, g, b)
                lines.append(f"{R:.6f} {G:.6f} {B:.6f}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    for name, fn in _LOOKS:
        dest = _write_cube(name, fn, OUT_DIR)
        print(f"wrote {dest}")


if __name__ == "__main__":
    main()
