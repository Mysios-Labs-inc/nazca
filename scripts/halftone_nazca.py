"""Render a Nazca hummingbird as halftone ASCII (brightness → density ramp).

No API calls, no spend — pure Pillow.
  python scripts/halftone_nazca.py [width]                 # built-in drawing
  python scripts/halftone_nazca.py --image PATH [width]    # from a real photo/line-art
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageFilter, ImageOps

# light → dark ink ramp (space = background, @ = densest figure)
RAMP = " .'`:-=+*oexk#%X8&WM@"

SS = 4  # supersample factor for smooth (gradient) edges


def from_image(path: str, cols: int) -> Image.Image:
    """Load black-line-on-white art, return grayscale with the FIGURE bright.

    Invert (lines → bright), threshold to crisp ink, then dilate enough to fuse
    a double outline (Nazca strokes are drawn as parallel line pairs) into solid
    strokes that survive the downscale to character resolution.
    """
    img = ImageOps.autocontrast(ImageOps.grayscale(Image.open(path)))
    img = ImageOps.invert(img)                       # black lines → white ink
    img = img.point(lambda p: 255 if p > 80 else 0)  # crisp threshold
    w, _ = img.size
    k = min(21, max(3, int(w / max(cols, 1) * 1.3) | 1))  # ~1.3 cells, odd
    img = img.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MaxFilter(k))
    return img


def draw_hummingbird() -> Image.Image:
    """Top-view Nazca hummingbird: long beak up, spread wings, comb tail."""
    W, H = 260 * SS, 300 * SS
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    cx = W // 2

    def s(v: float) -> float:
        return v * SS

    # long straight beak (the signature) — thin tapering wedge pointing up
    d.polygon([(cx - s(3), s(70)), (cx + s(3), s(70)),
               (cx + s(1.5), s(8)), (cx - s(1.5), s(8))], fill=255)
    # head
    d.ellipse([cx - s(16), s(66), cx + s(16), s(98)], fill=255)
    # body
    d.ellipse([cx - s(22), s(92), cx + s(22), s(196)], fill=255)

    # wings — long swept blades spreading down-out from the shoulders
    d.polygon([(cx - s(10), s(108)), (cx - s(118), s(150)),
               (cx - s(120), s(172)), (cx - s(14), s(150))], fill=255)
    d.polygon([(cx + s(10), s(108)), (cx + s(118), s(150)),
               (cx + s(120), s(172)), (cx + s(14), s(150))], fill=255)

    # tail — fan/comb of straight feathers
    n = 9
    for i in range(n):
        t = (i / (n - 1)) - 0.5            # -0.5 .. 0.5
        fx = cx + t * s(70)
        spread = t * s(34)
        d.polygon([(fx - s(3), s(190)), (fx + s(3), s(190)),
                   (fx + spread + s(2.5), s(292)),
                   (fx + spread - s(2.5), s(292))], fill=255)

    # eye (notch out a dark dot so the head reads)
    d.ellipse([cx - s(5), s(78), cx + s(5), s(88)], fill=0)
    return img


def to_halftone(img: Image.Image, cols: int) -> str:
    # terminal cells are ~2x taller than wide → squash vertically
    w, h = img.size
    rows = max(1, int(cols * (h / w) * 0.5))
    small = img.resize((cols, rows), Image.LANCZOS)
    px = small.load()
    lines = []
    for y in range(rows):
        row = []
        for x in range(cols):
            lum = px[x, y]
            row.append(RAMP[lum * (len(RAMP) - 1) // 255])
        lines.append("".join(row).rstrip())
    # trim fully blank top/bottom lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--image":
        path = args[1]
        cols = int(args[2]) if len(args) > 2 else 90
        img = from_image(path, cols)
    else:
        cols = int(args[0]) if args else 76
        img = draw_hummingbird()
    print(to_halftone(img, cols))
