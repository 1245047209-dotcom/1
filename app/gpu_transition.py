"""Hardware-only Direct2D transition surfaces; Tk remains the layout/input owner.

Capture each visible content region once, upload once, then composite its
opacity/translation on the GPU. No framebuffer readback occurs per frame.
Failure returns to the existing Tk renderer, without changing device I/O.
"""
import ctypes as c
from ctypes import wintypes as w
import math
import os
import threading
import uuid


class Size(c.Structure):
    _fields_ = [("width", w.UINT), ("height", w.UINT)]


class PixelFormat(c.Structure):
    _fields_ = [("format", w.UINT), ("alpha", w.UINT)]


class TargetProperties(c.Structure):
    _fields_ = [("kind", w.UINT), ("pixel", PixelFormat), ("dpi_x", c.c_float),
                ("dpi_y", c.c_float), ("usage", w.UINT), ("level", w.UINT)]


class WindowProperties(c.Structure):
    _fields_ = [("hwnd", w.HWND), ("size", Size), ("present", w.UINT)]


class BitmapProperties(c.Structure):
    _fields_ = [("pixel", PixelFormat), ("dpi_x", c.c_float), ("dpi_y", c.c_float)]


class Rect(c.Structure):
    _fields_ = [("left", c.c_float), ("top", c.c_float), ("right", c.c_float), ("bottom", c.c_float)]


class Color(c.Structure):
    _fields_ = [("r", c.c_float), ("g", c.c_float), ("b", c.c_float), ("a", c.c_float)]


class BitmapInfo(c.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                ("image_size", w.DWORD), ("xppm", w.LONG), ("yppm", w.LONG),
                ("colors", w.DWORD), ("important", w.DWORD)]


def com(pointer, index, result, arguments=()):
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[index])


def release(pointer):
    if pointer:
        com(pointer, 2, w.ULONG)(pointer)


def check(result, operation):
    if result < 0:
        raise OSError(f"{operation}: 0x{result & 0xffffffff:08X}")


class Direct2D:
    def __init__(self):
        self.user = c.WinDLL("user32", use_last_error=True)
        self.gdi = c.WinDLL("gdi32", use_last_error=True)
        signatures = {
            "CreateWindowExW": ([w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int,
                                 c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p], w.HWND),
            "DestroyWindow": ([w.HWND], w.BOOL),
            "ShowWindow": ([w.HWND, c.c_int], w.BOOL),
            "SetWindowLongPtrW": ([w.HWND, c.c_int, c.c_ssize_t], c.c_ssize_t),
            "CallWindowProcW": ([c.c_ssize_t, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t], c.c_ssize_t),
            "ValidateRect": ([w.HWND, c.c_void_p], w.BOOL),
            "InvalidateRect": ([w.HWND, c.c_void_p, w.BOOL], w.BOOL),
            "UpdateWindow": ([w.HWND], w.BOOL),
            "GetDC": ([w.HWND], w.HDC),
            "ReleaseDC": ([w.HWND, w.HDC], c.c_int),
        }
        for name, (args, restype) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, restype
        for name, args, restype in (
                ("CreateCompatibleDC", [w.HDC], w.HDC),
                ("CreateDIBSection", [w.HDC, c.POINTER(BitmapInfo), w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD], w.HBITMAP),
                ("SelectObject", [w.HDC, w.HANDLE], w.HANDLE),
                ("DeleteObject", [w.HANDLE], w.BOOL), ("DeleteDC", [w.HDC], w.BOOL),
                ("BitBlt", [w.HDC, c.c_int, c.c_int, c.c_int, c.c_int, w.HDC, c.c_int, c.c_int, w.DWORD], w.BOOL)):
            function = getattr(self.gdi, name)
            function.argtypes, function.restype = args, restype
        self.factory = c.c_void_p()
        create = c.WinDLL("d2d1").D2D1CreateFactory
        create.argtypes = [w.UINT, c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)]
        create.restype = c.c_long
        iid = (c.c_ubyte * 16).from_buffer_copy(uuid.UUID("06152247-6f50-465a-9245-118bfd3b6007").bytes_le)
        check(create(1, iid, None, c.byref(self.factory)), "D2D1CreateFactory")  # MULTI_THREADED: background warmup.

    def capture(self, hwnd, rectangle):
        x, y, width, height = rectangle
        if width < 1 or height < 1 or width * height > 16 * 1024 * 1024:
            raise ValueError("GPU surface dimensions are outside the bounded capture budget")
        source = self.user.GetDC(hwnd)
        memory = self.gdi.CreateCompatibleDC(source) if source else None
        bitmap, previous = None, None
        try:
            bits = c.c_void_p()
            info = BitmapInfo(c.sizeof(BitmapInfo), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            bitmap = self.gdi.CreateDIBSection(source, c.byref(info), 0, c.byref(bits), None, 0) if memory else None
            if not bitmap or not bits:
                raise OSError("Cannot prepare transition bitmap")
            previous = self.gdi.SelectObject(memory, bitmap)
            if not self.gdi.BitBlt(memory, 0, 0, width, height, source, x, y, 0x00CC0020):
                raise OSError("Cannot capture transition content")
            return c.string_at(bits, width * height * 4)
        finally:
            if previous:
                self.gdi.SelectObject(memory, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)
            if source:
                self.user.ReleaseDC(hwnd, source)

    def close(self):
        release(self.factory)
        self.factory = None


class Surface:
    def __init__(self, native, canvas, rectangle, pixels):
        self.native, self.canvas, self.rectangle = native, canvas, rectangle
        self.hwnd, self.previous, self.target, self.bitmap = None, None, None, None
        self.error, self.last = None, (1., 0.)
        rgb = canvas.winfo_rgb(canvas.cget("bg")) if canvas else (65535, 65535, 65535)
        self.background = Color(*(value/65535 for value in rgb), 1.)
        self.callback = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)(self.window_proc)
        x, y, width, height = rectangle
        try:
            # The child is a presentation surface only; HTTRANSPARENT keeps all
            # input, accessibility and hit tests on the original Tk canvas.
            self.hwnd = native.user.CreateWindowExW(0x20, "STATIC", "", 0x54000000 if canvas else 0x80000000,
                                                    x, y, width, height, canvas.winfo_id() if canvas else None,
                                                    None, None, None)
            if not self.hwnd:
                raise OSError("Cannot create GPU transition surface")
            self.previous = native.user.SetWindowLongPtrW(self.hwnd, -4, c.cast(self.callback, c.c_void_p).value)
            if not self.previous:
                raise OSError("Cannot attach GPU presentation window")
            self.target = c.c_void_p()
            props = TargetProperties(2, PixelFormat(87, 3), 96., 96., 0, 0)  # HARDWARE only.
            window = WindowProperties(self.hwnd, Size(width, height), 2)  # IMMEDIATELY; DWM composes.
            check(com(native.factory, 14, c.c_long, (c.POINTER(TargetProperties), c.POINTER(WindowProperties),
                      c.POINTER(c.c_void_p)))(native.factory, c.byref(props), c.byref(window), c.byref(self.target)),
                  "CreateHwndRenderTarget(HARDWARE)")
            self.bitmap = c.c_void_p()
            bitmap_props = BitmapProperties(PixelFormat(87, 3), 96., 96.)
            check(com(self.target, 4, c.c_long, (Size, c.c_void_p, w.UINT, c.POINTER(BitmapProperties),
                      c.POINTER(c.c_void_p)))(self.target, Size(width, height), pixels, width*4,
                                            c.byref(bitmap_props), c.byref(self.bitmap)), "CreateBitmap")
        except Exception:
            self.close()
            raise

    def window_proc(self, hwnd, message, wp, lp):
        if message == 0x84:  # WM_NCHITTEST
            return -1
        if message == 0x14:  # WM_ERASEBKGND
            return 1
        if message == 0xF and self.target and self.bitmap:
            try:
                self.draw(*self.last)
            except Exception as exc:
                self.error = str(exc)
            self.native.user.ValidateRect(hwnd, None)
            return 0
        return self.native.user.CallWindowProcW(self.previous, hwnd, message, wp, lp)

    def draw(self, alpha, offset):
        if self.error:
            raise OSError(self.error)
        self.last = alpha, offset
        width, height = self.rectangle[2:]
        destination = Rect(offset, 0., width+offset, float(height))
        com(self.target, 48, None)(self.target)
        com(self.target, 47, None, (c.POINTER(Color),))(self.target, c.byref(self.background))
        com(self.target, 26, None, (c.c_void_p, c.POINTER(Rect), c.c_float, w.UINT, c.c_void_p))(
            self.target, self.bitmap, c.byref(destination), max(0., min(1., alpha)), 1, None)
        check(com(self.target, 49, c.c_long, (c.c_void_p, c.c_void_p))(self.target, None, None), "EndDraw")

    def close(self):
        if self.hwnd:
            if self.previous:
                self.native.user.SetWindowLongPtrW(self.hwnd, -4, self.previous)
            self.native.user.DestroyWindow(self.hwnd)
            self.hwnd = None
        release(self.bitmap)
        release(self.target)
        self.bitmap, self.target = None, None


def bounds(canvas, items, region=None):
    if not items or not canvas.winfo_ismapped():
        return None
    box = ((region[0], region[1], region[0]+region[2], region[1]+region[3])
           if region is not None else canvas.bbox(*items))
    if not box:
        return None
    ox, oy = (0, 0) if region is not None else (canvas.canvasx(0), canvas.canvasy(0))
    x1, y1 = max(0, math.floor(box[0]-ox)), max(0, math.floor(box[1]-oy))
    x2 = min(canvas.winfo_width(), math.ceil(box[2]-ox))
    y2 = min(canvas.winfo_height(), math.ceil(box[3]-oy))
    return (x1, y1, x2-x1, y2-y1) if x2 > x1 and y2 > y1 else None


class GpuTransitions:
    def __init__(self, root, *, warm=False):
        self.root, self.native, self.surfaces = root, None, []
        self.enabled, self.failed = True, ""
        self.frames, self.uploads = 0, 0
        self._closed, self._warming = False, bool(warm)
        self._lock = threading.Lock()
        if warm:
            threading.Thread(target=self._warm, name="NexusGpuWarmup", daemon=True).start()

    def _warm(self):
        native, surface = None, None
        try:
            native = Direct2D()
            # This hidden window belongs entirely to the warmup thread. No Tk
            # call or UI-thread HWND is touched by the worker.
            surface = Surface(native, None, (0, 0, 16, 16), bytes(16*16*4))
            surface.draw(0., 0.)
            surface.close()
            surface = None
            with self._lock:
                if not self._closed:
                    self.native, native = native, None
        except Exception as exc:
            with self._lock:
                self.failed = str(exc)
        finally:
            if surface is not None:
                surface.close()
            if native is not None:
                native.close()
            self._warming = False

    @property
    def active(self):
        return bool(self.surfaces)

    def capture(self, layers, alpha=1., offset=0.):
        if not self.enabled or self.failed or self._closed or self._warming:
            return False
        self.clear()
        try:
            if self.native is None:
                self.native = Direct2D()
            self.root.update_idletasks()
            captures = []
            for layer in layers:
                canvas = layer["canvas"]
                rectangle = bounds(canvas, [item for item, _style in layer["entries"]], layer.get("region"))
                if rectangle:
                    self.native.user.InvalidateRect(canvas.winfo_id(), None, False)
                    self.root.update_idletasks()
                    captures.append((canvas, rectangle, self.native.capture(canvas.winfo_id(), rectangle)))
            for canvas, rectangle, pixels in captures:
                self.surfaces.append(Surface(self.native, canvas, rectangle, pixels))
                self.surfaces[-1].draw(alpha, offset)
                self.uploads += 1
            return self.active
        except Exception as exc:
            self.fail(exc)
            return False

    def paint(self, alpha, offset):
        try:
            for surface in self.surfaces:
                surface.draw(alpha, offset)
            self.frames += 1
            return self.active
        except Exception as exc:
            self.fail(exc)
            return False

    def fail(self, error):
        self.failed = str(error)
        self.clear()

    def clear(self):
        for surface in self.surfaces:
            surface.close()
        self.surfaces.clear()

    def close(self):
        self.clear()
        with self._lock:
            self._closed = True
            native, self.native = self.native, None
        if native is not None:
            native.close()


def create_backend(root):
    if os.name != "nt" or not hasattr(root, "tk") or os.environ.get("NEXUS_RENDERER") == "software":
        return None
    return GpuTransitions(root, warm=True)
