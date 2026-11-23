"""Desktop Acrylic behind the Python/Tk main window on Windows 11.

Tk paints with GDI, whose RGB output normally has no coverage byte. Acrylic
needs premultiplied BGRA instead. Normalize only the app's own canvas DCs after
Tk paints, leaving DWM to composite whatever is behind the window.
"""

import ctypes as ct
from ctypes import wintypes as wt
import sys
from tkinter import TclError


_SRCCOPY = 0x00CC0020
_DWM_DARK = 20
_DWM_CAPTION_COLOR = 35
_DWM_TEXT_COLOR = 36
_DWM_BACKDROP = 38
_DWM_DEFAULT_COLOR = 0xFFFFFFFF
_DWM_ACRYLIC = 3
_DWM_NONE = 1


class _Margins(ct.Structure):
    _fields_ = [(side, ct.c_int) for side in ('left', 'right', 'top', 'bottom')]


class _BitmapInfo(ct.Structure):
    _fields_ = [
        ('size', wt.DWORD), ('width', wt.LONG), ('height', wt.LONG),
        ('planes', wt.WORD), ('bits', wt.WORD), ('compression', wt.DWORD),
        ('image_size', wt.DWORD), ('x_ppm', wt.LONG), ('y_ppm', wt.LONG),
        ('colors', wt.DWORD), ('important', wt.DWORD),
    ]


def _api(dll, name, result, *parameters):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = parameters
    return function


class _Native:
    def __init__(self):
        user = ct.WinDLL('user32')
        gdi = ct.WinDLL('gdi32')
        dwm = ct.WinDLL('dwmapi')
        self.root_hwnd = _api(user, 'GetAncestor', wt.HWND, wt.HWND, wt.UINT)
        self.window = _api(user, 'IsWindow', wt.BOOL, wt.HWND)
        self.client = _api(user, 'GetClientRect', wt.BOOL, wt.HWND, ct.POINTER(wt.RECT))
        self.visible = _api(user, 'IsWindowVisible', wt.BOOL, wt.HWND)
        self.invalidate = _api(user, 'InvalidateRect', wt.BOOL, wt.HWND, ct.c_void_p, wt.BOOL)
        self.iconic = _api(user, 'IsIconic', wt.BOOL, wt.HWND)
        self.get_dc = _api(user, 'GetDC', wt.HDC, wt.HWND)
        self.release_dc = _api(user, 'ReleaseDC', ct.c_int, wt.HWND, wt.HDC)
        self.compatible_dc = _api(gdi, 'CreateCompatibleDC', wt.HDC, wt.HDC)
        self.delete_dc = _api(gdi, 'DeleteDC', wt.BOOL, wt.HDC)
        self.dib = _api(gdi, 'CreateDIBSection', wt.HBITMAP, wt.HDC, ct.POINTER(_BitmapInfo),
                        wt.UINT, ct.POINTER(ct.c_void_p), wt.HANDLE, wt.DWORD)
        self.select = _api(gdi, 'SelectObject', wt.HANDLE, wt.HDC, wt.HANDLE)
        self.delete = _api(gdi, 'DeleteObject', wt.BOOL, wt.HANDLE)
        self.blit = _api(gdi, 'BitBlt', wt.BOOL, wt.HDC, ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                         wt.HDC, ct.c_int, ct.c_int, wt.DWORD)
        self.flush = _api(gdi, 'GdiFlush', wt.BOOL)
        self.set_dwm = _api(dwm, 'DwmSetWindowAttribute', wt.LONG,
                            wt.HWND, wt.DWORD, ct.c_void_p, wt.DWORD)
        self.extend = _api(dwm, 'DwmExtendFrameIntoClientArea', wt.LONG, wt.HWND, ct.POINTER(_Margins))

    def attribute(self, hwnd, attribute, value):
        argument = wt.DWORD(value)
        return self.set_dwm(hwnd, attribute, ct.byref(argument), ct.sizeof(argument)) >= 0


class _Coverage:
    """Convert a canvas buffer using independent arithmetic in each 32-bit pixel."""

    def __init__(self, pixels):
        self.length = pixels * 4
        self.ones = int.from_bytes(b'\x01\0\0\0' * pixels, 'little')
        self.flags = self.ones << 24
        self.rgb_mask = self.flags - self.ones

    def _equal(self, pixels, value):
        # Guard bit 24 in every lane prevents subtraction crossing pixel boundaries.
        different = (((pixels ^ (self.ones * value)) | self.flags) - self.ones) & self.flags
        return self.flags ^ different

    @staticmethod
    def _premultiply(color, opacity):
        channels = ((color >> shift) & 255 for shift in (0, 8, 16))
        return sum(((channel * opacity + 127) // 255) << shift
                   for channel, shift in zip(channels, (0, 8, 16)))

    def convert(self, source, background, surface, surface_alpha, tint):
        pixels = int.from_bytes(source, 'little')
        rgb = pixels & self.rgb_mask
        alpha = (pixels >> 24) & (self.ones * 255)
        # Recognize pixels that already carry our alpha on a partial Tk repaint.
        # Light glass uses alpha 1 for its clear areas, leaving alpha 0 for raw GDI.
        tint_alpha = tint >> 24
        tint_rgb = self._premultiply(tint & 0x00FFFFFF, tint_alpha)
        backdrop = self._equal(rgb, background) | (self._equal(rgb, tint_rgb) & self._equal(alpha, tint_alpha))
        premultiplied = self._premultiply(surface, surface_alpha)
        translucent = self._equal(rgb, surface) | (self._equal(rgb, premultiplied) & self._equal(alpha, surface_alpha))
        translucent &= self.flags ^ backdrop
        opaque = self.flags ^ (backdrop | translucent)
        opaque_rgb_mask = (opaque >> 24) * 0x00FFFFFF
        backdrop_pixel = (tint_alpha << 24) | tint_rgb
        translucent_pixel = (surface_alpha << 24) | premultiplied
        output = ((rgb & opaque_rgb_mask) | (opaque * 255)
                  | ((backdrop >> 24) * backdrop_pixel) | ((translucent >> 24) * translucent_pixel))
        return output.to_bytes(self.length, 'little')


class _Canvas:
    _REDRAW = frozenset({
        'configure', 'coords', 'create', 'dchars', 'delete', 'focus', 'icursor',
        'imove', 'insert', 'itemconfigure', 'lower', 'move', 'moveto', 'raise',
        'scale', 'select', 'xview', 'yview',
    })

    def __init__(self, owner, widget):
        self.owner = owner
        self.native = owner.native
        self.widget = widget
        self.hwnd = widget.winfo_id()
        self.alias = f'::nexus_acrylic_{id(self)}'
        self.idle = None
        self.size = (0, 0)
        self.memory = self.bitmap = self.old_bitmap = self.coverage = None
        self.bits = ct.c_void_p()
        widget.tk.call('rename', widget._w, self.alias)
        try:
            widget.tk.createcommand(widget._w, self._command)
        except TclError:
            widget.tk.call('rename', self.alias, widget._w)
            raise
        self.bindings = [(event, widget.bind(event, self._widget_event, add='+'))
                         for event in ('<Expose>', '<Configure>', '<Map>')]

    def _widget_event(self, _event):
        self.schedule()

    def _command(self, operation, *args):
        result = self.widget.tk.call(self.alias, operation, *args)
        # Tk may render a changed canvas from its own idle handler without a
        # WM_PAINT message. Queue coverage normalization after that draw as well.
        if operation in self._REDRAW and args:
            self.schedule()
        return result

    def schedule(self):
        if self.owner.active and self.hwnd and self.idle is None:
            try:
                self.idle = self.widget.after_idle(self._after_tk_paint)
            except TclError:
                pass

    def _after_tk_paint(self):
        # An idle batch can enqueue additional Tk/GDI work. Present on the
        # next event-loop tick, once that batch has finished drawing.
        self.idle = self.widget.after(1, self._present)

    def _release_buffers(self):
        if self.old_bitmap:
            self.native.select(self.memory, self.old_bitmap)
        if self.bitmap:
            self.native.delete(self.bitmap)
        if self.memory:
            self.native.delete_dc(self.memory)
        self.memory = self.bitmap = self.old_bitmap = self.coverage = None
        self.size = (0, 0)

    def _buffer(self, dc, width, height):
        if self.size == (width, height):
            return True
        self._release_buffers()
        self.memory = self.native.compatible_dc(dc)
        if not self.memory:
            return False
        info = _BitmapInfo(ct.sizeof(_BitmapInfo), width, -height, 1, 32)
        self.bitmap = self.native.dib(dc, ct.byref(info), 0, ct.byref(self.bits), None, 0)
        if not self.bitmap:
            return False
        self.old_bitmap = self.native.select(self.memory, self.bitmap)
        if not self.old_bitmap:
            return False
        self.coverage = _Coverage(width * height)
        self.size = (width, height)
        return True

    def _present(self):
        self.idle = None
        hwnd = self.hwnd
        native = self.native
        if not self.owner.active or not hwnd or not native.visible(hwnd) or native.iconic(self.owner.hwnd):
            return
        rect = wt.RECT()
        if not native.client(hwnd, ct.byref(rect)) or not (0 < rect.right * rect.bottom <= 2_000_000):
            return
        width, height = rect.right, rect.bottom
        dc = native.get_dc(hwnd)
        if not dc:
            return
        try:
            if self._buffer(dc, width, height) and native.blit(self.memory, 0, 0, width, height, dc, 0, 0, _SRCCOPY):
                native.flush()
                source = ct.string_at(self.bits, self.coverage.length)
                pixels = self.coverage.convert(source, *self.owner.palette)
                if pixels != source:
                    ct.memmove(self.bits, pixels, len(pixels))
                    native.blit(dc, 0, 0, width, height, self.memory, 0, 0, _SRCCOPY)
        finally:
            native.release_dc(hwnd, dc)

    def close(self):
        if self.idle is not None:
            try:
                self.widget.after_cancel(self.idle)
            except TclError:
                pass
            self.idle = None
        if self.native.window(self.hwnd):
            for event, binding in self.bindings:
                if binding:
                    try:
                        self.widget.unbind(event, binding)
                    except TclError:
                        pass
        try:
            self.widget.tk.deletecommand(self.widget._w)
            if self.widget.tk.call('info', 'commands', self.alias):
                self.widget.tk.call('rename', self.alias, self.widget._w)
        except TclError:
            pass
        self._release_buffers()


class Acrylic:
    """Enable the native backdrop and attach the canvas widgets layered above it."""

    def __init__(self, root):
        self.root = root
        self.native = self.hwnd = None
        self.canvases = {}
        self.active = self.dark = False
        if sys.platform == 'win32' and sys.getwindowsversion().build >= 22621:
            try:
                self.native = _Native()
            except (AttributeError, OSError):
                pass

    @property
    def background_color(self):
        return '#151923' if self.dark else '#edf1f7'

    @property
    def surface_color(self):
        return '#303744' if self.dark else '#f8f9fc'

    @property
    def wallpaper_allowed(self):
        return self.native is None or self._transparency_enabled()

    @property
    def palette(self):
        return (int(self.background_color[1:], 16), int(self.surface_color[1:], 16),
                98 if self.dark else 72, 0x46101823 if self.dark else 0x01000000)

    def attach(self, widget):
        if self.native is None:
            return False
        if widget not in self.canvases:
            try:
                self.canvases[widget] = _Canvas(self, widget)
            except (TclError, OSError):
                return False
        self.canvases[widget].schedule()
        return True

    def apply(self, enabled, dark):
        self.dark = bool(dark)
        if self.native is None:
            return False
        native = self.native
        if enabled:
            try:
                hwnd = (native.root_hwnd(self.root.winfo_id(), 2)
                        if self.root.winfo_ismapped() else None)
            except TclError:
                hwnd = None
            if not hwnd or not self._transparency_enabled():
                enabled = False
            else:
                self.hwnd = hwnd
                native.attribute(hwnd, _DWM_DARK, int(self.dark))
                native.attribute(hwnd, _DWM_CAPTION_COLOR, _DWM_DEFAULT_COLOR)
                native.attribute(hwnd, _DWM_TEXT_COLOR, _DWM_DEFAULT_COLOR)
                margins = _Margins(-1, -1, -1, -1)
                self.active = (native.extend(hwnd, ct.byref(margins)) >= 0
                               and native.attribute(hwnd, _DWM_BACKDROP, _DWM_ACRYLIC))
                enabled = self.active
        if not enabled:
            if self.hwnd and native.window(self.hwnd):
                native.attribute(self.hwnd, _DWM_BACKDROP, _DWM_NONE)
                native.extend(self.hwnd, ct.byref(_Margins()))
            self.active = False
        for canvas in self.canvases.values():
            if native.window(canvas.hwnd):
                native.invalidate(canvas.hwnd, None, False)
                canvas.schedule()
        return self.active

    @staticmethod
    def _transparency_enabled():
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return bool(winreg.QueryValueEx(key, 'EnableTransparency')[0])
        except OSError:
            return True

    def close(self):
        self.apply(False, self.dark)
        for canvas in self.canvases.values():
            canvas.close()
        self.canvases.clear()
        self.hwnd = None
