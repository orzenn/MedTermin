import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from medical_guide.agents import GuideService
from medical_guide.runtime import get_service, load_cached_service, supports_current_api
import medical_guide.runtime as runtime_module

DATA = Path(__file__).resolve().parents[1] / "data"


class LegacyService:
    """The pre-update signature responsible for the reported production failure."""
    def search(self, query, *, online=True, selected_id=None, on_event=None):
        return None


class RuntimeTests(unittest.TestCase):
    def test_legacy_service_is_detected(self):
        self.assertFalse(supports_current_api(LegacyService()))
        self.assertTrue(supports_current_api(GuideService(DATA)))

    def test_cached_legacy_service_is_replaced_before_a_search(self):
        current = GuideService(DATA)
        loader = Mock(side_effect=[LegacyService(), current])
        with patch("medical_guide.runtime.load_cached_service", loader):
            service = get_service(DATA)
            result = service.search("Артериальная гипертензия", online=False, selected_english=None)
        loader.clear.assert_called_once()
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(result["title"], "Артериальная гипертензия")

    def test_stale_imported_module_is_reloaded(self):
        legacy_module = Mock()
        legacy_module.GuideService.return_value = LegacyService()
        fresh_module = Mock()
        fresh_module.GuideService.return_value = GuideService(DATA)
        load_cached_service.clear()
        with patch.object(runtime_module.importlib, "import_module", return_value=legacy_module), \
             patch.object(runtime_module.importlib, "reload", side_effect=[Mock(), fresh_module]) as reload_module:
            service = load_cached_service(str(DATA), "test-stale-module-recovery")
        self.assertTrue(supports_current_api(service))
        self.assertEqual(reload_module.call_count, 2)
        load_cached_service.clear()


if __name__ == "__main__":
    unittest.main()
