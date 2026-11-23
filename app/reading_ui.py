"""Small presentation utilities for source timestamps and in-place canvas edits."""
import time


def keep_newer(previous, incoming):
    """A slower batch must not replace a newer reply from the same source."""
    if previous is None or incoming.checked_at is None or previous.checked_at is None:
        return False
    same = previous.source == incoming.source or {previous.source, incoming.source} <= {"HID++ 实时", "HID++ 2.0"}
    return same and previous.checked_at > incoming.checked_at


def relative_time(then, now=None):
    """iOS-style age of a timestamp: 刚刚 / N 分钟前 / N 小时前 / M月D日."""
    now = time.time() if now is None else now
    seconds = max(0, now - then)
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    day = time.localtime(then)
    return f"{day.tm_mon}月{day.tm_mday}日"


class ReadingTimes:
    """Remember last successful reads without retaining old battery percentages."""
    def __init__(self):
        self.last = {}

    def observe(self, key, reading):
        if reading.error:
            return
        times = self.last.setdefault(key, {})
        values = {"device": reading.observed_at, **reading.component_observed_at}
        for part, value in values.items():
            if value is not None and (part not in times or value >= times[part]):
                times[part] = value

    def _times(self, key, reading, part=None):
        """(current success time, last success time, text when neither exists)."""
        last = self.last.get(key, {}).get(part or "device")
        if part is not None:
            known = not reading.error and ((reading.components or {}).get(part) or {}).get("level") is not None
            current = reading.component_observed_at.get(part) if known else None
            empty = "未上报"
        else:
            known = not reading.error and (reading.ok or any(
                value.get("level") is not None for value in (reading.components or {}).values()))
            current = reading.observed_at if known else None
            empty = "等待电量上报" if reading.connected is True else "等待设备连接" if reading.connected is False else "尚未读取电量"
        return current, last, empty

    def label(self, key, reading, part=None):
        if part is None and reading.status == "待适配电量":
            return "电量协议待适配"
        current, last, empty = self._times(key, reading, part)
        if current is not None:
            return ("更新 " if part else "读取 ") + time.strftime("%H:%M:%S", time.localtime(current))
        if last is not None:
            return ("上次 " if part else "上次成功 ") + time.strftime("%H:%M:%S", time.localtime(last))
        return empty

    def ago(self, key, reading, part=None, now=None):
        """Relative reading time for the iOS list: 刚刚 / 3 分钟前 / 上次读取 1 小时前."""
        if part is None and reading.status == "待适配电量":
            return "电量协议待适配"
        current, last, empty = self._times(key, reading, part)
        if current is not None:
            return relative_time(current, now)
        if last is not None:
            return "上次读取 " + relative_time(last, now)
        return empty

    def retain(self, keys):
        self.last = {key: value for key, value in self.last.items() if key in keys}


class LiveItems:
    """Keep layout item IDs stable; send only changed attributes to Tk."""
    def __init__(self, after_set=lambda *_args, **_options: None):
        self.bindings, self.after_set = {}, after_set

    def link(self, group, name, canvas, items, **initial):
        self.bindings[group, name] = (canvas, tuple(items), initial)

    def apply(self, group, values):
        changes = 0
        for name, options in values.items():
            binding = self.bindings.get((group, name))
            if binding is None:
                continue
            canvas, items, previous = binding
            changed = {key: value for key, value in options.items() if value != previous.get(key)}
            if changed:
                for item in items:
                    canvas.itemconfigure(item, **changed)
                    self.after_set(canvas, item, **changed)
                    changes += 1
                previous.update(changed)
        return changes
