from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aula_discovery import AulaDiscovery


class DiscoveryStartupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "discovery.json"

    def test_deferred_worker_can_only_start_once(self):
        with patch.object(AulaDiscovery, "_run") as scan:
            discovery = AulaDiscovery(self.path, autostart=False)
            self.addCleanup(discovery.close)
            self.assertIsNone(discovery.thread.ident)
            scan.assert_not_called()

            discovery.start()
            discovery.thread.join(timeout=2)
            discovery.start()
            self.assertFalse(discovery.thread.is_alive())
            scan.assert_called_once()

    def test_closing_before_start_prevents_the_worker_from_starting(self):
        with patch.object(AulaDiscovery, "_run") as scan:
            discovery = AulaDiscovery(self.path, autostart=False)
            discovery.close()
            discovery.start()

            self.assertIsNone(discovery.thread.ident)
            scan.assert_not_called()

    def test_the_default_constructor_still_starts_the_worker(self):
        with patch.object(AulaDiscovery, "_run") as scan:
            discovery = AulaDiscovery(self.path)
            self.addCleanup(discovery.close)
            discovery.thread.join(timeout=2)

            self.assertFalse(discovery.thread.is_alive())
            scan.assert_called_once()
