"""Deadline-based 60 Hz animation pacing, with no idle polling."""
import math
import sys
import time


class TimerResolution:
    """Share one Windows precision request across concurrent animations."""
    def __init__(self):
        self.users = 0
        self.api = None

    def acquire(self):
        if not self.users and sys.platform == "win32":
            try:
                import ctypes
                api = ctypes.WinDLL("winmm")
                if api.timeBeginPeriod(1) == 0:
                    self.api = api
            except (AttributeError, OSError):
                pass
        self.users += 1

    def release(self):
        if not self.users:
            return
        self.users -= 1
        if not self.users and self.api is not None:
            self.api.timeEndPeriod(1)
            self.api = None


_resolution = TimerResolution()


class FramePacer:
    PERIOD = 1 / 60

    def __init__(self, root, *, clock=time.perf_counter, resolution=None):
        self.root, self.clock = root, clock
        self.resolution = _resolution if resolution is None else resolution
        self.active, self.deadline = False, 0.0

    def start(self):
        if not self.active:
            self.resolution.acquire()
            self.active = True
        self.deadline = self.clock()

    def after(self, callback):
        if not self.active:
            self.start()
        now = self.clock()
        self.deadline += self.PERIOD
        if self.deadline <= now:
            self.deadline += (math.floor((now-self.deadline)/self.PERIOD)+1)*self.PERIOD
        delay = max(1, round((self.deadline-now)*1000))
        return self.root.after(delay, callback)

    def stop(self):
        if self.active:
            self.active = False
            self.resolution.release()
