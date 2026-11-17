from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from gpu_transition import GpuTransitions, bounds, create_backend
from ui_motion import DeviceTransition


class BoundsTests(unittest.TestCase):
    def canvas(self):
        return Mock(winfo_ismapped=Mock(return_value=True), bbox=Mock(return_value=(-10, 20, 500, 600)),
                    canvasx=Mock(return_value=0), canvasy=Mock(return_value=100),
                    winfo_width=Mock(return_value=400), winfo_height=Mock(return_value=300))

    def test_capture_is_clipped_to_the_visible_scrolled_viewport(self):
        self.assertEqual(bounds(self.canvas(), [1, 2]), (0, 0, 400, 300))

    def test_hidden_empty_and_outside_content_does_not_allocate_a_surface(self):
        canvas = self.canvas()
        self.assertIsNone(bounds(canvas, []))
        canvas.winfo_ismapped.return_value = False
        self.assertIsNone(bounds(canvas, [1]))

    def test_explicit_content_region_preserves_static_header_and_footer(self):
        canvas = self.canvas()
        self.assertEqual(bounds(canvas, [1], (0, 100, 400, 180)), (0, 100, 400, 180))
        canvas.winfo_ismapped.return_value = True
        canvas.bbox.return_value = (0, 800, 400, 900)
        self.assertIsNone(bounds(canvas, [1]))


class FailureTests(unittest.TestCase):
    def test_background_warmup_does_not_block_capture_or_touch_tk_and_close_is_safe(self):
        started, finish = threading.Event(), threading.Event()
        root, native, surface = Mock(), Mock(), Mock()
        def prepare(*args):
            started.set()
            finish.wait(2)
            return surface
        with patch("gpu_transition.Direct2D", return_value=native), patch("gpu_transition.Surface", side_effect=prepare):
            gpu = GpuTransitions(root, warm=True)
            self.assertTrue(started.wait(2))
            self.assertFalse(gpu.capture([]))
            gpu.close()
            finish.set()
            deadline = time.monotonic() + 2
            while gpu._warming and time.monotonic() < deadline:
                time.sleep(.001)
            self.assertFalse(gpu._warming)
        root.update_idletasks.assert_not_called()
        surface.close.assert_called_once()
        native.close.assert_called_once()

    def test_missing_device_returns_to_software_without_leaving_an_overlay(self):
        gpu = GpuTransitions(Mock())
        with patch("gpu_transition.Direct2D", side_effect=OSError("adapter unavailable")):
            self.assertFalse(gpu.capture([]))
        self.assertIn("adapter unavailable", gpu.failed)
        self.assertFalse(gpu.active)

    def test_partial_upload_failure_closes_already_created_surfaces(self):
        gpu = GpuTransitions(Mock())
        native, first = Mock(), Mock()
        canvas = BoundsTests().canvas()
        layers = [{"canvas": canvas, "entries": [(1, {})]}] * 2
        with patch("gpu_transition.Direct2D", return_value=native), \
                patch("gpu_transition.Surface", side_effect=[first, OSError("device lost")]):
            self.assertFalse(gpu.capture(layers))
        first.close.assert_called_once()
        self.assertFalse(gpu.active)

    def test_present_failure_closes_all_surfaces_and_allows_clean_shutdown(self):
        gpu = GpuTransitions(Mock())
        first, second = Mock(), Mock()
        first.draw.side_effect = OSError("target must be recreated")
        gpu.surfaces = [first, second]
        self.assertFalse(gpu.paint(.5, 8))
        first.close.assert_called_once()
        second.close.assert_called_once()
        gpu.close()
        gpu.close()
        self.assertFalse(gpu.active)


class MotionIntegrationTests(unittest.TestCase):
    def motion(self):
        root = SimpleNamespace(after_cancel=Mock(), after=Mock(return_value=1), winfo_rgb=Mock(return_value=(0, 0, 0)))
        return DeviceTransition(root, Mock(), gpu_enabled=False)

    def test_hardware_frames_never_generate_cpu_opacity_images(self):
        motion = self.motion()
        motion.gpu = Mock(active=True)
        motion.gpu.paint.return_value = True
        motion.fade_image = Mock(side_effect=AssertionError("CPU opacity generation in GPU path"))
        motion.paint_layer = Mock(side_effect=AssertionError("Per-item CPU rendering in GPU path"))
        motion.layers = [Mock()]
        motion.alpha, motion.offset = .5, 12
        motion.paint()
        motion.gpu.paint.assert_called_once_with(.5, 12)

    def test_failed_hardware_frame_immediately_uses_existing_renderer(self):
        motion = self.motion()
        motion.gpu = Mock(active=True)
        motion.gpu.paint.return_value = False
        motion.paint_layer = Mock()
        motion.layers = [Mock(), Mock()]
        motion.paint()
        self.assertEqual(motion.paint_layer.call_count, 2)

    def test_rapid_restart_reuses_current_gpu_texture_and_alpha(self):
        motion = self.motion()
        motion.gpu = Mock(active=True)
        motion.alpha, motion.offset = .4, 7
        motion.start()
        motion.gpu.capture.assert_not_called()
        self.assertEqual((motion.from_alpha, motion.from_offset), (.4, 7))

    def test_disabling_gpu_releases_device_and_restores_final_cpu_state(self):
        motion = self.motion()
        gpu = motion.gpu = Mock(active=True)
        motion.set_gpu_enabled(False)
        gpu.close.assert_called_once()
        self.assertIsNone(motion.gpu)
        self.assertEqual((motion.phase, motion.alpha, motion.offset), ("idle", 1, 0))

    def test_close_releases_gpu_even_during_preparation(self):
        motion = self.motion()
        gpu = motion.gpu = Mock()
        motion.phase = "prepare"
        motion.close()
        gpu.close.assert_called_once()
        self.assertEqual(motion.phase, "idle")

    def test_software_override_and_non_tk_roots_never_create_native_backend(self):
        with patch.dict("os.environ", {"NEXUS_RENDERER": "software"}):
            self.assertIsNone(create_backend(SimpleNamespace(tk=Mock())))
        self.assertIsNone(create_backend(SimpleNamespace()))
