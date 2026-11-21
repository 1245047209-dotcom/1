"""Layout and display transforms for the multi-device dashboard (stdlib only)."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from device_readers import KEYBOARD_TITLE, Reading, stamp_observation
from saved_devices import DEFAULT_DEVICE_ID

CARD_MARGIN = 16
CARD_PADDING = 10
CARD_GAP = 14
CARD_HEIGHT = 198
EMPTY_HEIGHT = 150
OVERVIEW_TOP = 125
FOOTER_HEIGHT = 40


def read_device_batch(devices, read_default, read_saved, *, include_default=True):
    """One unavailable device cannot suppress another device's result."""
    def read(device):
        try:
            if device is not None:
                return stamp_observation(read_saved(device))
            values = read_default()
            return stamp_observation(values[0]) if values else Reading(KEYBOARD_TITLE, "", error="没有读取结果")
        except Exception as exc:
            return Reading(device.label if device else KEYBOARD_TITLE, "", error=f"读取异常：{exc}")
    requests = ([(DEFAULT_DEVICE_ID, None)] if include_default else []) + [(d.id, d) for d in devices]
    if not requests:
        return {}
    result = {}
    with ThreadPoolExecutor(max_workers=min(4, len(requests))) as pool:
        futures = {pool.submit(read, device): key for key, device in requests}
        for future in as_completed(futures):
            result[futures[future]] = future.result()
    return result


def content_height(count):
    if not count:
        return EMPTY_HEIGHT
    return count * CARD_HEIGHT + (count - 1) * CARD_GAP + 2 * CARD_PADDING


def card_rectangles(width, count):
    rectangles = []
    for index in range(count):
        y = CARD_PADDING + index * (CARD_HEIGHT + CARD_GAP)
        rectangles.append((CARD_MARGIN, y, width - CARD_MARGIN, y + CARD_HEIGHT))
    return rectangles


def fit_ppm(data, max_width, max_height, clockwise=False):
    """Bilinear display scaling / rotation; source assets are left intact.

    Input is the P6 RGB stream supplied by Tk with white compositing.
    """
    magic, dimensions, maximum, pixels = data.split(b"\n", 3)
    if magic != b"P6" or maximum != b"255":
        raise ValueError("Unsupported display pixel format")
    width, height = map(int, dimensions.split())
    if len(pixels) != width * height * 3:
        raise ValueError("Incomplete display pixel data")
    rotated_width, rotated_height = (height, width) if clockwise else (width, height)
    scale = min(max_width / rotated_width, max_height / rotated_height, 1)
    target_width = max(1, round(rotated_width * scale))
    target_height = max(1, round(rotated_height * scale))
    output = bytearray(target_width * target_height * 3)
    for y in range(target_height):
        ry = min(rotated_height - 1, max(0, (y + 0.5) / scale - 0.5))
        for x in range(target_width):
            rx = min(rotated_width - 1, max(0, (x + 0.5) / scale - 0.5))
            sx, sy = (ry, height - 1 - rx) if clockwise else (rx, ry)
            x0, y0 = int(sx), int(sy)
            x1, y1 = min(x0 + 1, width - 1), min(y0 + 1, height - 1)
            fx, fy = sx - x0, sy - y0
            offsets = ((y0 * width + x0) * 3, (y0 * width + x1) * 3,
                       (y1 * width + x0) * 3, (y1 * width + x1) * 3)
            dest = (y * target_width + x) * 3
            for channel in range(3):
                top = pixels[offsets[0] + channel] * (1 - fx) + pixels[offsets[1] + channel] * fx
                bottom = pixels[offsets[2] + channel] * (1 - fx) + pixels[offsets[3] + channel] * fx
                output[dest + channel] = round(top * (1 - fy) + bottom * fy)
    return f"P6\n{target_width} {target_height}\n255\n".encode() + output
