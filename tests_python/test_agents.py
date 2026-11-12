import json
import unittest
from pathlib import Path

from medical_guide.agents import GuideService
from medical_guide.core import (SourceError, export_markdown, extract_summary, matches_document,
                               normalize, parse_medline_xml, safe_medline_url, split_sentences, validate_query)

DATA = Path(__file__).resolve().parents[1] / "data"


class OfflineClient:
    def __init__(self):
        self.calls = []

    def json(self, url, timeout=6):
        self.calls.append(url)
        raise OSError("Simulated source outage")

    text = json


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.client = OfflineClient()
        self.service = GuideService(DATA, self.client)

    def test_russian_aliases_do_not_guess_longer_diagnoses(self):
        self.assertEqual(normalize("  ЛЁГКИХ "), "легких")
        self.assertEqual(self.service.aliases[normalize("воспаление легких")]["key"], "pneumonia")
        self.assertNotIn("диабет", self.service.aliases)
        self.assertNotIn("анемия при беременности", self.service.aliases)

    def test_input_validation(self):
        for value in (None, "", "a", "x" * 121, "<script>", "ab\x00cd"):
            with self.subTest(value=value), self.assertRaises(SourceError):
                validate_query(value)
        self.assertEqual(validate_query("  Type  2 diabetes  "), "Type 2 diabetes")

    def test_source_links_are_restricted_to_official_https(self):
        self.assertTrue(safe_medline_url("https://medlineplus.gov/asthma.html"))
        for value in ("javascript:alert(1)", "https://medlineplus.gov.evil.test/", "http://medlineplus.gov/", "https://user@medlineplus.gov/", "https://medlineplus.gov:bad/"):
            self.assertFalse(safe_medline_url(value))

    def test_xml_parsing_and_entity_rejection(self):
        xml = '<nlmSearchResult><list><document url="https://medlineplus.gov/asthma.html"><content name="title">Asthma</content><content name="full-summary">&lt;p&gt;A source summary.&lt;/p&gt;</content><content name="mesh">Asthma</content></document><document url="https://evil.test"><content name="full-summary">Wrong</content></document></list></nlmSearchResult>'
        records = parse_medline_xml(xml)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["summary"], "A source summary.")
        self.assertTrue(matches_document(records[0], "Asthma"))
        self.assertFalse(matches_document({"title": "Asthma in Children"}, "Asthma"))
        for invalid in ('<!DOCTYPE x [<!ENTITY bad SYSTEM "file:///etc/passwd">]><x/>', '<unclosed>'):
            with self.assertRaises(SourceError):
                parse_medline_xml(invalid)

    def test_summary_preserves_decimal_values_and_removes_headings(self):
        text = "What is the test?\nThe measured reference value is 5.7 percent. Another complete sentence is present."
        summary = extract_summary(text)
        self.assertNotIn("What is", summary)
        self.assertIn("5.7 percent", summary)
        self.assertEqual(len(split_sentences(summary)), 2)

    def test_all_prepared_examples_work_without_any_network(self):
        for topic in self.service.topics:
            with self.subTest(topic=topic["key"]):
                result = self.service.search(topic["aliases"][0], online=False)
                self.assertEqual(result["type"], "result")
                self.assertEqual(result["language"], "ru")
                self.assertEqual(result["medline"]["provenance"], "snapshot")
                self.assertEqual(result["mesh"]["provenance"], "snapshot")
                self.assertEqual(len(result["trace"]), 5)
                self.assertTrue(all(c["ok"] for c in result["verification"]["checks"]))
        self.assertEqual(self.client.calls, [])

    def test_source_outage_falls_back_with_explicit_provenance(self):
        result = self.service.search("астма", online=True)
        self.assertEqual(result["mesh"]["provenance"], "snapshot")
        self.assertEqual(result["medline"]["provenance"], "snapshot")
        self.assertGreater(len(self.client.calls), 0)

    def test_english_topic_outside_examples_uses_full_official_dataset(self):
        result = self.service.search("A1C", online=False)
        self.assertEqual(result["type"], "result")
        self.assertEqual(result["medline"]["provenance"], "dataset")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["mesh"]["provenance"], "unavailable")
        self.assertFalse(result["verification"]["checks"][2]["ok"])
        self.assertEqual(len(self.service.dataset), 1017)

    def test_scoliosis_russian_and_english_resolve_to_the_same_source_without_translation(self):
        english = self.service.search("scoliosis", online=False)
        for query in ("сколиоз", "  СКОЛИОЗ  ", "сколиоз позвоночника"):
            with self.subTest(query=query):
                russian = self.service.search(query, online=False)
                self.assertEqual(russian["english"], "Scoliosis")
                self.assertEqual(russian["medline"]["url"], english["medline"]["url"])
                self.assertEqual(russian["medline"]["provenance"], "dataset")
                self.assertIn("Словарь русских терминов", russian["translation"])
        self.assertEqual(self.client.calls, [])

    def test_scoliosis_does_not_use_incorrect_transliteration_from_translation_service(self):
        class TransliterationClient(OfflineClient):
            def json(self, url, timeout=6):
                if "mymemory" in url:
                    raise AssertionError("Known Russian query must bypass unreliable translation")
                return super().json(url, timeout)

        service = GuideService(DATA, TransliterationClient())
        plan = service.planning_agent("сколиоз", online=True)
        self.assertEqual(plan["english"], "Scoliosis")

    def test_unknown_queries_are_not_replaced_with_a_prepared_answer(self):
        for query in ("unknown-disease-xyz", "анемия при беременности"):
            with self.subTest(query=query), self.assertRaises(SourceError):
                self.service.search(query, online=False)

    def test_selected_descriptor_replaces_the_original_topic(self):
        result = self.service.search("астма", online=False, selected_id="D000740")
        self.assertEqual(result["mesh"]["id"], "D000740")
        self.assertEqual(result["title"], "Анемия")
        self.assertIn("anemia", result["medline"]["url"])

    def test_selected_unavailable_descriptor_must_not_reuse_original_topic(self):
        with self.assertRaises(SourceError):
            self.service.search("астма", online=False, selected_id="D999999")
        with self.assertRaises(SourceError):
            self.service.search("астма", selected_id="https://evil.test/")

    def test_ambiguous_mesh_query_returns_choices_and_stops_pipeline(self):
        class AmbiguousClient:
            def json(self, url, timeout=6):
                return [{"resource": "http://id.nlm.nih.gov/mesh/D001249", "label": "Asthma"},
                        {"resource": "http://id.nlm.nih.gov/mesh/D000740", "label": "Anemia"}]

        service = GuideService(DATA, AmbiguousClient())
        result = service.search("ambiguous", online=True)
        self.assertEqual(result["type"], "choices")
        self.assertEqual(len(result["choices"]), 2)
        self.assertEqual(len(result["trace"]), 2)

    def test_live_medline_material_does_not_use_a_stale_prepared_translation(self):
        class LiveClient(OfflineClient):
            def text(self, url, timeout=6):
                return '<nlmSearchResult><list><document url="https://medlineplus.gov/asthma.html"><content name="title">Asthma</content><content name="full-summary">This is a newly retrieved source sentence with different wording.</content><content name="mesh">Asthma</content></document></list></nlmSearchResult>'

        service = GuideService(DATA, LiveClient())
        result = service.search("астма", online=True)
        self.assertEqual(result["medline"]["provenance"], "live")
        self.assertEqual(result["language"], "en")
        self.assertIn("newly retrieved", result["text"])
        self.assertEqual(result["facts"], [])

    def test_trace_events_and_export_include_source_evidence(self):
        events = []
        result = self.service.search("анемия", online=False, on_event=events.append)
        self.assertEqual(len(events), 10)
        self.assertEqual(sum(e["status"] == "done" for e in events), 5)
        document = export_markdown(result)
        self.assertIn(result["medline"]["url"], document)
        self.assertIn(result["mesh"]["url"], document)
        self.assertIn(result["original"], document)


if __name__ == "__main__":
    unittest.main()
