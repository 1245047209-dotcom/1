# -*- coding: utf-8 -*-
"""只读取狼蛛 F87 Pro 2.4G 键盘，并供面板和托盘共用。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import time
from typing import Optional

import battery as bt
import hid_win as hw
from battery_runtime import record_sample
from query_coordinator import shared_reading

KEYBOARD_TITLE = "狼蛛 F87 Pro"
DEVICE_TITLES = (KEYBOARD_TITLE,)

@dataclass
class Reading:
    title: str
    subtitle: str
    level: Optional[int] = None
    status: str = ""
    detail: str = ""
    error: str = ""
    extra: str = ""
    remaining_hours: Optional[float] = None
    connected: Optional[bool] = None
    estimated: bool = False
    voltage_mv: Optional[int] = None
    source: str = ""
    components: Optional[dict] = None
    # Successful source reads only. Cache access and UI redraws never advance these.
    observed_at: Optional[float] = field(default=None, compare=False)
    component_observed_at: dict = field(default_factory=dict, compare=False)
    checked_at: Optional[float] = field(default=None, compare=False)
    reported_model: str = ""
    coarse: bool = False

    @property
    def ok(self) -> bool:
        return not self.error and (self.level is not None or self.status in ("charging", bt.STATUS_TEXT.get("charging")))

    @property
    def display_level(self) -> str:
        if self.level is not None:
            return f"{self.level}%"
        return bt.STATUS_TEXT.get("charging", "charging") if self.ok else "--"


def stamp_observation(reading, *, now=None):
    """Attach wall-clock times at the source boundary, keeping errors untimed."""
    now = time.time() if now is None else now
    if reading.checked_at is not None:
        return replace(reading)
    components = reading.components or {}
    parts = {key: now for key, part in components.items()
             if not reading.error and type(part.get("level")) is int and 0 <= part["level"] <= 100}
    known = not reading.error and (reading.ok or bool(parts))
    return replace(reading, observed_at=now if known else None, component_observed_at=parts, checked_at=now)


def _read_keyboard(interfaces: list[hw.HidInterface]) -> Reading:
    reading = Reading(KEYBOARD_TITLE, "未连接")
    matches = [
        d for d in interfaces
        if (d.vid, d.pid, d.usage_page, d.usage, d.input_len, d.output_len)
        == (bt.AULA_VID, bt.AULA_PID, 0xFF02, 0x0002, 20, 20)
    ]
    if not matches:
        reading.error = "未找到狼蛛 F87 Pro 2.4G 接收器"
        return reading

    for iface in matches:
        result = _read_f87_interface(iface)
        if result.ok:
            return result
        reading = result

    if not reading.error:
        reading.error = "没有收到键盘电量回复，请确认键盘处于 2.4G 模式并按键唤醒"
    return reading


@shared_reading("f87pro-battery-v1")
def _read_f87_interface(iface):
    reading = Reading(KEYBOARD_TITLE, f"{iface.vid_pid} · 2.4G 无线")
    from aula_catalog import match_name
    from aula_protocols import identify_interface
    named_model = match_name(getattr(iface, "product", ""))
    identified_model = identify_interface(iface)
    actual_model = identified_model or named_model
    if actual_model is None:
        reading.error = "未能确认接收器配对型号，请按键唤醒后刷新或重新搜索设备"
        return reading
    if actual_model is not None and actual_model["key"] != "f87pro":
        return reading  # A shared receiver must not turn an F75/F99 into the default F87.
    try:
        result = bt.strategy_aula_f87(iface)
    except Exception as exc:
        reading.error = f"读取出错：{exc}"
        return reading
    if result is None:
        return reading
    reading.subtitle = f"{iface.vid_pid} · 2.4G 无线"
    if result.ok:
        reading.level = result.level
        reading.status = result.status_text
        reading.detail = result.source
        reading.extra = result.extra.get("notice", "")
        reading.error = ""
        return reading
    reading.error = result.detail or "读取失败"

    if not reading.error:
        reading.error = "没有收到键盘电量回复，请确认键盘处于 2.4G 模式并按键唤醒"
    return reading


def read_all() -> list[Reading]:
    try:
        interfaces = hw.enumerate_hid()
    except OSError as exc:
        return [Reading(KEYBOARD_TITLE, "", error=f"设备枚举失败：{exc}")]
    reading = _read_keyboard(interfaces)
    reading.remaining_hours = record_sample(
        reading.level, charging=reading.status in ("charging", bt.STATUS_TEXT.get("charging")),
        valid=reading.ok)
    return [reading]
