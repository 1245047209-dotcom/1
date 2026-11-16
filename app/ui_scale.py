"""Uniform Tk presentation scaling; GDI+ preserves PNG alpha without packages."""
import atexit
import ctypes as c
from ctypes import wintypes as w
import tkinter as tk
import tkinter.font as tkfont


class _Startup(c.Structure):
    _fields_ = [("version", w.UINT), ("callback", c.c_void_p), ("no_thread", w.BOOL), ("no_codecs", w.BOOL)]


class NativeImages:
    def __init__(self):
        self.gdi = c.WinDLL("gdiplus")
        self.kernel = c.WinDLL("kernel32")
        self.ole = c.WinDLL("ole32")
        signatures = {
            "GdiplusStartup": ([c.POINTER(c.c_size_t), c.POINTER(_Startup), c.c_void_p], c.c_int),
            "GdiplusShutdown": ([c.c_size_t], None),
            "GdipLoadImageFromStream": ([c.c_void_p, c.POINTER(c.c_void_p)], c.c_int),
            "GdipImageRotateFlip": ([c.c_void_p, c.c_int], c.c_int),
            "GdipCreateBitmapFromScan0": ([c.c_int, c.c_int, c.c_int, c.c_int, c.c_void_p, c.POINTER(c.c_void_p)], c.c_int),
            "GdipGetImageGraphicsContext": ([c.c_void_p, c.POINTER(c.c_void_p)], c.c_int),
            "GdipSetCompositingMode": ([c.c_void_p, c.c_int], c.c_int),
            "GdipSetInterpolationMode": ([c.c_void_p, c.c_int], c.c_int),
            "GdipDrawImageRectI": ([c.c_void_p, c.c_void_p, c.c_int, c.c_int, c.c_int, c.c_int], c.c_int),
            "GdipSaveImageToStream": ([c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p], c.c_int),
            "GdipDeleteGraphics": ([c.c_void_p], c.c_int),
            "GdipDisposeImage": ([c.c_void_p], c.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.gdi, name)
            function.argtypes, function.restype = args, result
        self.kernel.GlobalAlloc.argtypes, self.kernel.GlobalAlloc.restype = [w.UINT, c.c_size_t], c.c_void_p
        self.kernel.GlobalLock.argtypes, self.kernel.GlobalLock.restype = [c.c_void_p], c.c_void_p
        self.kernel.GlobalUnlock.argtypes = [c.c_void_p]
        self.kernel.GlobalSize.argtypes, self.kernel.GlobalSize.restype = [c.c_void_p], c.c_size_t
        self.kernel.GlobalFree.argtypes, self.kernel.GlobalFree.restype = [c.c_void_p], c.c_void_p
        self.ole.CreateStreamOnHGlobal.argtypes = [c.c_void_p, w.BOOL, c.POINTER(c.c_void_p)]
        self.ole.GetHGlobalFromStream.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
        self.token = c.c_size_t()
        self.call("GdiplusStartup", c.byref(self.token), c.byref(_Startup(1, None, False, False)), None)
        atexit.register(self.gdi.GdiplusShutdown, self.token.value)

    def call(self, name, *args):
        status = getattr(self.gdi, name)(*args)
        if status:
            raise RuntimeError(f"Image scaling failed: {name} ({status})")

    def stream(self, data=None):
        memory = None
        if data is not None:
            memory = self.kernel.GlobalAlloc(2, len(data))
            if not memory:
                raise MemoryError("Image scaling allocation failed")
            pointer = self.kernel.GlobalLock(memory)
            if not pointer:
                self.kernel.GlobalFree(memory)
                raise MemoryError("Image scaling memory unavailable")
            try:
                c.memmove(pointer, data, len(data))
            finally:
                self.kernel.GlobalUnlock(memory)
        result = c.c_void_p()
        if self.ole.CreateStreamOnHGlobal(memory, True, c.byref(result)):
            if memory:
                self.kernel.GlobalFree(memory)
            raise RuntimeError("Image scaling stream failed")
        return result

    @staticmethod
    def release(stream):
        if stream:
            table = c.cast(stream, c.POINTER(c.POINTER(c.c_void_p))).contents
            c.WINFUNCTYPE(w.ULONG, c.c_void_p)(table[2])(stream)

    def resize(self, data, width, height, *, clockwise=False):
        import uuid
        input_stream = self.stream(data)
        output_stream = None
        image, bitmap, graphics = c.c_void_p(), c.c_void_p(), c.c_void_p()
        try:
            self.call("GdipLoadImageFromStream", input_stream, c.byref(image))
            if clockwise:
                self.call("GdipImageRotateFlip", image, 1)
            self.call("GdipCreateBitmapFromScan0", width, height, 0, 0x26200A, None, c.byref(bitmap))
            self.call("GdipGetImageGraphicsContext", bitmap, c.byref(graphics))
            self.call("GdipSetCompositingMode", graphics, 1)
            self.call("GdipSetInterpolationMode", graphics, 7)
            self.call("GdipDrawImageRectI", graphics, image, 0, 0, width, height)
            self.call("GdipDeleteGraphics", graphics)
            graphics = c.c_void_p()
            output_stream = self.stream()
            encoder = (c.c_ubyte * 16).from_buffer_copy(uuid.UUID("557cf406-1a04-11d3-9a73-0000f81ef32e").bytes_le)
            self.call("GdipSaveImageToStream", bitmap, output_stream, encoder, None)
            memory = c.c_void_p()
            if self.ole.GetHGlobalFromStream(output_stream, c.byref(memory)):
                raise RuntimeError("Image scaling output unavailable")
            pointer = self.kernel.GlobalLock(memory)
            if not pointer:
                raise MemoryError("Image scaling output memory unavailable")
            try:
                result = c.string_at(pointer, self.kernel.GlobalSize(memory))
            finally:
                self.kernel.GlobalUnlock(memory)
            # The memory stream's allocation can be larger than the encoded PNG.
            position = 8
            while position + 12 <= len(result):
                end = position + 12 + int.from_bytes(result[position:position + 4], "big")
                if result[position + 4:position + 8] == b"IEND":
                    return result[:end]
                position = end
            raise ValueError("Scaled PNG output is incomplete")
        finally:
            if graphics:
                self.gdi.GdipDeleteGraphics(graphics)
            if bitmap:
                self.gdi.GdipDisposeImage(bitmap)
            if image:
                self.gdi.GdipDisposeImage(image)
            self.release(output_stream)
            self.release(input_stream)


class UIScale:
    def __init__(self, root, factor=.85):
        self.root, self.factor = root, factor
        self.fonts, self.images = {}, {}
        self.native = NativeImages()
        self.pixels_per_point = float(root.tk.call("tk", "scaling"))

    def px(self, value):
        return round(value * self.factor)

    def logical(self, value):
        return value / self.factor

    def place(self, widget, **options):
        widget.place(**{key: self.px(value) if key in ("x", "y", "width", "height") else value
                        for key, value in options.items()})

    def font(self, specification):
        key = str(specification)
        if key not in self.fonts:
            original = tkfont.Font(root=self.root, font=specification).actual()
            size = original["size"]
            pixels = abs(size) * (self.pixels_per_point if size > 0 else 1)
            original["size"] = -max(1, self.px(pixels))
            self.fonts[key] = tkfont.Font(root=self.root, **original)
        return self.fonts[key]

    def image(self, name):
        if name not in self.images:
            width = int(self.root.tk.call("image", "width", name))
            height = int(self.root.tk.call("image", "height", name))
            data = self.root.tk.call(name, "data", "-format", "png")
            result = self.native.resize(data, max(1, self.px(width)), max(1, self.px(height)))
            self.images[name] = tk.PhotoImage(master=self.root, data=result, format="PNG")
        return self.images[name]

    def canvas(self, canvas):
        for _ in self.canvas_steps(canvas):
            pass

    def canvas_steps(self, canvas):
        canvas.scale("all", 0, 0, self.factor, self.factor)
        for item in canvas.find_all():
            kind = canvas.type(item)
            if kind == "image":
                name = canvas.itemcget(item, "image")
                if name:
                    canvas.itemconfigure(item, image=self.image(name))
            elif kind == "text":
                options = {"font": self.font(canvas.itemcget(item, "font"))}
                width = float(canvas.itemcget(item, "width"))
                if width:
                    options["width"] = width * self.factor
                canvas.itemconfigure(item, **options)
            elif kind in ("line", "rectangle", "oval", "arc", "polygon"):
                width = float(canvas.itemcget(item, "width"))
                canvas.itemconfigure(item, width=width * self.factor)
            yield
