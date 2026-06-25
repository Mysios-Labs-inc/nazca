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


def test_apply_grade_grain_params_accepted_noop(tmp_path):
    """apply_grade accepts grain and grain_size params (no-op in PR1)."""
    grade = _import_grade()
    cube_path = tmp_path / "identity.cube"
    _write_identity_cube_2x2x2(cube_path)
    lut = grade.load_cube(cube_path)

    test_img = Image.new("RGB", (10, 10), (128, 128, 128))
    # Should not raise; grain is threaded but not implemented yet
    result = grade.apply_grade(test_img, lut, strength=1.0, grain=0.5, grain_size=2)
    assert result is not None


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
