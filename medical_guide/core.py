"""Parsing, matching and evidence checks shared by the agents."""
from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urlparse


class SourceError(Exception):
    """An expected input or upstream-source failure safe to show to a user."""


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "").strip().lower().replace("ё", "е"))


def validate_query(value: str) -> str:
    if not isinstance(value, str):
        raise SourceError("Введите название заболевания или состояния.")
    query = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip())
    if not 2 <= len(query) <= 120 or re.search(r"[\x00-\x1f<>]", query):
        raise SourceError("Введите от 2 до 120 символов без разметки.")
    return query


def plain_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.I | re.S)
    value = re.sub(r"</(?:p|li|h\d|div)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\n\s*\n", "\n", re.sub(r"[ \t]+", " ", value)).strip()


def safe_medline_url(value: str) -> bool:
    try:
        url = urlparse(value)
        return url.scheme == "https" and url.hostname == "medlineplus.gov" and not url.username and not url.password and url.port in (None, 443)
    except (ValueError, TypeError):
        return False


def parse_medline_xml(xml: str) -> list[dict]:
    if len(xml.encode("utf-8")) > 2_000_000 or re.search(r"<!ENTITY|<!DOCTYPE", xml, re.I):
        raise SourceError("Неподдерживаемый ответ MedlinePlus.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SourceError("MedlinePlus вернул некорректные данные.") from exc
    documents = []
    for node in root.findall(".//document"):
        url = node.get("url", "")
        if not safe_medline_url(url):
            continue
        fields: dict[str, list[str]] = {}
        for field in node.findall("content"):
            fields.setdefault(field.get("name", ""), []).append(plain_text("".join(field.itertext())))
        summary = fields.get("full-summary", [""])[0]
        if summary:
            documents.append({"title": fields.get("title", ["MedlinePlus"])[0], "url": url,
                              "summary": summary, "mesh": fields.get("mesh", []),
                              "synonyms": fields.get("alt-title", [])})
    return documents


def split_sentences(text: str) -> list[str]:
    """Split English prose without breaking decimal numbers or percentage values."""
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+(?=[A-Z"“])|\n+', text) if part.strip()]


def extract_summary(text: str, max_sentences: int = 4) -> str:
    text = re.sub(r"\bNIH:[^\n]*", "", text).strip()
    candidates = split_sentences(text)
    sentences = [s.strip() for s in candidates if len(s.strip()) > 20 and not re.match(r"\s*(what|how|who|why)\b", s, re.I)]
    selected = []
    for sentence in sentences:
        if sum(map(len, selected)) + len(sentence) > 1500:
            break
        selected.append(sentence)
        if len(selected) == max_sentences:
            break
    return " ".join(selected)


def matches_document(document: dict, label: str) -> bool:
    target = normalize(label)
    return bool(target) and any(normalize(candidate) == target for candidate in
                               [document.get("title", ""), *document.get("mesh", []), *document.get("synonyms", [])])


def verify_result(result: dict) -> dict:
    mesh = result.get("mesh", {})
    checks = [
        {"label": "У справки есть ссылка на MedlinePlus", "ok": safe_medline_url(result.get("medline", {}).get("url", ""))},
        {"label": "Сохранён исходный текст для сравнения", "ok": bool(result.get("original", "").strip())},
        {"label": "Понятие подтверждено записью MeSH", "ok": bool(re.fullmatch(r"D\d{6,9}", mesh.get("id") or "")) and mesh.get("provenance") in ("live", "snapshot")},
        {"label": "Русское объяснение доступно", "ok": result.get("language") == "ru"},
    ]
    return {"checks": checks, "note": "Проверены ссылки, наличие оригинала и запись MeSH. Это не клиническая экспертиза и не гарантия точности перевода."}


def export_markdown(result: dict) -> str:
    mesh, medline = result["mesh"], result["medline"]
    mesh_link = f"MeSH: {mesh['url']}\n" if mesh.get("url") else "MeSH: понятие не подтверждено\n"
    return (f"# {result['title']}\n\n{result['english']}\n\n{result['text']}\n\n"
            f"## Источники\n\n{mesh_link}MedlinePlus: {medline['url']}\n"
            f"\nПолучение материала: {medline['provenance']}\nДата: {medline['checkedAt']}\n"
            f"Способ подготовки: {result['mode']}\n\n## Исходный фрагмент\n\n{result['original']}\n\n"
            "Справочная информация не является диагнозом или персональной рекомендацией.\n")
