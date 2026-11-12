"""Five small agents coordinated in a sequential, source-backed pipeline."""
from __future__ import annotations

import copy
import json
import re
import time
from difflib import get_close_matches
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import (SourceError, extract_summary, matches_document, normalize, parse_medline_xml,
                   validate_query, verify_result)
from .translation import Translator


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class HttpClient:
    """Read-only HTTP transport with explicit size limits and timeouts."""

    def text(self, url: str, timeout: float = 6) -> str:
        request = Request(url, headers={"User-Agent": "MedTerm-Educational/1.0", "Accept": "application/json, application/xml, text/xml"})
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise SourceError("Ответ источника слишком большой.")
            return raw.decode("utf-8-sig")

    def json(self, url: str, timeout: float = 6):
        return json.loads(self.text(url, timeout))


class GuideService:
    def __init__(self, data_dir: Path, client: HttpClient | None = None):
        self.client = client or HttpClient()
        self.translator = Translator(self.client)
        self.topics = json.loads((data_dir / "topics.json").read_text(encoding="utf-8"))
        self.mesh_snapshots = json.loads((data_dir / "mesh.json").read_text(encoding="utf-8"))
        self.dataset = json.loads((data_dir / "medline.json").read_text(encoding="utf-8"))
        self.aliases = {normalize(alias): topic for topic in self.topics for alias in topic["aliases"]}
        self.ru_terms = {normalize(term): english for term, english in
                         json.loads((data_dir / "ru_terms.json").read_text(encoding="utf-8")).items()}
        self.vocabulary = {}
        for doc in self.dataset:
            for label in [doc["title"], *doc.get("synonyms", []), *doc.get("mesh", [])]:
                self.vocabulary.setdefault(normalize(label), label)
        if any(normalize(english) not in self.vocabulary for english in self.ru_terms.values()):
            raise ValueError("A Russian term mapping does not match the source vocabulary.")

    def translate(self, text: str, languages: str) -> str:
        return self.translator.translate_fragment(text, languages)

    def planning_agent(self, query: str, online: bool, selected_english: str | None = None) -> dict:
        if selected_english:
            english = self.vocabulary.get(normalize(selected_english))
            if not english:
                raise SourceError("Выбранный перевод не найден в словаре источника.")
            topic = self.aliases.get(normalize(english))
            return {"english": english, "title": topic["title"] if topic else query, "topic": topic,
                    "translation": "Английское понятие подтверждено пользователем"}
        topic = self.aliases.get(normalize(query))
        if topic:
            return {"english": topic["english"], "title": topic["title"], "topic": topic,
                    "translation": "Проверенный словарь соответствий"}
        english = self.ru_terms.get(normalize(query))
        if english:
            return {"english": english, "title": query, "topic": None,
                    "translation": "Словарь русских терминов: название сопоставлено со словарём источника"}
        if not re.search(r"[а-яё]", query, re.I):
            return {"english": query, "title": query, "topic": None, "translation": "Запрос на английском"}
        if not online:
            raise SourceError("Для нового русского термина включите онлайн-поиск или введите английское название.")
        try:
            candidates = self.translator.query_candidates(query)
        except Exception as exc:
            raise SourceError("Не удалось перевести запрос. Попробуйте английское название или один из примеров.") from exc
        matched = list(dict.fromkeys(self.vocabulary[normalize(term)] for term in candidates if normalize(term) in self.vocabulary))
        if len(matched) > 1:
            return {"translation_candidates": [{"english": term} for term in matched[:5]], "translatedQuery": candidates[0],
                    "reason": "Найдено несколько вариантов перевода в словаре источника."}
        if matched:
            english = matched[0]
            return {"english": english, "title": query, "topic": self.aliases.get(normalize(english)),
                    "translation": "MyMemory: английское название найдено в словаре источника"}
        suggestions = []
        for candidate in candidates[:3]:
            if len(candidate) < 5:
                continue
            for key in get_close_matches(normalize(candidate), self.vocabulary, n=3, cutoff=0.86):
                term = self.vocabulary[key]
                if term not in suggestions:
                    suggestions.append(term)
        if suggestions:
            return {"translation_candidates": [{"english": term} for term in suggestions[:5]], "translatedQuery": candidates[0],
                    "reason": "Точный перевод не подтверждён. Найдены похожие названия; приложение не выбирает их автоматически."}
        return {"english": candidates[0], "title": query, "topic": None,
                "translation": "MyMemory: название не подтверждено локальным словарём; выполняется поиск в MeSH"}

    @staticmethod
    def _rdf_value(value) -> str:
        return value if isinstance(value, str) else (value or {}).get("@value", "")

    def mesh_agent(self, plan: dict, online: bool, selected_id: str | None = None) -> dict:
        snapshot = self.mesh_snapshots.get(selected_id) if selected_id else next(
            (s for s in self.mesh_snapshots.values() if normalize(s["label"]) == normalize(plan["english"])), None)
        if online:
            try:
                if selected_id:
                    candidates = [{"resource": f"http://id.nlm.nih.gov/mesh/{selected_id}"}]
                elif snapshot:
                    candidates = [{"resource": f"http://id.nlm.nih.gov/mesh/{snapshot['id']}"}]
                else:
                    params = {"label": plan["english"], "match": "exact", "limit": 6}
                    candidates = self.client.json("https://id.nlm.nih.gov/mesh/lookup/descriptor?" + urlencode(params))
                    if not candidates:
                        params["match"] = "contains"
                        candidates = self.client.json("https://id.nlm.nih.gov/mesh/lookup/descriptor?" + urlencode(params))
                candidates = [c for c in candidates if isinstance(c, dict) and re.fullmatch(r"https?://id\.nlm\.nih\.gov/mesh/D\d{6,9}", c.get("resource", ""))]
                if len(candidates) > 1:
                    return {"candidates": [{"id": c["resource"].rsplit("/", 1)[-1], "label": c.get("label", "MeSH")} for c in candidates]}
                if candidates:
                    identifier = candidates[0]["resource"].rsplit("/", 1)[-1]
                    record = self.client.json(f"https://id.nlm.nih.gov/mesh/{identifier}.json")
                    label = self._rdf_value(record.get("label"))
                    if not label:
                        raise SourceError("В записи MeSH нет названия.")
                    definition = ""
                    concept = record.get("preferredConcept", "")
                    if re.fullmatch(r"http://id\.nlm\.nih\.gov/mesh/M\d+", concept):
                        try:
                            data = self.client.json(concept.replace("http:", "https:") + ".json", timeout=4)
                            definition = self._rdf_value(data.get("scopeNote"))
                        except Exception:
                            pass
                    return {"id": identifier, "label": label, "definition": definition,
                            "synonyms": [], "provenance": "live", "checkedAt": timestamp(),
                            "url": f"https://id.nlm.nih.gov/mesh/{identifier}"}
            except Exception:
                pass
        if snapshot:
            return {**copy.deepcopy(snapshot), "provenance": "snapshot"}
        return {"id": None, "label": plan["english"], "definition": "", "synonyms": [], "provenance": "unavailable"}

    def medline_agent(self, plan: dict, mesh: dict, online: bool) -> dict:
        if online:
            try:
                term = f'mesh:"{mesh["label"]}"' if mesh.get("id") else plan["english"]
                query = urlencode({"db": "healthTopics", "rettype": "topic", "retmax": 6, "term": term})
                documents = parse_medline_xml(self.client.text("https://wsearch.nlm.nih.gov/ws/query?" + query, timeout=5))
                exact = next((d for d in documents if matches_document(d, mesh.get("label") or plan["english"])), None)
                if exact:
                    return {**exact, "provenance": "live", "checkedAt": timestamp()}
                # Related material must not be presented as an exact match.
            except Exception:
                pass
        topic = plan.get("topic")
        if topic:
            return {"title": topic["english"], "url": topic["url"], "summary": topic["summaryEn"],
                    "synonyms": [], "mesh": [], "provenance": "snapshot", "checkedAt": topic["checkedAt"]}
        wanted = {normalize(plan["english"]), normalize(mesh.get("label"))} - {""}
        exact = next((d for d in self.dataset if any(normalize(label) in wanted for label in [d["title"], *d.get("synonyms", [])])), None)
        if exact:
            return copy.deepcopy(exact)
        linked = [d for d in self.dataset if mesh.get("id") and mesh["id"] in d.get("meshIds", [])]
        if len(linked) == 1:
            return copy.deepcopy(linked[0])
        if len(linked) > 1:
            return {"candidates": [{"title": d["title"], "url": d["url"]} for d in linked[:8]]}
        raise SourceError("Точного материала MedlinePlus не найдено. Попробуйте другое или более общее название. Для новых терминов проверьте онлайн-поиск.")

    def explanation_agent(self, plan: dict, medline: dict, online: bool) -> dict:
        topic = plan.get("topic")
        if topic and medline["provenance"] == "snapshot":
            return {"text": topic["summaryRu"], "original": topic["summaryEn"], "language": "ru",
                    "mode": "Подготовленная русская справка по сохранённому материалу", "facts": topic["factsRu"]}
        original = extract_summary(medline["summary"])
        if not original:
            raise SourceError("В найденном материале недостаточно текста для краткой справки.")
        if online:
            try:
                pairs = self.translator.translate_source(original)
                return {"text": " ".join(pair["translated"] for pair in pairs), "original": original,
                        "language": "ru", "facts": [], "translationPairs": pairs,
                        "mode": "Извлечение предложений + MyMemory с проверкой чисел"}
            except SourceError as exc:
                note = str(exc)
            except Exception:
                note = "Сервис перевода временно недоступен. Показан английский оригинал."
        else:
            note = "Перевод отключён в режиме без сети."
        return {"text": original, "original": original, "language": "en", "facts": [],
                "translationNote": note, "mode": "Исходный английский фрагмент: перевод отключён или недоступен"}

    def search(self, query: str, *, online: bool = True, selected_id: str | None = None,
               selected_english: str | None = None,
               on_event: Callable[[dict], None] | None = None) -> dict:
        query = validate_query(query)
        if selected_id and not re.fullmatch(r"D\d{6,9}", selected_id):
            raise SourceError("Некорректный идентификатор MeSH.")
        trace = []
        emit = on_event or (lambda event: None)

        def step(key, name, task):
            started = time.perf_counter()
            emit({"key": key, "name": name, "status": "running"})
            try:
                value = task()
            except Exception:
                emit({"key": key, "name": name, "status": "error"})
                raise
            event = {"key": key, "name": name, "status": "done", "durationMs": round((time.perf_counter() - started) * 1000)}
            trace.append(event)
            emit(event)
            return value

        plan = step("plan", "Понимание запроса", lambda: self.planning_agent(query, online, selected_english))
        if "translation_candidates" in plan:
            return {"type": "translation_choices", "choices": plan["translation_candidates"], "query": query,
                    "translatedQuery": plan["translatedQuery"], "reason": plan["reason"], "trace": trace}
        mesh = step("mesh", "Поиск понятия в MeSH", lambda: self.mesh_agent(plan, online, selected_id))
        if "candidates" in mesh:
            return {"type": "choices", "choices": mesh["candidates"], "query": query, "trace": trace, "selectedEnglish": selected_english}
        if selected_id and mesh.get("id"):
            topic = next((t for t in self.topics if normalize(t["english"]) == normalize(mesh["label"])), None)
            plan = {**plan, "english": mesh["label"], "title": topic["title"] if topic else mesh["label"], "topic": topic}
        elif selected_id:
            raise SourceError("Не удалось получить выбранное понятие MeSH. Повторите поиск, когда источник будет доступен.")
        medline = step("medline", "Получение материала MedlinePlus", lambda: self.medline_agent(plan, mesh, online))
        if "candidates" in medline:
            return {"type": "related", "choices": medline["candidates"], "query": query, "trace": trace}
        explanation = step("explain", "Подготовка объяснения", lambda: self.explanation_agent(plan, medline, online))
        result = {"type": "result", "query": query, "title": plan["title"], "english": mesh["label"],
                  "category": (plan.get("topic") or {}).get("category", "Медицинское понятие"),
                  "mesh": mesh, "medline": medline, **explanation, "translation": plan["translation"],
                  "createdAt": timestamp(), "onlineRequested": online}
        result["verification"] = step("verify", "Проверка источников", lambda: verify_result(result))
        return {**result, "trace": trace}
