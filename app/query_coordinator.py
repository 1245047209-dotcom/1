"""Serialize physical-device queries and share short-lived source observations.

The Windows mutex spans processes (panel, tray and extra app instances). JSON
contains observations only, never executable objects or HID handles. A cache
failure must not prevent a hardware read, and a failed read replaces old data.
"""
from dataclasses import asdict, replace
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

CACHE_SECONDS = 2.0
FAILURE_SECONDS = .3


def resource_key(device):
    """Use physical paths/instance IDs, never just a model or VID/PID."""
    path = getattr(device, "path", None)
    if path:
        return str(path).casefold()
    paths = sorted({str(p).casefold() for p in getattr(device, "hid_paths", ())})
    instance = str(getattr(device, "instance_id", "")).casefold()
    if not instance and not paths:
        raise ValueError("设备缺少物理实例和接口标识")
    return json.dumps([instance, paths], separators=(",", ":"))


def cache_directory():
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "NexusBatteryCenter" / "QueryCache-v1"


def _load(path, key, now):
    from device_readers import Reading
    try:
        if path.stat().st_size > 65536:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("key") != key:
            return None
        saved = data["saved_at"]
        if type(saved) not in (float, int) or not math.isfinite(saved):
            return None
        reading = Reading(**data["reading"])
        if any(not isinstance(getattr(reading, name), str) for name in
               ("title", "subtitle", "status", "detail", "error", "extra", "source", "reported_model")):
            return None
        if (type(reading.estimated) is not bool or type(reading.coarse) is not bool
                or reading.connected is not None and type(reading.connected) is not bool
                or reading.components is not None and not isinstance(reading.components, dict)
                or not isinstance(reading.component_observed_at, dict)):
            return None
        ttl = CACHE_SECONDS if reading.ok else min(CACHE_SECONDS, FAILURE_SECONDS)
        if not 0 <= now - saved < ttl:
            return None
        if (reading.level is not None and (type(reading.level) is not int or not 0 <= reading.level <= 100)
                or reading.checked_at != saved
                or reading.ok and reading.observed_at != saved):
            return None
        return reading
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _save(path, key, reading):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.stem + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"schema": 1, "key": key, "saved_at": reading.checked_at,
                       "reading": asdict(reading)}, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def query_reading(resource, operation, read):
    """Followers wait for the owner, then reuse its completed observation."""
    import hid_win as hw
    from device_readers import stamp_observation
    key = json.dumps([resource, operation], separators=(",", ":"))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    path = cache_directory() / (digest + ".json")
    # Check only after acquiring ownership: an in-flight refresh must finish
    # before another client can consume or overwrite its result.
    with hw.resource_lock("observation:" + digest):
        cached = _load(path, key, time.time())
        if cached is not None:
            return cached
        reading = stamp_observation(read(), now=time.time())
        _save(path, key, reading)
        return reading


def shared_reading(operation):
    def decorate(read):
        @wraps(read)
        def coordinated(device, *args, **kwargs):
            # Explicit diagnostic traces must actually inspect hardware.
            if kwargs.get("trace") is not None:
                return read(device, *args, **kwargs)
            expected = getattr(args[0], "model", "") if args else ""
            result = query_reading(resource_key(device), operation + (":" + expected if expected else ""),
                                   lambda: read(device, *args, **kwargs))
            # UI labels are caller-owned, not part of a hardware observation.
            title = kwargs.get("title") or (args[0] if args and isinstance(args[0], str)
                                            else getattr(args[0], "label", None) if args else None)
            return replace(result, title=title) if title and not result.reported_model else result
        return coordinated
    return decorate
