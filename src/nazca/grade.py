"""nazca.grade — local colour-grading primitive (no model, no network, no cost).

This module is nazca's finishing layer: apply a colour LUT to an already-generated
image entirely on-device using Pillow's Color3DLUT filter.  It is deliberately
walled off from the model-orchestration core:

    ARCHITECTURE FIREWALL
    grade.py must NOT import from nazca.capabilities, nazca.models,
    nazca.backends, nazca.image, or nazca.request.
    Allowed: stdlib + PIL only.

Entry points
------------
load_cube(path)  → Color3DLUT   parse an Adobe/Iridas .cube file
load_hald(path)  → Color3DLUT   load a HALD CLUT PNG
load_lut(spec)   → Color3DLUT   resolve a name or path, dispatch to the above
apply_grade(img, lut, strength, grain, grain_size) → Image
crop_to_preset(img, preset, gravity) → Image  head-safe center crop to platform aspect
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

# ---------------------------------------------------------------------------
# Platform format presets
# ---------------------------------------------------------------------------

PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (9, 16),
    "4:5": (4, 5),
    "1:1": (1, 1),
    "2:3": (2, 3),
    "16:9": (16, 9),
}


def crop_to_preset(img: Image.Image, preset: str, gravity: str = "north") -> Image.Image:
    """Head-safe center crop to a platform aspect preset. Crops only — NEVER upscales.

    gravity controls the VERTICAL anchor when trimming height (portrait crops of people):
      north = keep the top (faces), south = keep the bottom, center = middle.
    Horizontal trims are always centered.
    """
    if preset not in PRESETS:
        valid = ", ".join(sorted(PRESETS))
        raise ValueError(f"Unknown preset {preset!r}. Valid presets: {valid}")
    tw, th = PRESETS[preset]
    w, h = img.size
    target = tw / th
    cur = w / h
    if cur > target:  # too wide -> trim width, centered horizontally
        new_w = min(w, round(h * target))
        x = (w - new_w) // 2
        box = (x, 0, x + new_w, h)
    else:  # too tall (or equal) -> trim height by gravity
        new_h = min(h, round(w / target))
        if gravity == "north":
            y = 0
        elif gravity == "south":
            y = h - new_h
        else:
            y = (h - new_h) // 2
        box = (0, y, w, y + new_h)
    return img.crop(box)


# ---------------------------------------------------------------------------
# .cube parser
# ---------------------------------------------------------------------------


def load_cube(path: str | Path) -> ImageFilter.Color3DLUT:
    """Parse a 3-D Adobe/Iridas .cube file and return a Color3DLUT.

    Spec notes
    ----------
    - Lines beginning with '#' and blank lines are skipped.
    - TITLE keyword is skipped.
    - LUT_3D_SIZE N sets the cube dimension; N**3 RGB lines follow.
    - LUT_1D_SIZE is rejected — 1-D LUTs are unsupported.
    - DOMAIN_MIN / DOMAIN_MAX: assumed 0..1 (true for all standard film LUTs).
      If present and non-default they are silently ignored; this is a documented
      limitation (the user should normalise the LUT before using nazca).
    - RED varies fastest in .cube data, which is the same ordering that
      Color3DLUT expects — so the flat float stream maps directly without
      any axis transposition.
    """
    size: int | None = None
    table: list[float] = []

    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            upper = line.upper()
            if upper.startswith("TITLE"):
                continue
            if upper.startswith("LUT_1D_SIZE"):
                raise ValueError(
                    f"{path}: 1-D LUTs (.cube LUT_1D_SIZE) are not supported — "
                    "provide a 3-D LUT (LUT_3D_SIZE)."
                )
            if upper.startswith("LUT_3D_SIZE"):
                parts = line.split()
                if len(parts) < 2:
                    raise ValueError(f"{path}: malformed LUT_3D_SIZE line: {line!r}")
                size = int(parts[1])
                continue
            if upper.startswith("DOMAIN_MIN") or upper.startswith("DOMAIN_MAX"):
                # Silently ignore domain declarations — we assume 0..1.
                continue
            # Data line: three floats "R G B". Some exporters emit other
            # 3-token keyword lines (e.g. LUT_3D_INPUT_RANGE 0 1); skip any
            # line whose first token isn't numeric rather than failing on it.
            parts = line.split()
            if len(parts) == 3:
                try:
                    table.extend(float(v) for v in parts)
                except ValueError:
                    continue

    if size is None:
        raise ValueError(f"{path}: missing LUT_3D_SIZE — not a valid 3-D .cube file.")
    expected = size**3 * 3
    if len(table) != expected:
        raise ValueError(
            f"{path}: expected {expected} values for a {size}³ LUT but found {len(table)}."
        )
    return ImageFilter.Color3DLUT(size, table, channels=3)


# ---------------------------------------------------------------------------
# HALD CLUT loader
# ---------------------------------------------------------------------------


def load_hald(path: str | Path) -> ImageFilter.Color3DLUT:
    """Load a HALD CLUT PNG and return a Color3DLUT.

    A HALD image encodes a 3-D LUT as a square (or rectangular) grid of RGB
    patches.  The total pixel count must be a perfect cube: total == edge**3.

    Note: 8-bit PNG input gives 8-bit precision (255 discrete steps per
    channel).  This introduces visible banding on subtle gradients compared
    with a 32-bit float .cube file.  Use .cube for high-quality finishing.
    """
    with Image.open(path) as src:
        img = src.convert("RGB")
    w, h = img.size
    total = w * h
    edge = round(total ** (1.0 / 3.0))
    if edge**3 != total:
        raise ValueError(
            f"{path}: pixel count {total} is not a perfect cube — not a valid HALD CLUT image."
        )
    # Normalise 0-255 → 0.0-1.0; flatten (r,g,b) tuples into a single list.
    table = [c / 255.0 for px in img.getdata() for c in px]
    return ImageFilter.Color3DLUT(edge, table, channels=3)


# ---------------------------------------------------------------------------
# LUT resolver
# ---------------------------------------------------------------------------


def load_lut(spec: str) -> ImageFilter.Color3DLUT:
    """Resolve *spec* to a Color3DLUT.

    Resolution order
    ----------------
    1. If *spec* is an existing file path, dispatch by extension:
       - .cube  → load_cube
       - .png   → load_hald
       - other  → ValueError
    2. Otherwise treat *spec* as a NAME and search for
       ``<name>.cube`` then ``<name>.png`` in (in order):
       a. $NAZCA_LUT_DIR       (if the env var is set)
       b. ~/.config/nazca/luts

       # TODO (PR2 / bundled looks): also search the package-internal
       #   looks directory once it is shipped with the wheel.  The hook
       #   belongs here — simply prepend or append to search_dirs below.

    3. If unresolved, raise ValueError listing the directories that were
       searched and any look names available in them.
    """
    # --- path branch ---------------------------------------------------------
    candidate = Path(spec)
    if candidate.exists():
        ext = candidate.suffix.lower()
        if ext == ".cube":
            return load_cube(candidate)
        if ext == ".png":
            return load_hald(candidate)
        raise ValueError(
            f"Unsupported LUT file extension {ext!r} for {spec!r}. "
            "Expected .cube (Adobe/Iridas 3-D) or .png (HALD CLUT)."
        )

    # --- name branch ---------------------------------------------------------
    search_dirs: list[Path] = []
    env_dir = os.environ.get("NAZCA_LUT_DIR")
    if env_dir:
        search_dirs.append(Path(env_dir))
    search_dirs.append(Path.home() / ".config" / "nazca" / "luts")

    # Collect available names for a helpful error message.
    available: list[str] = []
    for d in search_dirs:
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in (".cube", ".png"):
                    available.append(p.stem)

    for d in search_dirs:
        for ext, loader in ((".cube", load_cube), (".png", load_hald)):
            p = d / f"{spec}{ext}"
            if p.exists():
                return loader(p)

    dirs_str = ", ".join(str(d) for d in search_dirs) if search_dirs else "(none)"
    avail_str = (
        "Available looks: " + ", ".join(sorted(set(available)))
        if available
        else "No looks found in those directories."
    )
    raise ValueError(f"LUT {spec!r} not found.\nSearched: {dirs_str}\n{avail_str}")


# ---------------------------------------------------------------------------
# Film grain
# ---------------------------------------------------------------------------


def add_grain(img: Image.Image, intensity: float, size: int = 1) -> Image.Image:
    """Composite a MONOCHROME (luminance) film-grain layer onto *img*.

    Parameters
    ----------
    img:        Source RGB image.
    intensity:  Grain strength in 0..1.  Values <= 0 are a no-op (guarded).
    size:       Grain coarseness: 1 = fine (native pixel), 2-4 = progressively
                coarser (noise is generated at 1/size resolution then upscaled).

    Implementation note — why this can never produce chroma speckle
    ----------------------------------------------------------------
    Noise is generated once as an 'L' (8-bit greyscale) image via
    Image.effect_noise, which produces a single-channel luminance field.
    That single channel value is then replicated identically to R, G, and B
    when we call noise.convert('RGB').  Because R == G == B at every pixel,
    there is NO code path that adds independent per-channel noise — coloured
    chroma speckle is structurally impossible.
    """
    if intensity <= 0:
        return img

    # soft_light is per-channel; ensure a 3-channel base so the public helper
    # is robust if called directly with a non-RGB image (apply_grade already
    # passes RGB).
    img = img.convert("RGB")
    w, h = img.size
    sigma = 16 + intensity * 64  # perceptible but not blown out

    # 1. Generate MONOCHROME noise (L mode, mean ~128).
    noise = Image.effect_noise((max(1, w // size), max(1, h // size)), sigma)

    # 2. Resize back to source dimensions (no-op when size == 1).
    noise = noise.resize((w, h), Image.BILINEAR)

    # 3. Soft-light composite, then blend at low opacity.
    #    noise.convert('RGB') replicates the single L value to all channels —
    #    same delta applied to R, G, B equally → pure luminance shift, no chroma.
    blended = ImageChops.soft_light(img, noise.convert("RGB"))
    opacity = min(0.5, intensity * 0.3)  # ~4% at intensity=0.15, capped at 50%
    return Image.blend(img, blended, opacity)


# ---------------------------------------------------------------------------
# Grade applicator
# ---------------------------------------------------------------------------


def apply_grade(
    img: Image.Image,
    lut: ImageFilter.Color3DLUT,
    strength: float = 1.0,
    grain: float = 0.0,
    grain_size: int = 1,
) -> Image.Image:
    """Apply *lut* to *img* and return the graded image.

    Parameters
    ----------
    img:        Source PIL image (any mode; converted to RGB internally).
    lut:        A Color3DLUT returned by load_cube / load_hald / load_lut.
    strength:   0.0 = no grade (original), 1.0 = full grade.
                Values between blend original ↔ graded linearly.
    grain:      Monochrome film-grain intensity (0.0 = none, 1.0 = heavy).
                Applied after the LUT via add_grain() for luminance-only effect.
    grain_size: Grain coarseness (1 = fine, 2-4 = progressively coarser).

    Returns
    -------
    Graded PIL Image.  RGB for opaque inputs; an alpha channel present on the
    source (RGBA / LA) is preserved and re-attached after grading, so a
    transparent cutout (e.g. from ``nazca image --rmbg``) stays transparent.
    """
    # Preserve transparency: the LUT operates on RGB only, so split the alpha
    # off, grade the colour, then re-attach. Without this, convert("RGB")
    # would flatten a cutout onto black.
    alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else None

    base = img.convert("RGB")
    graded = base.filter(lut)
    if strength < 1.0:
        graded = Image.blend(base, graded, strength)
    if grain > 0:
        graded = add_grain(graded, grain, grain_size)
    if alpha is not None:
        graded.putalpha(alpha)
    return graded
