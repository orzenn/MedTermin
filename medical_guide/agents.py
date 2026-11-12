"""Five small agents coordinated in a sequential, source-backed pipeline."""
from __future__ import annotations

import copy
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import (SourceError, extract_summary, matches_document, normalize, parse_medline_xml,
                   plain_text, split_sentences, validate_query, verify_result)


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
        self.topics = json.loads((data_dir / "topics.json").read_text(encoding="utf-8"))
        self.mesh_snapshots = json.loads((data_dir / "mesh.json").read_text(encoding="utf-8"))
        self.dataset = json.loads((data_dir / "medline.json").read_text(encoding="utf-8"))
        self.aliases = {normalize(alias): topic for topic in self.topics for alias in topic["aliases"]}

    def translate(self, text: str, languages: str) -> str:
        if len(text.encode("utf-8")) > 490:
            raise SourceError("Фрагмент слишком большой для перевода.")
        data = self.client.json("https://api.mymemory.translated.net/get?" + urlencode({"q": text, "langpair": languages}))
        value = data.get("responseData", {}).get("translatedText", "")
        if str(data.get("responseStatus")) != "200" or not value or re.search(r"MYMEMORY WARNING|QUERY LENGTH LIMIT|INVALID LANGUAGE", value, re.I):
            raise SourceError("Перевод временно недоступен.")
        return plain_text(value)

    def planning_agent(self, query: str, online: bool) -> dict:
        topic = self.aliases.get(normalize(query))
        if topic:
            return {"english": topic["english"], "title": topic["title"], "topic": topic,
                    "translation": "Проверенный словарь соответствий"}
        if not re.search(r"[а-яё]", query, re.I):
            return {"english": query, "title": query, "topic": None, "translation": "Запрос на английском"}
        if not online:
            raise SourceError("Для нового русского термина включите онлайн-поиск или введите английское название.")
        try:
            english = self.translate(query, "ru|en")
        except Exception as exc:
            raise SourceError("Не удалось перевести запрос. Попробуйте английское название или один из примеров.") from exc
        if re.search(r"[а-яё]", english, re.I):
            raise SourceError("Не удалось получить английское название. Уточните термин или введите его на английском.")
        return {"english": english, "title": query, "topic": None, "translation": "Машинный перевод MyMemory"}

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
                chunks = split_sentences(original)[:4]
                if any(len(s.encode("utf-8")) > 490 for s in chunks):
                    raise SourceError("Длинное предложение для перевода.")
                with ThreadPoolExecutor(max_workers=4) as executor:
                    translated = list(executor.map(lambda s: self.translate(s, "en|ru"), chunks))
                if not all(re.search(r"[а-яё]", s, re.I) for s in translated):
                    raise SourceError("Неверный язык перевода.")
                return {"text": " ".join(translated), "original": original, "language": "ru", "facts": [],
                        "mode": "Извлечение предложений + машинный перевод MyMemory"}
            except Exception:
                pass
        return {"text": original, "original": original, "language": "en", "facts": [],
                "mode": "Исходный английский фрагмент: перевод отключён или недоступен"}

    def search(self, query: str, *, online: bool = True, selected_id: str | None = None,
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

        plan = step("plan", "Понимание запроса", lambda: self.planning_agent(query, online))
        mesh = step("mesh", "Поиск понятия в MeSH", lambda: self.mesh_agent(plan, online, selected_id))
        if "candidates" in mesh:
            return {"type": "choices", "choices": mesh["candidates"], "query": query, "trace": trace}
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
