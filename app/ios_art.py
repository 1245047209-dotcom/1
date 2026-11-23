"""iOS-style artwork rendered at the destination pixel size (stdlib only).

Tk on Windows draws arcs and ovals without antialiasing, so every curved shape
on the iOS layout (battery rings, grouped-list corners, charging badges) is
rendered here as an RGBA PNG with per-pixel edge coverage.
"""
from __future__ import annotations

from functools import lru_cache
import math
import struct
import zlib

TAU = 2 * math.pi
# Normalised lightning bolt (unit radius, y grows downwards).
BOLT = ((.18, -.78), (-.46, .1), (-.04, .1), (-.18, .78), (.46, -.1), (.04, -.1))


def rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def png(width, height, scanlines, level=1):
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(scanlines), level)) + chunk(b"IEND", b""))


def _cover(distance):
    """Coverage of a pixel whose centre is ``distance`` px outside a shape edge."""
    return 0. if distance >= .5 else 1. if distance <= -.5 else .5 - distance


def _inside(points, x, y):
    inside = False
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class _Layers:
    """Premultiplied 'over' compositing for one pixel."""
    __slots__ = ("r", "g", "b", "a")

    def __init__(self):
        self.r = self.g = self.b = self.a = 0.

    def over(self, color, coverage):
        if coverage <= 0:
            return
        keep = 1 - coverage
        self.r = color[0] * coverage + self.r * keep
        self.g = color[1] * coverage + self.g * keep
        self.b = color[2] * coverage + self.b * keep
        self.a = coverage + self.a * keep

    def bytes(self):
        if self.a <= 0:
            return 0, 0, 0, 0
        return (round(self.r / self.a), round(self.g / self.a), round(self.b / self.a), round(self.a * 255))


@lru_cache(maxsize=48)
def _corner(radius):
    """Alpha of a radius x radius top-left corner tile."""
    rows = []
    for y in range(radius):
        row = bytearray()
        for x in range(radius):
            distance = math.hypot(radius - (x + .5), radius - (y + .5)) - radius
            row.append(round(255 * _cover(distance)))
        rows.append(bytes(row))
    return tuple(rows)


@lru_cache(maxsize=48)
def rounded_surface_png(width, height, radius, color, background=None):
    """Rounded rectangle with antialiased corners; middle rows are copied.

    With ``background`` the corners are blended onto that colour and the PNG is fully
    opaque. Tk on Windows composites partially transparent photos by reading the
    window back from the screen on every repaint, which stalls on the desktop
    compositor; an opaque image is drawn with a plain copy.
    """
    width, height = max(1, int(width)), max(1, int(height))
    radius = max(0, min(int(radius), width // 2, height // 2))
    r, g, b = rgb(color)
    solid = bytes((r, g, b, 255))
    corner = _corner(radius) if radius else ()
    if background is not None and radius:
        br, bg, bb = rgb(background)
        blend = lambda a: bytes((round(br + (r - br) * a / 255), round(bg + (g - bg) * a / 255),
                                 round(bb + (b - bb) * a / 255), 255))
        corner = tuple(b"".join(blend(value) for value in row) for row in corner)
        scanlines = bytearray()
        middle = b"\x00" + solid * width
        for y in range(height):
            edge = y if y < radius else height - 1 - y if y >= height - radius else None
            if edge is None:
                scanlines += middle
                continue
            row = corner[edge]
            mirrored = b"".join(row[i:i + 4] for i in range(len(row) - 4, -1, -4))
            scanlines += b"\x00" + row + solid * (width - 2 * radius) + mirrored
        return png(width, height, scanlines)
    middle = b"\x00" + solid * width
    scanlines = bytearray()
    for y in range(height):
        edge = y if y < radius else height - 1 - y if y >= height - radius else None
        if edge is None:
            scanlines += middle
            continue
        alpha = corner[edge]
        left = b"".join(bytes((r, g, b, value)) for value in alpha)
        right = b"".join(bytes((r, g, b, value)) for value in reversed(alpha))
        scanlines += b"\x00" + left + solid * (width - 2 * radius) + right
    return png(width, height, scanlines)


@lru_cache(maxsize=512)
def ring_png(size, stroke, level, progress, track, badge=None):
    """Battery ring like the iOS Batteries widget.

    ``level`` is 0-100 or None (track only). The arc starts at 12 o'clock,
    runs clockwise and has round caps. ``badge`` is an optional
    ``(gap_color, badge_color, bolt_color)`` charging marker at the top right.
    """
    size, stroke = int(size), max(1., float(stroke))
    centre = size / 2
    radius = (size - stroke) / 2 - .5
    half = stroke / 2
    progress, track = rgb(progress), rgb(track)
    sweep = 0. if level is None else max(0., min(100., float(level))) / 100 * TAU
    full = sweep >= TAU - 1e-6
    start = (centre, centre - radius)
    end = (centre + radius * math.sin(sweep), centre - radius * math.cos(sweep))
    if badge is not None:
        gap, disc, bolt = (rgb(value) for value in badge)
        badge_radius = size * .18
        bx, by = size - badge_radius - .5, badge_radius + .5
        gap_radius = badge_radius + max(1.5, size * .045)
        bolt_points = tuple((bx + px * badge_radius * .82, by + py * badge_radius * .82) for px, py in BOLT)
    scanlines = bytearray()
    for y in range(size):
        scanlines.append(0)
        py = y + .5
        for x in range(size):
            px = x + .5
            dx, dy = px - centre, py - centre
            band = abs(math.hypot(dx, dy) - radius) - half
            pixel = _Layers()
            pixel.over(track, _cover(band))
            if sweep > 0:
                if full:
                    shape = band
                else:
                    angle = math.atan2(dx, -dy) % TAU
                    if angle <= sweep:
                        shape = band
                    else:
                        shape = min(math.hypot(px - start[0], py - start[1]),
                                    math.hypot(px - end[0], py - end[1])) - half
                pixel.over(progress, _cover(shape))
            if badge is not None:
                distance = math.hypot(px - bx, py - by)
                if distance - gap_radius < .5:
                    pixel.over(gap, _cover(distance - gap_radius))
                    pixel.over(disc, _cover(distance - badge_radius))
                    if distance < badge_radius:
                        hits = sum(_inside(bolt_points, x + (i + .5) / 4, y + (j + .5) / 4)
                                   for i in range(4) for j in range(4))
                        pixel.over(bolt, hits / 16)
            scanlines.extend(pixel.bytes())
    return png(size, size, scanlines)
