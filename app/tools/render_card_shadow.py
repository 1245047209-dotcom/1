"""Build a smooth, nine-slice card surface using only the Python stdlib."""
import math
from pathlib import Path
import struct
import zlib

SIZE = 161
PADDING = 32
RADIUS = 24
SAMPLES = 8


def coverage(x, y):
    center = SIZE / 2
    half = (SIZE - 2 * PADDING) / 2
    count = 0
    for sy in range(SAMPLES):
        for sx in range(SAMPLES):
            qx = abs(x + (sx + 0.5) / SAMPLES - center) - (half - RADIUS)
            qy = abs(y + (sy + 0.5) / SAMPLES - center) - (half - RADIUS)
            distance = (math.hypot(max(qx, 0), max(qy, 0))
                        + min(max(qx, qy), 0) - RADIUS)
            count += distance <= 0
    return count / (SAMPLES * SAMPLES)


def gaussian_blur(mask, sigma):
    radius = math.ceil(sigma * 3)
    kernel = [math.exp(-(i * i) / (2 * sigma * sigma))
              for i in range(-radius, radius + 1)]
    total = sum(kernel)
    kernel = [weight / total for weight in kernel]
    horizontal = [0.0] * (SIZE * SIZE)
    output = [0.0] * (SIZE * SIZE)
    for y in range(SIZE):
        for x in range(SIZE):
            horizontal[y * SIZE + x] = sum(
                mask[y * SIZE + sx] * kernel[sx - x + radius]
                for sx in range(max(0, x - radius), min(SIZE, x + radius + 1)))
    for y in range(SIZE):
        for x in range(SIZE):
            output[y * SIZE + x] = sum(
                horizontal[sy * SIZE + x] * kernel[sy - y + radius]
                for sy in range(max(0, y - radius), min(SIZE, y + radius + 1)))
    return output


def png_chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def main():
    mask = [coverage(x, y) for y in range(SIZE) for x in range(SIZE)]
    ambient = gaussian_blur(mask, 8)
    contact = gaussian_blur(mask, 2.8)
    pixels = bytearray()
    for y in range(SIZE):
        pixels.append(0)  # PNG's unfiltered scanline marker.
        for x in range(SIZE):
            wide = ambient[(y - 8) * SIZE + x] * 0.10 if y >= 8 else 0
            near = contact[(y - 3) * SIZE + x] * 0.045 if y >= 3 else 0
            shadow = wide + near * (1 - wide)
            face = mask[y * SIZE + x]
            alpha = face + shadow * (1 - face)
            if alpha:
                rgb = [round((255 * face + color * shadow * (1 - face)) / alpha)
                       for color in (32, 43, 58)]
                pixels.extend((*rgb, round(alpha * 255)))
            else:
                pixels.extend((0, 0, 0, 0))
    png = b"\x89PNG\r\n\x1a\n"
    png += png_chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += png_chunk(b"IDAT", zlib.compress(bytes(pixels), 9))
    png += png_chunk(b"IEND", b"")
    asset = Path(__file__).resolve().parents[1] / "assets" / "card-surface.png"
    asset.write_bytes(png)
    print(f"Rendered {asset.name}: rounded face, ambient and contact shadows.")


if __name__ == "__main__":
    main()
