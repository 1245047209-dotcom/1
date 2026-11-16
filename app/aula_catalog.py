"""Offline official AULA mechanical-keyboard model and product-image catalog."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def catalog():
    return json.loads((BASE / "assets/aula-catalog/catalog.json").read_text(encoding="utf-8"))


def compact(text):
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


def model_by_key(key):
    return next((m for m in catalog()["models"] if m["key"] == key), None)


def match_name(*names, trusted=False):
    """An exact model token, with a brand/trusted-device context, is required.

    F87 Pro, F87 Pro V2, F87S and F87 Pro V2D are distinct revisions. A long
    token must never be collapsed into a shorter model in this catalog.
    """
    for name in names:
        text = str(name).casefold()
        if not trusted and not ("aula" in text or "狼蛛" in text):
            continue
        value = compact(text)
        value = re.sub(r"^(?:aula|langzhu)", "", value)
        # Only descriptor decorations may follow the exact model name.
        changed = True
        while changed:
            changed = False
            for suffix in ("dongle", "receiver", "wirelesskeyboard", "gamingkeyboard", "keyboard", "24g", "80key", "8k"):
                if value.endswith(suffix):
                    value = value[:-len(suffix)]
                    changed = True
        for model in catalog()["models"]:
            if any(value == compact(alias) for alias in model["aliases"]):
                return model
    return None


def model_for_device(device):
    if device is None:
        return None
    maker = compact(getattr(device, "manufacturer", ""))
    return match_name(device.model, trusted=maker in ("aula", "狼蛛"))


def image_path(key, variant=0):
    model = model_by_key(key)
    if model is None:
        return None
    photos = model["photos"]
    relative = Path(photos[max(0, min(int(variant), len(photos)-1))]["path"])
    path = (BASE / relative).resolve()
    if not path.is_relative_to(BASE / "assets/aula-catalog"):
        raise ValueError("无效的产品图片路径")
    return path
