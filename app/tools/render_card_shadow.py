"""Render the Nexus glass skins and their hover frames as RGBA PNGs."""

import math
from pathlib import Path
import struct
import zlib


ASSETS = Path(__file__).resolve().parents[1] / "assets"


def rgb(color):
    return tuple(bytes.fromhex(color))


def blend(first, second, amount):
    return tuple(round(a + (b - a) * amount) for a, b in zip(first, second))


def clamp(value):
    return min(1.0, max(0.0, value))


def rounded_distance(x, y, cx, cy, half_width, half_height, radius):
    dx = abs(x - cx) - (half_width - radius)
    dy = abs(y - cy) - (half_height - radius)
    return math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0) - radius


def write_png(path, width, height, pixel):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(pixel(x + .5, y + .5))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def render_skin(path, width, height, padding, radius, palette, *, lift=0, inset=False):
    bright, face, shade, rim, highlight, shadow_color = map(rgb, palette)
    cx, cy = width / 2, height / 2
    half_width, half_height = cx - padding, cy - padding
    shadow_strength = (.055 if inset else .035) + .055 * lift
    shadow_offset = 2 if inset else 4 + lift * 3
    spread = 3.0 if inset else 5.8 + lift * 1.2

    def pixel(x, y):
        distance = rounded_distance(x, y, cx, cy, half_width, half_height, radius)
        coverage = clamp(.5 - distance)
        depth = distance - shadow_offset * clamp((y - padding) / (height - 2 * padding))
        shadow = shadow_strength * math.exp(-(max(0, depth) / spread) ** 2 / 2)
        t = clamp((y - padding) / (height - 2 * padding))
        # The vertical sheen survives stretching the middle row of the nine-slice.
        body = blend(face, bright, math.exp(-t * 9) * .85)
        body = blend(body, shade, t ** 1.7)
        body = blend(body, highlight, lift * .045)
        edge = blend(highlight, rim, t ** .36)
        body = blend(body, edge, clamp(distance + 1.65))
        material = coverage * .95
        alpha = material + shadow * (1 - material)
        if alpha < 1 / 255:
            return 0, 0, 0, 0
        return (*blend(shadow_color, body, material / alpha), round(255 * alpha))

    write_png(path, width, height, pixel)


def render_header(path, tint, rim):
    tint, rim = rgb(tint), rgb(rim)

    def pixel(x, y):
        halo = clamp(1 - math.hypot((x - 29) / 246, (y - 5) / 77))
        glint = clamp(1 - abs(y - 21 - (x - 25) * .08) / 26) * halo
        alpha = round(151 * halo ** 2 + 37 * glint)
        return (*blend(tint, rim, glint), alpha)

    write_png(path, 274, 84, pixel)


def main():
    palettes = (
        (
            ASSETS,
            ("ffffff", "fcfcfd", "fafafc", "e8e9ed", "ffffff", "585c68"),
            ("fcfdff", "eeeef7", "f5f5fa", "d9d9e8", "ffffff", "666681"),
            ("dcdff5", "fcfdff"),
        ),
        (
            ASSETS / "dark",
            ("282a30", "25262c", "24252a", "30323a", "393b44", "08090c"),
            ("41414e", "30303a", "272730", "494958", "626273", "101014"),
            ("32323d", "454556"),
        ),
    )
    for folder, card, product, header in palettes:
        render_skin(folder / "card-surface.png", 161, 161, 32, 20, card)
        for index in range(1, 6):
            render_skin(folder / f"card-hover-{index}.png", 161, 161, 32, 20, card, lift=index / 5)
        render_skin(folder / "product-surface.png", 121, 107, 12, 16, product, inset=True)
        render_header(folder / "ambient-header.png", *header)


if __name__ == "__main__":
    main()
