"""Build a replacement scene outside the viewport, then commit it together."""
import tkinter as tk


class SceneCanvas(tk.Canvas):
    SHIFT = 4096

    def __init__(self, master, **options):
        super().__init__(master, **options)
        self.staging = False
        self.scene_items, self.old_items = [], ()

    def begin_scene(self):
        self.abort_scene()
        self.old_items = super().find_all()
        self.scene_items = []
        self.staging = True

    def _create(self, item_type, args, options):
        item = super()._create(item_type, args, options)
        if self.staging:
            self.scene_items.append(item)
            super().addtag_withtag("scene_build", item)
            super().move(item, 0, self.SHIFT)
        return item

    def find_all(self):
        return tuple(self.scene_items) if self.staging else super().find_all()

    def delete(self, *items):
        if self.staging and "all" in items:
            if self.scene_items:
                super().delete(*self.scene_items)
            self.scene_items.clear()
            return
        super().delete(*items)
        if self.staging:
            removed = set(items)
            self.scene_items = [item for item in self.scene_items if item not in removed]

    def coords(self, item, *coordinates):
        if not self.staging or item not in self.scene_items:
            return super().coords(item, *coordinates)
        if coordinates:
            values = list(tk._flatten(coordinates))
            values[1::2] = [y+self.SHIFT for y in values[1::2]]
            return super().coords(item, *values)
        values = super().coords(item)
        values[1::2] = [y-self.SHIFT for y in values[1::2]]
        return values

    def bbox(self, *items):
        bounds = super().bbox(*items)
        if bounds and self.staging and all(item in self.scene_items for item in items):
            return bounds[0], bounds[1]-self.SHIFT, bounds[2], bounds[3]-self.SHIFT
        return bounds

    def scale(self, tag, x, y, x_scale, y_scale):
        if self.staging and tag == "all":
            return super().scale("scene_build", x, y+self.SHIFT, x_scale, y_scale)
        return super().scale(tag, x, y, x_scale, y_scale)

    def commit_scene(self):
        if not self.staging:
            return
        if self.old_items:
            super().delete(*self.old_items)
        super().move("scene_build", 0, -self.SHIFT)
        super().dtag("scene_build", "scene_build")
        self.staging = False
        self.old_items, self.scene_items = (), []

    def abort_scene(self):
        if self.staging:
            if self.scene_items:
                super().delete(*self.scene_items)
            self.staging = False
            self.old_items, self.scene_items = (), []
