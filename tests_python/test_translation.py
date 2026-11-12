import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from medical_guide.agents import GuideService
from medical_guide.core import SourceError
from medical_guide.translation import Translator, numeric_values, translation_chunks

DATA = Path(__file__).resolve().parents[1] / "data"


class FakeClient:
    def __init__(self, primary="Scoliosis", alternatives=None):
        self.primary = primary
        self.alternatives = alternatives or []
        self.calls = []

    def json(self, url, timeout=6):
        self.calls.append(url)
        if "mymemory" not in url:
            raise OSError("Offline fixture")
        return {"responseStatus": 200, "responseData": {"translatedText": self.primary}, "matches": self.alternatives}


class TranslationTests(unittest.TestCase):
    def test_verified_alternative_beats_unrecognised_primary_transliteration(self):
        client = FakeClient("Skoliosis", [{"segment": "тестовый термин", "translation": "Scoliosis", "match": 0.99}])
        service = GuideService(DATA, client)
        plan = service.planning_agent("тестовый термин", online=True)
        self.assertEqual(plan["english"], "Scoliosis")
        self.assertIn("найдено в словаре", plan["translation"])

    def test_unrelated_or_low_quality_alternatives_are_not_used(self):
        client = FakeClient("not-an-existing-medical-name", [
            {"segment": "другой запрос", "translation": "Asthma", "match": 1},
            {"segment": "тестовый термин", "translation": "Anemia", "match": 0.5},
            {"segment": "тестовый термин", "translation": "Scoliosis", "match": "bad"},
        ])
        self.assertEqual(Translator(client).query_candidates("тестовый термин"), ["not-an-existing-medical-name"])

    def test_similar_spelling_requires_user_confirmation(self):
        service = GuideService(DATA, FakeClient("Skoliosis"))
        result = service.search("тестовый термин", online=True)
        self.assertEqual(result["type"], "translation_choices")
        self.assertIn("Scoliosis", [c["english"] for c in result["choices"]])
        self.assertEqual(len(result["trace"]), 1)
        self.assertNotIn("text", result)
        selected = service.search("тестовый термин", online=False, selected_english="Scoliosis")
        self.assertEqual(selected["medline"]["url"], "https://medlineplus.gov/scoliosis.html")

    def test_conflicting_exact_alternatives_are_not_selected_silently(self):
        client = FakeClient("Asthma", [{"segment": "тестовый термин", "translation": "Anemia", "match": 0.99}])
        result = GuideService(DATA, client).search("тестовый термин", online=True)
        self.assertEqual(result["type"], "translation_choices")
        self.assertEqual({c["english"] for c in result["choices"]}, {"Asthma", "Anemia"})

    def test_arbitrary_user_selected_translation_is_rejected(self):
        service = GuideService(DATA, FakeClient())
        with self.assertRaises(SourceError):
            service.search("тестовый термин", online=False, selected_english="invented-disease-xyz")

    def test_query_alternatives_preserve_numbers(self):
        client = FakeClient("Type 1 diabetes", [{"segment": "диабет 2", "translation": "Type 2 diabetes", "match": 1}])
        self.assertEqual(Translator(client).query_candidates("диабет 2"), ["Type 2 diabetes"])

    def test_candidate_case_and_outer_punctuation_are_normalised(self):
        client = FakeClient('"Cataract."', [{"segment": "катаракта", "translation": "cataract", "match": 1}])
        self.assertEqual(Translator(client).query_candidates("катаракта"), ["Cataract"])

    def test_long_sentence_is_split_without_dropping_words_or_exceeding_byte_limit(self):
        sentence = " ".join(["longerword"] * 90) + "."
        chunks = translation_chunks(sentence)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(part.encode("utf-8")) <= 450 for part in chunks))
        self.assertEqual(" ".join(chunks), sentence)

    def test_number_check_preserves_multiplicity_and_decimal_comma(self):
        self.assertEqual(numeric_values("5.7 in 2 days"), numeric_values("5,7 через 2 дня"))
        self.assertNotEqual(numeric_values("2 and 2"), numeric_values("2"))
        self.assertNotEqual(numeric_values("-5 degrees"), numeric_values("5 градусов"))
        self.assertEqual(numeric_values("−5 degrees"), numeric_values("-5 градусов"))
        with self.assertRaises(SourceError):
            Translator(FakeClient("Результат 5,7 через 3 дня.")).translate_fragment("The result is 5.7 after 2 days.", "en|ru")

    def test_failed_number_check_returns_original_instead_of_corrupted_translation(self):
        service = GuideService(DATA, FakeClient("Результат 5,7 процента через 3 дня."))
        result = service.explanation_agent({"topic": None}, {"summary": "The result is 5.7 percent after 2 days.", "provenance": "live"}, online=True)
        self.assertEqual(result["language"], "en")
        self.assertIn("изменились числа", result["translationNote"])
        self.assertIn("2 days", result["text"])

    def test_checked_source_translation_is_cached_but_queries_are_not(self):
        client = FakeClient("Это понятное русское объяснение.")
        translator = Translator(client)
        for _ in range(2):
            translator.translate_fragment("This is an English explanation.", "en|ru")
        self.assertEqual(len(client.calls), 1)
        client.primary = "Cataract"
        for _ in range(2):
            translator.query_candidates("катаракта")
        self.assertEqual(len(client.calls), 3)

    def test_source_cache_is_bounded_and_expires(self):
        client = FakeClient("Русский перевод.")
        translator = Translator(client, max_cache=1)
        translator.translate_fragment("First source.", "en|ru")
        translator.translate_fragment("Second source.", "en|ru")
        translator.translate_fragment("First source.", "en|ru")
        self.assertEqual(len(client.calls), 3)
        translator.ttl = 0
        translator.translate_fragment("First source.", "en|ru")
        self.assertEqual(len(client.calls), 4)

    def test_source_pairs_keep_original_and_translation_together(self):
        pairs = Translator(FakeClient("Перевод исходного предложения.")).translate_source("A complete English source sentence.")
        self.assertEqual(pairs, [{"original": "A complete English source sentence.", "translated": "Перевод исходного предложения."}])

    def test_quota_error_is_not_displayed_as_a_translation(self):
        with self.assertRaises(SourceError):
            Translator(FakeClient("MYMEMORY WARNING: quota exceeded")).translate_fragment("Source sentence.", "en|ru")


if __name__ == "__main__":
    unittest.main()
