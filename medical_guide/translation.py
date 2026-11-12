"""Translation candidates and conservative checks; no medical meaning is inferred."""
from __future__ import annotations

import re
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.parse import urlencode

from .core import SourceError, normalize, plain_text, split_sentences


def numeric_values(text: str) -> Counter:
    text = text.replace("−", "-")
    return Counter(value.replace(",", ".") for value in re.findall(r"(?<![\w\d])[+-]?\d+(?:[.,]\d+)?", text))


def translation_chunks(text: str, limit: int = 450) -> list[str]:
    """Keep all words; split long sentences at whitespace within the API byte limit."""
    chunks = []
    for sentence in split_sentences(text):
        current = ""
        for word in sentence.split():
            if len(word.encode("utf-8")) > limit:
                raise SourceError("Слишком длинный фрагмент для перевода.")
            candidate = current + (" " if current else "") + word
            if len(candidate.encode("utf-8")) > limit:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def clean_candidate(value: str) -> str:
    return plain_text(value).strip(" \t\n\"'«»“”‘’.;")


class Translator:
    """Cache only checked translations of public source fragments, not user queries."""

    def __init__(self, client, max_cache: int = 256, ttl_seconds: float = 86400):
        self.client = client
        self.max_cache = max_cache
        self.ttl = ttl_seconds
        self._cache: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()
        self._lock = Lock()

    def _response(self, text: str, languages: str) -> dict:
        if len(text.encode("utf-8")) > 490:
            raise SourceError("Фрагмент слишком большой для перевода.")
        data = self.client.json("https://api.mymemory.translated.net/get?" + urlencode({"q": text, "langpair": languages}))
        value = data.get("responseData", {}).get("translatedText", "")
        if (str(data.get("responseStatus")) != "200" or not isinstance(value, str) or not value.strip()
                or re.search(r"MYMEMORY WARNING|QUERY LENGTH LIMIT|INVALID LANGUAGE", value, re.I)):
            raise SourceError("Перевод временно недоступен или исчерпан лимит сервиса.")
        return data

    def query_candidates(self, query: str) -> list[str]:
        data = self._response(query, "ru|en")
        values = [data["responseData"]["translatedText"]]
        # Only alternatives for this exact input segment, with a high provider match score.
        for item in data.get("matches", []):
            if not isinstance(item, dict) or normalize(item.get("segment", "")) != normalize(query):
                continue
            try:
                score = float(item.get("match", 0))
            except (ValueError, TypeError):
                continue
            if score >= 0.95 and isinstance(item.get("translation"), str):
                values.append(item["translation"])
        result, seen = [], set()
        for value in values[:20]:
            candidate = clean_candidate(value)
            key = normalize(candidate)
            if (key and key not in seen and len(candidate) <= 160 and re.search(r"[a-z]", candidate, re.I)
                    and not re.search(r"[а-яё]", candidate, re.I)
                    and numeric_values(query) == numeric_values(candidate)):
                result.append(candidate)
                seen.add(key)
        if not result:
            raise SourceError("Не удалось получить подходящий английский перевод. Уточните термин или введите его на английском.")
        return result

    def translate_fragment(self, text: str, languages: str) -> str:
        cacheable = languages == "en|ru"
        key = (languages, text)
        if cacheable:
            with self._lock:
                cached = self._cache.get(key)
                if cached and time.monotonic() - cached[0] < self.ttl:
                    self._cache.move_to_end(key)
                    return cached[1]
                self._cache.pop(key, None)
        translated = plain_text(self._response(text, languages)["responseData"]["translatedText"])
        if not re.search(r"[а-яё]" if languages == "en|ru" else r"[a-z]", translated, re.I):
            raise SourceError("Сервис вернул текст не на ожидаемом языке.")
        if numeric_values(text) != numeric_values(translated):
            raise SourceError("При переводе изменились числа. Показан английский оригинал.")
        if cacheable:
            with self._lock:
                self._cache[key] = (time.monotonic(), translated)
                self._cache.move_to_end(key)
                while len(self._cache) > self.max_cache:
                    self._cache.popitem(last=False)
        return translated

    def translate_source(self, original: str) -> list[dict]:
        chunks = translation_chunks(original)
        if not chunks or len(chunks) > 8:
            raise SourceError("Слишком большой фрагмент для краткого перевода. Показан английский оригинал.")
        with ThreadPoolExecutor(max_workers=4) as executor:
            translated = list(executor.map(lambda part: self.translate_fragment(part, "en|ru"), chunks))
        return [{"original": source, "translated": target} for source, target in zip(chunks, translated)]
