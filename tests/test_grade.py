"""PR1 grading module: load_cube, load_hald, load_lut, apply_grade — NO grain, NO crop."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageFilter


# Lazy import to allow test collection without nazca.grade existing yet
def _import_grade():
    from nazca import grade

    return grade


# ─────────────────────────────────────────────────────────────────── Helper: .cube writers


def _write_identity_cube_2x2x2(path: Path) -> None:
    """Write a 2x2x2 identity .cube (8 corner RGB triples: 0/1 permutations)."""
    lines = ["# Identity LUT 2x2x2", "LUT_3D_SIZE 2"]
    # 2x2x2 cube with R varying fastest, so the order is:
    # (0,0,0), (1,0,0), (0,1,0), (1,1,0), (0,0,1), (1,0,1), (0,1,1), (1,1,1)
    for b in (0, 1):
        for g in (0, 1):
            for r in (0, 1):
                lines.append(f"{r} {g} {b}")
    path.write_text("\n".join(lines) + "\n")


def _write_channel_swap_cube_2x2x2(path: Path) -> None:
    """Write a 2x2x2 .cube that swaps R and G channels (R, G, B -> G, R, B)."""
    lines = ["# Channel swap LUT 2x2x2", "LUT_3D_SIZE 2"]
    # Swap R and G: input (r, g, b) -> output (g, r, b)
    for b in (0, 1):
        for g in (0, 1):
            for r in (0, 1):
                lines.append(f"{g} {r} {b}")  # Swapped R and G
    path.write_text("\n".join(lines) + "\n")


def _write_malformed_cube(path: Path) -> None:
    """Write a .cube with wrong number of floats (6 instead of 8)."""
    lines = [
        "# Malformed cube",
        "LUT_3D_SIZE 2",
        "0 0 0",
        "1 0 0",
        "0 1 0",
        "1 1 0",
        "0 0 1",
        "1 0 1",
        # Missing last line
    ]
    path.write_text("\n".join(lines) + "\n")


def _write_1d_lut_file(path: Path) -> None:
    """Write a .cube with LUT_1D_SIZE (unsupported in PR1)."""
    lines = [
        "# 1D LUT",
        "LUT_1D_SIZE 2",
        "0 0 0",
        "1 1 1",
    ]
    path.write_text("\n".join(lines) + "\n")


def _write_identity_hald_4x4(path: Path) -> None:
    """Write a 4x4 PNG (16 pixels, not a perfect cube — edge^3 ≠ 16).

    Also write a valid identity HALD: 8x8 PNG (64 pixels = 4^3).
    Here we write a valid one: edge=4, so 4x4x4 = 64 pixels -> 8x8 PNG.
    """
    # For an identity HALD with edge=4, we need 64 pixels arranged as 8x8
    # Each pixel represents (r, g, b) in the range [0..255]
    edge = 4
    size = edge**3  # 64
    side = int(size**0.5)  # 8
    assert side * side == size

    # Create an identity HALD: pixel value at (i, j) encodes a color in the LUT
    pixels = []
    for idx in range(size):
        # For identity, pixel value = scaled index
        # HALD ordering: iterate through the LUT table in order
        r = (idx // (edge * edge)) % edge
        g = (idx // edge) % edge
        b = idx % edge
        # Scale to 0..255
        pixels.append((r * 255 // (edge - 1), g * 255 // (edge - 1), b * 255 // (edge - 1)))

    img = Image.new("RGB", (side, side))
    img.putdata(pixels)
    img.save(path)


def _write_invalid_hald_7x7(path: Path) -> None:
    """Write a 7x7 PNG (49 pixels, not a perfect cube — 3^3=27, 4^3=64)."""
    img = Image.new("RGB", (7, 7), (100, 100, 100))
    img.save(path)


# ─────────────────────────────────────────────────────────────────── Tests: load_cube


def test_load_cube_identity_2x2x2(tmp_path):
    """Identity .cube round-trip: write identity 2x2x2, load, apply to solid image, assert ≈ identity."""
    grade = _import_grade()
    cube_path = tmp_path / "identity_2x2x2.cube"
    _write_identity_cube_2x2x2(cube_path)

    lut = grade.load_cube(cube_path)
    assert isinstance(lut, ImageFilter.Color3DLUT)
    assert lut.size[0] == 2

    # Create a solid-color test image (mid-gray)
    test_img = Image.new("RGB", (10, 10), (128, 128, 128))
    graded = test_img.filter(lut)

    # For identity LUT, output ≈ input (allow ±1 per channel for interpolation)
    graded_px = graded.getpixel((5, 5))
    # Interpolation may shift by 1–2 levels, so we allow ±2 tolerance
    assert all(abs(graded_px[i] - 128) <= 2 for i in range(3)), (
        f"Expected ≈(128,128,128), got {graded_px}"
    )


def test_load_cube_channel_swap_2x2x2(tmp_path):
    """Non-identity .cube (channel swap) changes pixels as expected."""
    grade = _import_grade()
    cube_path = tmp_path / "swap_2x2x2.cube"
    _write_channel_swap_cube_2x2x2(cube_path)

    lut = grade.load_cube(cube_path)
    assert lut.size[0] == 2

    # Create a test image with distinct R and G (red-ish)
    test_img = Image.new("RGB", (10, 10), (200, 100, 50))
    graded = test_img.filter(lut)

    # After swap, (200, 100, 50) -> (100, 200, 50) (R and G swapped)
    graded_px = graded.getpixel((5, 5))
    # Allow ±2 for interpolation
    assert abs(graded_px[0] - 100) <= 2, f"R channel swapped: expected ≈100, got {graded_px[0]}"
    assert abs(graded_px[1] - 200) <= 2, f"G channel swapped: expected ≈200, got {graded_px[1]}"
    assert abs(graded_px[2] - 50) <= 2, f"B channel unchanged: expected ≈50, got {graded_px[2]}"


def test_load_cube_malformed_raises(tmp_path):
    """load_cube raises ValueError on wrong float count."""
    grade = _import_grade()
    cube_path = tmp_path / "malformed.cube"
    _write_malformed_cube(cube_path)

    with pytest.raises(ValueError, match="8|LUT|3D"):
        grade.load_cube(cube_path)


def test_load_cube_1d_lut_raises(tmp_path):
    """load_cube raises ValueError on LUT_1D_SIZE (unsupported)."""
    grade = _import_grade()
    cube_path = tmp_path / "1d.cube"
    _write_1d_lut_file(cube_path)

    with pytest.raises(ValueError, match="1D|unsupported"):
        grade.load_cube(cube_path)


# ─────────────────────────────────────────────────────────────────── Tests: load_hald


def test_load_hald_identity_8x8(tmp_path):
    """load_hald: synthesize valid identity HALD, load and apply ≈ identity."""
    grade = _import_grade()
    hald_path = tmp_path / "identity.png"
    _write_identity_hald_4x4(hald_path)

    lut = grade.load_hald(hald_path)
    assert isinstance(lut, ImageFilter.Color3DLUT)
    assert lut.size[0] == 4

    # Apply to a solid-color image
    test_img = Image.new("RGB", (10, 10), (128, 128, 128))
    graded = test_img.filter(lut)

    graded_px = graded.getpixel((5, 5))
    # Allow ±2 for interpolation (8-bit PNG precision)
    assert all(abs(graded_px[i] - 128) <= 2 for i in range(3)), (
        f"Expected ≈(128,128,128), got {graded_px}"
    )


def test_load_hald_non_cube_pixel_count_raises(tmp_path):
    """load_hald raises ValueError if pixel count is not a perfect cube."""
    grade = _import_grade()
    hald_path = tmp_path / "invalid.png"
    _write_invalid_hald_7x7(hald_path)

    with pytest.raises(ValueError, match="cube|HALD"):
        grade.load_hald(hald_path)


# ─────────────────────────────────────────────────────────────────── Tests: apply_grade


def test_apply_grade_strength_0_preserves_image(tmp_path):
    """apply_grade with strength=0.0 returns ≈ original image."""
    grade = _import_grade()
    cube_path = tmp_path / "identity.cube"
    _write_identity_cube_2x2x2(cube_path)
    lut = grade.load_cube(cube_path)

    # Even if we had a non-identity LUT, strength=0 should preserve original
    test_img = Image.new("RGB", (10, 10), (100, 150, 200))
    result = grade.apply_grade(test_img, lut, strength=0.0)

    result_px = result.getpixel((5, 5))
    assert result_px == (100, 150, 200), f"strength=0 should preserve image, got {result_px}"


def test_apply_grade_strength_1_applies_full_grade(tmp_path):
    """apply_grade with strength=1.0 returns fully graded image."""
    grade = _import_grade()
    cube_path = tmp_path / "swap.cube"
    _write_channel_swap_cube_2x2x2(cube_path)
    lut = grade.load_cube(cube_path)

    test_img = Image.new("RGB", (10, 10), (200, 100, 50))
    result = grade.apply_grade(test_img, lut, strength=1.0)

    result_px = result.getpixel((5, 5))
    # After full swap: (200, 100, 50) -> (100, 200, 50)
    assert abs(result_px[0] - 100) <= 2, f"Expected R ≈100, got {result_px[0]}"
    assert abs(result_px[1] - 200) <= 2, f"Expected G ≈200, got {result_px[1]}"


def test_apply_grade_strength_intermediate(tmp_path):
    """apply_grade with strength between 0 and 1 blends original and graded."""
    grade = _import_grade()
    cube_path = tmp_path / "swap.cube"
    _write_channel_swap_cube_2x2x2(cube_path)
    lut = grade.load_cube(cube_path)

    test_img = Image.new("RGB", (10, 10), (200, 100, 50))
    result = grade.apply_grade(test_img, lut, strength=0.5)

    result_px = result.getpixel((5, 5))
    # Blend: 0.5 * original + 0.5 * graded = 0.5 * (200, 100, 50) + 0.5 * (100, 200, 50)
    # = (150, 150, 50)
    expected = (150, 150, 50)
    for i in range(3):
        assert abs(result_px[i] - expected[i]) <= 3, (
            f"Channel {i}: expected ≈{expected[i]}, got {result_px[i]}"
        )


def test_apply_grade_grain_params_threaded(tmp_path):
    """apply_grade threads grain/grain_size into add_grain, staying chroma-neutral."""
    grade = _import_grade()
    cube_path = tmp_path / "identity.cube"
    _write_identity_cube_2x2x2(cube_path)
    lut = grade.load_cube(cube_path)

    test_img = Image.new("RGB", (10, 10), (128, 128, 128))
    result = grade.apply_grade(test_img, lut, strength=1.0, grain=0.5, grain_size=2)
    assert result is not None
    # Grain is monochrome: a grey input must stay perfectly neutral (no chroma speckle).
    assert max(max(px) - min(px) for px in result.getdata()) == 0


# ─────────────────────────────────────────────────────────────────── Tests: load_lut


def test_load_lut_with_env_var_resolves_name(tmp_path, monkeypatch):
    """load_lut: with NAZCA_LUT_DIR set, resolves spec as name."""
    grade = _import_grade()
    lut_dir = tmp_path / "luts"
    lut_dir.mkdir()

    cube_path = lut_dir / "foo.cube"
    _write_identity_cube_2x2x2(cube_path)

    monkeypatch.setenv("NAZCA_LUT_DIR", str(lut_dir))

    lut = grade.load_lut("foo")
    assert isinstance(lut, ImageFilter.Color3DLUT)


def test_load_lut_with_explicit_path(tmp_path):
    """load_lut: spec as absolute path loads directly (no name search)."""
    grade = _import_grade()
    cube_path = tmp_path / "my_lut.cube"
    _write_identity_cube_2x2x2(cube_path)

    lut = grade.load_lut(str(cube_path))
    assert isinstance(lut, ImageFilter.Color3DLUT)


def test_load_lut_dispatch_by_extension_cube(tmp_path):
    """load_lut: .cube extension dispatches to load_cube."""
    grade = _import_grade()
    cube_path = tmp_path / "test.cube"
    _write_identity_cube_2x2x2(cube_path)

    lut = grade.load_lut(str(cube_path))
    assert lut.size[0] == 2


def test_load_lut_dispatch_by_extension_png(tmp_path):
    """load_lut: .png extension dispatches to load_hald."""
    grade = _import_grade()
    hald_path = tmp_path / "test.png"
    _write_identity_hald_4x4(hald_path)

    lut = grade.load_lut(str(hald_path))
    assert lut.size[0] == 4


def test_load_lut_missing_raises_with_search_paths(tmp_path, monkeypatch):
    """load_lut('missing') raises ValueError listing searched dirs."""
    grade = _import_grade()
    lut_dir = tmp_path / "luts"
    lut_dir.mkdir()

    monkeypatch.setenv("NAZCA_LUT_DIR", str(lut_dir))
    monkeypatch.setenv("HOME", str(tmp_path))  # Override HOME for ~/.config/nazca/luts

    with pytest.raises(ValueError) as exc_info:
        grade.load_lut("missing")

    error_msg = str(exc_info.value)
    assert "missing" in error_msg.lower() or "not found" in error_msg.lower()
    # Message should list the directories searched
    assert str(lut_dir) in error_msg or "NAZCA_LUT_DIR" in error_msg


def test_load_lut_searches_home_config_fallback(tmp_path, monkeypatch):
    """load_lut: searches ~/.config/nazca/luts if NAZCA_LUT_DIR not set."""
    grade = _import_grade()
    home = tmp_path / "home"
    home.mkdir()
    config_dir = home / ".config" / "nazca" / "luts"
    config_dir.mkdir(parents=True)

    cube_path = config_dir / "fallback.cube"
    _write_identity_cube_2x2x2(cube_path)

    monkeypatch.setenv("HOME", str(home))
    # Unset NAZCA_LUT_DIR so it falls back to ~/.config
    monkeypatch.delenv("NAZCA_LUT_DIR", raising=False)

    lut = grade.load_lut("fallback")
    assert isinstance(lut, ImageFilter.Color3DLUT)


def test_load_lut_invalid_extension_raises(tmp_path):
    """load_lut: rejects paths with unsupported extensions."""
    grade = _import_grade()
    invalid_path = tmp_path / "test.jpg"
    invalid_path.write_text("fake")

    with pytest.raises(ValueError, match="jpg|extension|supported"):
        grade.load_lut(str(invalid_path))


# ─────────────────────────────────────────── Tests: add_grain (luminance-only guarantee)


def test_add_grain_grey_stays_neutral(tmp_path):
    """REGRESSION GUARD: add_grain on solid grey produces zero chroma variance.

    This is the core guarantee: monochrome grain CANNOT produce colored speckles.
    Test passes a mid-grey image through add_grain and verifies that every
    pixel (R, G, B) remains balanced (max per-pixel chroma delta = 0).
    """
    grade = _import_grade()

    # Create solid grey image (neutral, no color cast)
    grey_img = Image.new("RGB", (100, 100), (120, 120, 120))
    result = grade.add_grain(grey_img, intensity=0.4, size=1)

    # Chroma neutrality: for each pixel, max(R, G, B) - min(R, G, B) == 0
    # (no color difference means all three channels are identical)
    max_chroma_delta = max(max(px) - min(px) for px in result.getdata())
    assert max_chroma_delta == 0, (
        f"Chroma speckle detected: max delta {max_chroma_delta} "
        "(luminance grain on grey must stay neutral)"
    )


def test_add_grain_grey_via_apply_grade_stays_neutral(tmp_path):
    """REGRESSION GUARD: apply_grade with grain on grey stays chroma-neutral.

    Covers the full wired path: identity LUT + grain applied after grade.
    """
    grade = _import_grade()
    cube_path = tmp_path / "identity.cube"
    _write_identity_cube_2x2x2(cube_path)
    lut = grade.load_cube(cube_path)

    grey_img = Image.new("RGB", (100, 100), (120, 120, 120))
    result = grade.apply_grade(grey_img, lut, strength=1.0, grain=0.4, grain_size=1)

    max_chroma_delta = max(max(px) - min(px) for px in result.getdata())
    assert max_chroma_delta == 0, (
        f"apply_grade path: chroma delta {max_chroma_delta} "
        "(identity LUT + grain must preserve neutrality)"
    )


def test_add_grain_zero_is_noop():
    """add_grain(img, intensity=0.0) returns img unchanged."""
    grade = _import_grade()

    test_img = Image.new("RGB", (50, 50), (100, 150, 200))
    result = grade.add_grain(test_img, intensity=0.0)

    # Should be identical (same pixels)
    assert result.tobytes() == test_img.tobytes(), "grain=0.0 should be a no-op"


def test_apply_grade_grain_zero_noop(tmp_path):
    """apply_grade with grain=0.0 equals result without grain."""
    grade = _import_grade()
    cube_path = tmp_path / "identity.cube"
    _write_identity_cube_2x2x2(cube_path)
    lut = grade.load_cube(cube_path)

    test_img = Image.new("RGB", (50, 50), (100, 150, 200))

    # Two paths: with grain=0 and without grain
    result_with_zero = grade.apply_grade(test_img, lut, strength=1.0, grain=0.0)
    result_no_grain = grade.apply_grade(test_img, lut, strength=1.0)

    # Both should be identical
    assert result_with_zero.tobytes() == result_no_grain.tobytes(), (
        "grain=0.0 should produce same result as no grain parameter"
    )


def test_add_grain_actually_adds_variance():
    """add_grain(img, intensity=0.5) on grey produces luminance variance.

    Verify that grain is not a silent no-op: the set of distinct pixel values
    should have more than one element (because noise is applied).
    """
    grade = _import_grade()

    # Mid-grey image
    grey_img = Image.new("RGB", (200, 200), (128, 128, 128))
    result = grade.add_grain(grey_img, intensity=0.5)

    # Collect all distinct grey values across all pixels
    # (all channels are identical per our luminance-only guarantee)
    grey_values = set(px[0] for px in result.getdata())

    # Grain should produce variance; more than one distinct grey level
    assert len(grey_values) > 1, (
        f"Grain produced no variance (only {len(grey_values)} distinct value). "
        "Grain not applied?"
    )


def test_add_grain_size_coarser(tmp_path):
    """add_grain with size > 1 produces coarser grain; output size unchanged."""
    grade = _import_grade()

    grey_img = Image.new("RGB", (100, 100), (128, 128, 128))

    # Coarse grain (size=3)
    result_coarse = grade.add_grain(grey_img, intensity=0.4, size=3)

    # Must be same size as input
    assert result_coarse.size == grey_img.size, (
        f"add_grain must preserve image size, got {result_coarse.size}"
    )

    # Verify chroma remains neutral
    max_chroma_delta = max(max(px) - min(px) for px in result_coarse.getdata())
    assert max_chroma_delta == 0, (
        f"Coarse grain (size=3) produced chroma delta {max_chroma_delta}"
    )


# ─────────────────────────────────────────── Tests: crop_to_preset (format/crop)


def test_crop_to_preset_9_16_from_square():
    """Aspect correctness: 1000x1000 cropped to 9:16 yields width/height ≈ 9/16."""
    grade = _import_grade()

    # 1000x1000 square
    img = Image.new("RGB", (1000, 1000), (50, 50, 50))
    result = grade.crop_to_preset(img, "9:16")

    # 9:16 aspect = 0.5625; from 1000x1000, width is wider, so trim width
    # new_w = round(1000 * 0.5625) = 562
    # output: 562x1000
    assert result.size[0] <= 1000 and result.size[1] <= 1000, (
        f"Never upscale: got {result.size}"
    )
    aspect = result.size[0] / result.size[1]
    target_aspect = 9 / 16
    assert abs(aspect - target_aspect) < 0.02, (
        f"Aspect mismatch: expected ≈{target_aspect:.4f}, got {aspect:.4f}"
    )


def test_crop_to_preset_4_5_from_square():
    """Aspect correctness: 1000x1000 cropped to 4:5 yields width/height ≈ 4/5."""
    grade = _import_grade()

    img = Image.new("RGB", (1000, 1000), (50, 50, 50))
    result = grade.crop_to_preset(img, "4:5")

    aspect = result.size[0] / result.size[1]
    target_aspect = 4 / 5
    assert abs(aspect - target_aspect) < 0.02, (
        f"Aspect mismatch: expected ≈{target_aspect:.4f}, got {aspect:.4f}"
    )


def test_crop_to_preset_1_1_from_wide():
    """Aspect correctness: 1000x600 cropped to 1:1 yields square w==h."""
    grade = _import_grade()

    img = Image.new("RGB", (1000, 600), (50, 50, 50))
    result = grade.crop_to_preset(img, "1:1")

    assert result.size[0] == result.size[1], (
        f"1:1 preset must yield square, got {result.size}"
    )
    aspect = result.size[0] / result.size[1]
    assert abs(aspect - 1.0) < 0.02


def test_crop_to_preset_2_3_and_16_9():
    """Aspect correctness: verify 2:3 and 16:9 presets."""
    grade = _import_grade()

    img = Image.new("RGB", (1000, 1000), (50, 50, 50))

    # 2:3 aspect
    result_2_3 = grade.crop_to_preset(img, "2:3")
    aspect_2_3 = result_2_3.size[0] / result_2_3.size[1]
    target_2_3 = 2 / 3
    assert abs(aspect_2_3 - target_2_3) < 0.02

    # 16:9 aspect
    result_16_9 = grade.crop_to_preset(img, "16:9")
    aspect_16_9 = result_16_9.size[0] / result_16_9.size[1]
    target_16_9 = 16 / 9
    assert abs(aspect_16_9 - target_16_9) < 0.02


def test_crop_to_preset_never_upscales():
    """Never upscale: output width <= input width AND output height <= input height."""
    grade = _import_grade()

    presets = ["9:16", "4:5", "1:1", "2:3", "16:9"]
    test_sizes = [(1000, 1000), (1000, 600), (600, 1000), (800, 600), (1200, 400)]

    for preset in presets:
        for w, h in test_sizes:
            img = Image.new("RGB", (w, h), (50, 50, 50))
            result = grade.crop_to_preset(img, preset)
            assert result.size[0] <= w, (
                f"Preset {preset} from {(w, h)}: output width {result.size[0]} > input {w}"
            )
            assert result.size[1] <= h, (
                f"Preset {preset} from {(w, h)}: output height {result.size[1]} > input {h}"
            )


def _make_row_gradient(w: int, h: int) -> Image.Image:
    """Return an RGB image where row y has pixel value (y//4, y//4, y//4).

    This encodes the source row index into every pixel, making it possible to
    verify exactly which vertical window a crop kept by inspecting the result's
    top pixel: result.getpixel((0, 0))[0] * 4 ≈ source_y_kept.
    """
    data = [(y // 4,) * 3 for y in range(h) for _ in range(w)]
    img = Image.new("RGB", (w, h))
    img.putdata(data)
    return img


def test_crop_to_preset_gravity_north():
    """Gravity north: 600x1000 crop to 1:1 keeps rows 0..599 (top, y=0).

    Uses a row-gradient so the result top pixel encodes which source row was
    kept.  A swapped or broken anchor would produce a different value and fail.
    """
    grade = _import_grade()

    # Row y has pixel (y//4, y//4, y//4).
    # crop to 1:1: new_h = round(600/1) = 600.
    # north → y=0 → result (0,0) = source (0,0) → value = 0//4 = 0.
    img = _make_row_gradient(600, 1000)
    result = grade.crop_to_preset(img, "1:1", gravity="north")

    assert result.size == (600, 600)
    top_val = result.getpixel((0, 0))[0]  # encoded source-row // 4
    # north starts at source row 0 → encoded value 0; allow ±1 for rounding
    assert top_val <= 1, (
        f"North anchor must keep the top (source row 0); "
        f"got top_val={top_val} (≈ source row {top_val * 4}), expected ≈0"
    )


def test_crop_to_preset_gravity_south():
    """Gravity south: 600x1000 crop to 1:1 keeps rows 400..999 (bottom, y=400).

    A north or center anchor produces top_val ≈ 0 or 50 respectively — both
    fail the assertion that top_val must be close to 100 (source row 400).
    """
    grade = _import_grade()

    # south → y = 1000-600 = 400 → result (0,0) = source (0,400) → value = 400//4 = 100.
    img = _make_row_gradient(600, 1000)
    result = grade.crop_to_preset(img, "1:1", gravity="south")

    assert result.size == (600, 600)
    top_val = result.getpixel((0, 0))[0]
    # south starts at source row 400 → encoded value 100; allow ±1
    assert abs(top_val - 100) <= 1, (
        f"South anchor must keep the bottom window (source row 400); "
        f"got top_val={top_val} (≈ source row {top_val * 4}), expected ≈100"
    )


def test_crop_to_preset_gravity_center():
    """Gravity center: 600x1000 crop to 1:1 keeps rows 200..799 (middle, y=200).

    A north anchor gives top_val ≈ 0, south gives ≈ 100 — both distinct from
    the expected ≈ 50, so any wrong anchor fails this test.
    """
    grade = _import_grade()

    # center → y = (1000-600)//2 = 200 → result (0,0) = source (0,200) → value = 200//4 = 50.
    img = _make_row_gradient(600, 1000)
    result = grade.crop_to_preset(img, "1:1", gravity="center")

    assert result.size == (600, 600)
    top_val = result.getpixel((0, 0))[0]
    # center starts at source row 200 → encoded value 50; allow ±1
    assert abs(top_val - 50) <= 1, (
        f"Center anchor must keep the middle window (source row 200); "
        f"got top_val={top_val} (≈ source row {top_val * 4}), expected ≈50"
    )


def test_crop_to_preset_horizontal_centered():
    """Horizontal trim is centered: wide image from 1000x500 to 1:1 trims width symmetrically."""
    grade = _import_grade()

    # 1000x500 wide image, crop to 1:1 (trims width from 1000 to 500)
    # width centering: x = (1000 - 500) // 2 = 250
    img = Image.new("RGB", (1000, 500))

    # Left 250px (red, trimmed), middle 500px (green, kept), right 250px (red, trimmed)
    for y in range(500):
        for x in range(250):
            img.putpixel((x, y), (200, 0, 0))  # red left (trimmed)
    for y in range(500):
        for x in range(250, 750):
            img.putpixel((x, y), (0, 200, 0))  # green middle (kept)
    for y in range(500):
        for x in range(750, 1000):
            img.putpixel((x, y), (200, 0, 0))  # red right (trimmed)

    result = grade.crop_to_preset(img, "1:1")

    # Result should be 500x500, mostly green
    assert result.size == (500, 500)
    green_count = sum(
        1 for px in result.getdata() if px[1] > px[0] and px[1] > px[2]
    )
    assert green_count > 500 * 500 * 0.9, (
        f"Horizontal centering: most of output should be green, got {green_count} green pixels"
    )


def test_crop_to_preset_invalid_preset_raises():
    """Invalid preset name raises ValueError with helpful message."""
    grade = _import_grade()

    img = Image.new("RGB", (100, 100), (50, 50, 50))

    with pytest.raises(ValueError, match="invalid|preset|9:16|4:5"):
        grade.crop_to_preset(img, "invalid_preset")


def test_crop_to_preset_already_matching_aspect():
    """Image already matching target aspect is not cropped."""
    grade = _import_grade()

    # Create a 900x1600 image (9:16 aspect)
    img = Image.new("RGB", (900, 1600), (50, 50, 50))

    result = grade.crop_to_preset(img, "9:16")

    # Should return unchanged (box is the full frame)
    assert result.size == (900, 1600)
