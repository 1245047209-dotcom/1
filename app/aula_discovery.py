"""Background AULA discovery; only confirmed catalog matches are auto-added."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import threading

import aula_catalog


class AulaDiscovery:
    def __init__(self, state_path=None):
        self.path = Path(state_path) if state_path else Path(__file__).with_name("aula_discovery_state.json")
        self.results = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.lock = threading.RLock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.ignored = {str(v).casefold() for v in data.get("ignored", [])}
        except (OSError, ValueError, TypeError, AttributeError):
            self.ignored = set()
        self.thread = threading.Thread(target=self._run, daemon=True, name="aula-discovery")
        self.thread.start()

    def _run(self):
        from receiver_scan import scan_receivers
        from aula_protocols import identify_receivers
        while not self.stop.is_set():
            try:
                receivers = identify_receivers(scan_receivers())
                from aula_f87_v2d import is_f87_v2d
                with self.lock:
                    found = [r for r in receivers if r.instance_id.casefold() not in self.ignored
                             and (aula_catalog.model_for_device(r) is not None or is_f87_v2d(r.hardware_id, r.model))]
                if self.results.full():
                    try:
                        self.results.get_nowait()
                    except queue.Empty:
                        pass
                self.results.put_nowait(found)
            except (OSError, ValueError, RuntimeError):
                pass
            self.stop.wait(10)

    def set_ignored(self, instance_id, ignored=True):
        with self.lock:
            key = instance_id.casefold()
            if ignored:
                self.ignored.add(key)
            else:
                self.ignored.discard(key)
            data = json.dumps({"version": 1, "ignored": sorted(self.ignored)}, indent=2)
            self.path.write_text(data, encoding="utf-8")

    def take(self):
        try:
            found = self.results.get_nowait()
            with self.lock:
                return [r for r in found if r.instance_id.casefold() not in self.ignored]
        except queue.Empty:
            return []

    def close(self):
        self.stop.set()
