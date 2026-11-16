"""Learn approximate remaining battery life from local discharge observations."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import msvcrt
import os
from pathlib import Path
import threading
import time
from typing import Optional
from keyboard_activity import get_snapshot

HISTORY_PATH = Path(__file__).resolve().with_name("battery_usage.json")
MAX_GAP = 90 * 60  # Allows the existing 30-minute polling option.
HISTORY_AGE = 7 * 24 * 3600
MIN_DROP = 2
MIN_SECONDS = 15 * 60
_FILE_LOCK = threading.Lock()
_AUTO_ACTIVITY = object()


def _point(value):
    try:
        at, level = float(value["at"]), value["level"]
        if math.isfinite(at) and at > 0 and type(level) is int and 0 <= level <= 100:
            point = {"at": at, "level": level}
            session, active = value.get("session"), value.get("active_seconds")
            if isinstance(session, str) and isinstance(active, (int, float)) and math.isfinite(active) and active >= 0:
                point.update(session=session, active_seconds=active)
            return point
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return None


class UsageEstimator:
    def __init__(self, state=None):
        state = state if isinstance(state, dict) and state.get("version") in (1, 2) else {}
        self.previous = _point(state.get("previous"))
        self.boundary = _point(state.get("boundary"))
        self.projection_anchor = _point(state.get("projection_anchor"))
        self.intervals = []
        intervals = state.get("intervals", [])
        for item in intervals if isinstance(intervals, list) else []:
            try:
                at, seconds, drop = float(item["at"]), float(item["seconds"]), item["drop"]
                if (math.isfinite(at) and at > 0 and math.isfinite(seconds) and 60 <= seconds <= HISTORY_AGE
                        and type(drop) is int and 1 <= drop <= 5):
                    active = item.get("active_seconds")
                    if not isinstance(active, (int, float)) or not math.isfinite(active) or not 0 <= active <= seconds:
                        active = None  # Retain old records without guessing their activity.
                    self.intervals.append({"at": at, "seconds": seconds, "drop": drop, "active_seconds": active})
            except (KeyError, TypeError, ValueError, OverflowError):
                continue

    def _prune(self, now):
        self.intervals = sorted(
            (item for item in self.intervals if 0 <= now - item["at"] <= HISTORY_AGE),
            key=lambda item: item["at"])[-12:]

    def usage_rates(self, now):
        self._prune(now)
        intervals = [item for item in self.intervals if item["active_seconds"] is not None]
        drop = sum(item["drop"] for item in intervals)
        seconds = sum(item["seconds"] for item in intervals)
        active = sum(item["active_seconds"] for item in intervals)
        if drop < MIN_DROP or seconds < MIN_SECONDS or active < 300:
            return None
        ratios = [item["active_seconds"] / item["seconds"] for item in intervals]
        if min(ratios) >= 0.9:
            return {"active_rate": drop * 3600 / active, "idle_rate": None, "kind": "initial"}
        if max(ratios) - min(ratios) < 0.2:
            return None  # One mixed duty cycle cannot identify two power rates.

        # Nonnegative least squares: discharge = idle_rate * elapsed_hours
        # + extra_active_rate * active_hours. Idle power cannot exceed active.
        rows = [(item["seconds"] / 3600, item["active_seconds"] / 3600, item["drop"])
                for item in intervals]
        xx = sum(x*x for x, _, _ in rows)
        yy = sum(y*y for _, y, _ in rows)
        xy = sum(x*y for x, y, _ in rows)
        xd = sum(x*d for x, _, d in rows)
        yd = sum(y*d for _, y, d in rows)
        candidates = [(xd / xx, 0.0), (0.0, yd / yy)]
        determinant = xx * yy - xy * xy
        if determinant > 1e-9:
            idle, extra = (xd * yy - yd * xy) / determinant, (yd * xx - xd * xy) / determinant
            if idle >= 0 and extra >= 0:
                candidates.append((idle, extra))
        idle, extra = min(candidates, key=lambda rates: sum(
            (rates[0]*x + rates[1]*y - d)**2 for x, y, d in rows))
        if idle + extra <= 0:
            return None
        return {"active_rate": idle + extra, "idle_rate": idle, "kind": "calibrated"}

    def remaining_hours(self, level, now):
        rates = self.usage_rates(now)
        return None if rates is None else level / rates["active_rate"]

    def observe(self, level, charging=False, now=None, activity=None):
        now = time.time() if now is None else now
        self._prune(now)
        if charging or type(level) is not int or not 0 <= level <= 100:
            self.previous = self.boundary = self.projection_anchor = None
            return None
        point = {"at": now, "level": level}
        if activity is not None:
            point.update(session=activity["session"], active_seconds=activity["active_seconds"])
        continuous = (self.previous is not None and point.get("session") is not None
                      and point.get("session") == self.previous.get("session")
                      and point["active_seconds"] >= self.previous.get("active_seconds", float("inf")))
        if (self.previous is None or now < self.previous["at"]
                or (now - self.previous["at"] > MAX_GAP and not continuous)):
            self.previous, self.boundary = point, None
            self.projection_anchor = point
            return self.remaining_hours(level, now)
        if now == self.previous["at"]:
            return self.remaining_hours(level, now)
        drop = self.previous["level"] - level
        session_changed = point.get("session") != self.previous.get("session")
        if session_changed:
            self.boundary = None
        if self.projection_anchor is None or drop != 0 or session_changed:
            self.projection_anchor = point
        if drop < 0:
            # Charging or a fluctuating report must not look like discharge.
            self.boundary = None
        elif drop > 0:
            if self.boundary is not None:
                seconds = now - self.boundary["at"]
                measured_drop = self.boundary["level"] - level
                if 1 <= measured_drop <= 5 and 60 <= seconds <= HISTORY_AGE:
                    active = None
                    if point.get("session") is not None and point.get("session") == self.boundary.get("session"):
                        delta = point["active_seconds"] - self.boundary["active_seconds"]
                        if 0 <= delta <= seconds + 2:
                            active = min(delta, seconds)
                    self.intervals.append({"at": now, "seconds": seconds, "drop": measured_drop,
                                           "active_seconds": active})
            # The first drop establishes a percentage boundary: its preceding
            # fraction of one percent has an unknown start time and is excluded.
            self.boundary = point
        self.previous = point
        return self.remaining_hours(level, now)

    def to_dict(self):
        return {"version": 2, "previous": self.previous, "boundary": self.boundary,
                "projection_anchor": self.projection_anchor,
                "intervals": self.intervals}


@contextmanager
def _locked(path):
    # The tray and panel are separate processes; serialize their read/modify/write.
    with _FILE_LOCK:
        with path.with_suffix(".lock").open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def record_sample(level, *, charging=False, valid=True, path=HISTORY_PATH,
                  now=None, activity=_AUTO_ACTIVITY) -> Optional[float]:
    if not valid:
        # Brief missing replies do not imply the battery stopped discharging.
        # A long gap is discarded when the next valid reading arrives.
        return None
    path = Path(path)
    if activity is _AUTO_ACTIVITY:
        activity = get_snapshot()
    try:
        with _locked(path):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                state = None
            estimator = UsageEstimator(state)
            hours = estimator.observe(level, charging, now, activity)
            temporary = path.with_suffix(".json.tmp")
            try:
                temporary.write_text(json.dumps(estimator.to_dict(), ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return hours
    except OSError:
        # A history-file failure must not interrupt the hardware reading.
        return None


def get_runtime_view(level, *, charging=False, valid=True, path=HISTORY_PATH,
                     now=None, activity=_AUTO_ACTIVITY):
    now = time.time() if now is None else now
    if activity is _AUTO_ACTIVITY:
        activity = get_snapshot()
    if not valid:
        return {"hours": None, "hint": "等待设备连接"}
    if charging:
        return {"hours": None, "hint": "充电时暂停使用估算"}
    if activity is None:
        hint = "活动检测暂不可用"
    else:
        hint = "使用中 · 实时推算" if activity.get("active") else "闲置中 · 不计入使用时间"
    try:
        estimator = UsageEstimator(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeError):
        estimator = UsageEstimator()
    rates = estimator.usage_rates(now)
    if rates is None or type(level) is not int:
        if activity is not None:
            hint = "使用中 · 学习耗电规律" if activity.get("active") else "闲置中 · 学习待机耗电"
        return {"hours": None, "hint": hint}
    projected_level = float(level)
    anchor = estimator.projection_anchor
    if (activity is not None and anchor is not None and anchor["level"] == level
            and anchor.get("session") == activity.get("session")
            and 0 <= now - anchor["at"] <= MAX_GAP):
        elapsed = now - anchor["at"]
        active = max(0, min(elapsed, activity["active_seconds"] - anchor["active_seconds"]))
        consumed = (active * rates["active_rate"] + (elapsed - active) * (rates["idle_rate"] or 0)) / 3600
        # Stay inside the reported integer percentage's uncertainty; reconcile
        # with the actual device reading rather than inventing a new percentage.
        projected_level -= max(0, min(0.99, consumed))
    return {"hours": max(0, projected_level) / rates["active_rate"], "hint": hint}


def format_remaining(hours: Optional[float], *, live=False) -> str:
    if hours is None:
        return "正在估算…"
    if hours <= 0:
        return "电量即将耗尽"
    if live and 1 <= hours < 48:
        minutes = round(hours * 60 / 5) * 5
        whole, remainder = divmod(minutes, 60)
        return f"约 {whole} 小时 {remainder} 分钟" if remainder else f"约 {whole} 小时"
    if live and hours >= 48:
        days, remainder = divmod(round(hours), 24)
        return f"约 {days} 天 {remainder} 小时" if remainder else f"约 {days} 天"
    if hours < 1:
        minutes = max(5, round(hours * 60 / 5) * 5)
        return "约 1 小时" if minutes >= 60 else f"约 {minutes} 分钟"
    if hours < 48:
        rounded = max(1, round(hours * 2) / 2)
        return f"约 {rounded:g} 小时"
    days = round(hours / 24 * 2) / 2
    return f"约 {days:g} 天"
