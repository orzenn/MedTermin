import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"


class StreamlitTests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(str(APP), default_timeout=20).run()

    def assert_no_errors(self):
        self.assertEqual(len(self.app.exception), 0, list(self.app.exception))

    def search_example(self):
        self.app.checkbox(key="online").uncheck().run()
        self.app.button(key="example_asthma").click().run()

    def test_page_renders_and_search_shows_evidence_tabs(self):
        self.assert_no_errors()
        self.search_example()
        self.assert_no_errors()
        self.assertEqual(self.app.session_state["result"]["title"], "Бронхиальная астма")
        self.assertEqual([tab.label for tab in self.app.tabs], ["Объяснение", "Источники", "Работа агентов"])
        self.assertTrue(any("готова" in status.label for status in self.app.status))

    def test_saved_result_survives_reruns_and_can_be_opened_and_removed(self):
        self.search_example()
        self.app.button(key="save_result").click().run()
        self.assert_no_errors()
        self.assertEqual(len(self.app.session_state["saved"]), 1)
        self.app.radio(key="navigation").set_value("Сохранённое").run()
        self.app.button(key="open_D001249").click().run()
        self.assert_no_errors()
        self.assertEqual(self.app.radio(key="navigation").value, "Справочник")
        self.app.button(key="save_result").click().run()
        self.assertEqual(len(self.app.session_state["saved"]), 0)

    def test_unknown_query_shows_error_and_clears_previous_result(self):
        self.search_example()
        self.app.text_input(key="query").set_value("unknown-disease-xyz")
        self.app.button(key="FormSubmitter:search_form-Объяснить ↗").click().run()
        self.assert_no_errors()
        self.assertIsNone(self.app.session_state["result"])
        self.assertGreater(len(self.app.error), 0)

    def test_about_and_official_dataset_search(self):
        self.app.radio(key="navigation").set_value("О проекте").run()
        self.assert_no_errors()
        self.app.radio(key="navigation").set_value("Справочник").run()
        self.app.checkbox(key="online").uncheck().run()
        self.app.text_input(key="query").set_value("A1C")
        self.app.button(key="FormSubmitter:search_form-Объяснить ↗").click().run()
        self.assert_no_errors()
        self.assertEqual(self.app.session_state["result"]["medline"]["provenance"], "dataset")
        self.assertEqual(self.app.session_state["result"]["language"], "en")

    def test_russian_scoliosis_search_reaches_the_source(self):
        self.app.checkbox(key="online").uncheck().run()
        self.app.text_input(key="query").set_value("сколиоз")
        self.app.button(key="FormSubmitter:search_form-Объяснить ↗").click().run()
        self.assert_no_errors()
        result = self.app.session_state["result"]
        self.assertEqual(result["english"], "Scoliosis")
        self.assertEqual(result["medline"]["url"], "https://medlineplus.gov/scoliosis.html")


if __name__ == "__main__":
    unittest.main()
