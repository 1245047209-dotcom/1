"""Persistent device membership, separate from battery history and tray settings."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from state_lock import state_file_lock

DEFAULT_DEVICE_ID = "aula-f87-default"


class DeviceStoreError(ValueError):
    pass


@dataclass(frozen=True)
class SavedDevice:
    id: str
    instance_id: str
    model: str
    manufacturer: str
    hardware_id: str
    category: str
    wireless: bool = True
    transport: str = "usb"
    product_variant: int = 0

    @property
    def label(self):
        return f"{self.manufacturer} {self.model}" if self.manufacturer not in self.model and self.manufacturer != "未提供" else self.model


class DeviceStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(__file__).with_name("added_devices.json")

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("devices"), list):
                raise ValueError("不支持的设备列表格式")
            devices = [SavedDevice(**row) for row in data["devices"]]
            seen_ids, seen_instances = set(), set()
            for d in devices:
                if type(d.product_variant) is not int or not 0 <= d.product_variant < 50:
                    raise ValueError("无效的产品配色")
                if any(not isinstance(getattr(d, f), str) or not getattr(d, f).strip()
                       for f in ("id", "instance_id", "model", "manufacturer", "hardware_id", "category")):
                    raise ValueError("设备信息不完整")
                if (d.id == DEFAULT_DEVICE_ID or d.id in seen_ids or
                        d.instance_id.upper() in seen_instances or
                        d.transport not in ("usb", "bluetooth") or
                        not re.fullmatch(r"[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}" if d.transport == "usb" else r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", d.hardware_id)):
                    raise ValueError("设备标识无效或重复")
                seen_ids.add(d.id)
                seen_instances.add(d.instance_id.upper())
            from aula_f87_v2d import is_f87_v2d
            devices = [replace(d, model="F87 Pro V2D", manufacturer="AULA", wireless=True)
                       if d.transport == "usb" and is_f87_v2d(d.hardware_id, d.model)
                       else d for d in devices]
            from aula_catalog import model_for_device
            devices = [replace(d, model=model["model"], manufacturer="AULA", category="键盘")
                       if (model := model_for_device(d)) is not None else d for d in devices]
            from logitech_g304 import HARDWARE_ID, FAMILY_NAME, model_name
            devices = [replace(d, model=model_name(d.model) or FAMILY_NAME,
                               manufacturer="Logitech", category="鼠标", wireless=True)
                       if d.transport == "usb" and d.hardware_id.upper() == HARDWARE_ID
                       else d for d in devices]
            selected = data.get("selected", DEFAULT_DEVICE_ID)
            if data.get("default_visible", True):
                seen_ids.add(DEFAULT_DEVICE_ID)
            if selected not in seen_ids:
                selected = DEFAULT_DEVICE_ID if DEFAULT_DEVICE_ID in seen_ids else next((d.id for d in devices), None)
            return devices, selected
        except FileNotFoundError:
            return [], DEFAULT_DEVICE_ID
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            raise DeviceStoreError(f"无法读取已加入设备，原文件已保留：{exc}") from exc

    def view_mode(self):
        try:
            mode = json.loads(self.path.read_text(encoding="utf-8")).get("view", "all")
            return mode if mode in ("all", "single") else "all"
        except (OSError, ValueError, AttributeError):
            return "all"

    def default_visible(self):
        return bool(self._options().get("default_visible", True))

    def _options(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            opts = {"default_visible": bool(data.get("default_visible", True)),
                    "ignored_instances": data.get("ignored_instances", [])}
            if isinstance(data.get("device_order"), list):
                opts["device_order"] = [str(v) for v in data["device_order"] if isinstance(v, str)]
            return opts
        except (OSError, ValueError, AttributeError):
            return {"default_visible": True, "ignored_instances": []}

    def order(self):
        return list(self._options().get("device_order", ()))

    def reorder(self, ordered_ids):
        clean_order = [str(item) for item in (ordered_ids or ()) if isinstance(item, str) and item]
        with state_file_lock(self.path):
            devices, selected = self.load()
            options = self._options()
            rank = {key: index for index, key in enumerate(clean_order)}
            devices.sort(key=lambda d: rank.get(d.id, len(rank)))
            options["device_order"] = clean_order
            self._save(devices, selected, options=options)

    def can_auto_add(self, receiver):
        from aula_protocols import legacy_default
        options = self._options()
        return (not legacy_default(receiver) or options["default_visible"]) and receiver.instance_id.casefold() not in {
            str(v).casefold() for v in options["ignored_instances"]}

    def set_view_mode(self, mode):
        if mode not in ("all", "single"):
            raise DeviceStoreError("无效的视图")
        with state_file_lock(self.path):
            devices, selected = self.load()
            self._save(devices, selected, mode)

    def _save(self, devices, selected, view=None, *, options=None):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump({"version": 1, "selected": selected, "view": view or self.view_mode(),
                           **(options if options is not None else self._options()),
                           "devices": [asdict(d) for d in devices]}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def id_for(self, receiver):
        from aula_protocols import legacy_default
        devices, _ = self.load()
        existing = next((d.id for d in devices if d.instance_id.upper() == receiver.instance_id.upper()), None)
        return existing or (DEFAULT_DEVICE_ID if legacy_default(receiver) and self.default_visible() else None)

    def add(self, receiver, *, select=True):
        with state_file_lock(self.path):
            devices, selected = self.load()
            options = self._options()
            if not select and not self.can_auto_add(receiver):
                return None, False
            if select:
                options["ignored_instances"] = [v for v in options["ignored_instances"]
                                                if str(v).casefold() != receiver.instance_id.casefold()]
            from aula_protocols import legacy_default
            existing = next((d for d in devices if d.instance_id.upper() == receiver.instance_id.upper()), None)
            if existing:
                from aula_catalog import model_for_device
                if model_for_device(receiver) is not None:
                    devices = [replace(d, model=receiver.model, manufacturer="AULA", category="键盘",
                                       product_variant=getattr(receiver, "product_variant", 0))
                               if d.id == existing.id else d for d in devices]
                self._save(devices, existing.id if select else selected, options=options)
                return existing.id, False
            if legacy_default(receiver):
                restored = not options["default_visible"]
                options["default_visible"] = True
                self._save(devices, DEFAULT_DEVICE_ID if select else selected, options=options)
                return DEFAULT_DEVICE_ID, restored
            device = SavedDevice(uuid.uuid4().hex, receiver.instance_id, receiver.model,
                                 receiver.manufacturer or "未提供", receiver.hardware_id.upper(),
                                 receiver.category or "USB 外设", bool(receiver.wireless),
                                 getattr(receiver, "transport", "usb"), getattr(receiver, "product_variant", 0))
            devices.append(device)
            self._save(devices, device.id if select else selected, options=options)
            return device.id, True

    def select(self, device_id):
        with state_file_lock(self.path):
            devices, _ = self.load()
            if not ((device_id == DEFAULT_DEVICE_ID and self.default_visible()) or any(d.id == device_id for d in devices)):
                raise DeviceStoreError("设备已被移除，请重新加入")
            self._save(devices, device_id)

    def remove(self, device_id):
        with state_file_lock(self.path):
            devices, selected = self.load()
            options = self._options()
            if device_id == DEFAULT_DEVICE_ID:
                options["default_visible"] = False
            else:
                removed = next((d for d in devices if d.id == device_id), None)
                if removed is not None:
                    options["ignored_instances"] = list(dict.fromkeys(options["ignored_instances"] + [removed.instance_id.casefold()]))
            remaining = [d for d in devices if d.id != device_id]
            if selected == device_id:
                selected = DEFAULT_DEVICE_ID if options["default_visible"] else next((d.id for d in remaining), None)
            self._save(remaining, selected, options=options)

    def confirm_logitech_model(self, device_id, model):
        """Persist a validated HID++ name without changing membership/selection."""
        from logitech_g304 import HARDWARE_ID, model_name
        model = model_name(model)
        if not model:
            return False
        with state_file_lock(self.path):
            devices, selected = self.load()
            changed = False
            updated = []
            for device in devices:
                if (device.id == device_id and device.transport == "usb"
                        and device.hardware_id.upper() == HARDWARE_ID and device.model != model):
                    device = replace(device, model=model, manufacturer="Logitech", category="鼠标", wireless=True)
                    changed = True
                updated.append(device)
            if changed:
                self._save(updated, selected)
            return changed


def read_saved_device(device):
    """Membership does not imply a battery protocol is implemented for a model."""
    from device_readers import Reading
    if device.transport == "bluetooth":
        from bluetooth_devices import read_bluetooth_device
        return read_bluetooth_device(device)
    from receiver_scan import scan_receivers
    try:
        receivers = scan_receivers()
        current = next((r for r in receivers
                        if r.instance_id.upper() == device.instance_id.upper()), None)
    except Exception as exc:
        return Reading(device.label, "设备检查失败", error=f"设备检查失败：{exc}", connected=None)
    if current is None:
        return Reading(device.label, "接收器未连接", error="未找到已加入的接收器，请插入接收器后刷新",
                       detail=device.hardware_id, connected=False)
    if current.hardware_id.upper() == "046D:C539":
        from logitech_battery import read_pro_wireless
        return read_pro_wireless(current, device.label)
    if current.hardware_id.upper() == "046D:C53F":
        from logitech_g304 import read_g304
        return read_g304(current, device.label)
    from aula_f87_v2d import is_f87_v2d, read_f87_v2d
    if is_f87_v2d(current.hardware_id, current.model, *current.names):
        return read_f87_v2d(current, device.label)
    from aula_catalog import model_for_device
    if model_for_device(device) is not None:
        from aula_protocols import read_keyboard
        return read_keyboard(current, device)
    return Reading(device.label, "接收器已连接", status="待适配电量",
                   error="暂未适配该型号的电量读取", detail="接收器已连接 · 暂未适配该型号的电量读取",
                   connected=True)
