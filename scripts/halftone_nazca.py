"""Draw a stylized Nazca hummingbird and render it as halftone ASCII.

No API calls, no spend — pure Pillow. Brightness → character-density ramp.
Run: python scripts/halftone_nazca.py [width]
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw

# light → dark ink ramp (space = background, @ = densest figure)
RAMP = " .'`:-=+*oexk#%X8&WM@"

SS = 4  # supersample factor for smooth (gradient) edges


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
    cols = int(sys.argv[1]) if len(sys.argv) > 1 else 76
    print(to_halftone(draw_hummingbird(), cols))
