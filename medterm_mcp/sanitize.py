from math import isfinite

DEMOS = {
    "bmi": {"height_cm": 175, "weight_kg": 70, "adult_categories": True},
    "body_surface_area": {"height_cm": 175, "weight_kg": 70},
    "glasgow_coma_scale": {"eye": "3 — На звук", "verbal": "4 — Спутанная речь", "motor": "6 — Выполняет команды"},
    "mean_arterial_pressure": {"systolic": 120, "diastolic": 78},
    "shock_index": {"heart_rate": 78, "systolic": 120},
    "pubmed_search_articles": {"query": "Asthma[MeSH Terms] AND systematic review[Publication Type]"},
}


def exposed_schema(tool: dict) -> dict:
    schema = tool["inputSchema"]
    if tool["name"] != "pubmed_search_articles":
        return schema
    fields = schema["properties"]
    return {"required": ["query"], "properties": {
        "query": {**fields["query"], "title": "Запрос PubMed (по-английски)", "maxLength": 200, "type": "string"},
        "maxResults": {**fields["maxResults"], "title": "Количество публикаций", "type": "integer", "minimum": 1, "maximum": 10, "default": 5},
    }}


def sanitize(tool: str, schema: dict, values: dict) -> dict:
    arguments = {}
    for name, specification in schema.get("properties", {}).items():
        value = values.get(name)
        label = specification.get("title", name)
        if isinstance(value, str):
            value = value.strip()
        if value is None or value == "":
            if name in schema.get("required", []):
                raise ValueError(f"Заполните поле «{label}».")
            if "default" not in specification:
                continue
            value = specification["default"]
        if specification.get("type") in {"number", "integer"}:
            try:
                number = float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                raise ValueError(f"В поле «{label}» нужно ввести число.") from None
            if not isfinite(number):
                raise ValueError(f"В поле «{label}» нужно ввести конечное число.")
            if specification["type"] == "integer":
                if not number.is_integer():
                    raise ValueError(f"В поле «{label}» нужно ввести целое число.")
                number = int(number)
            value = number
        if isinstance(value, str) and len(value) > specification.get("maxLength", 1000):
            raise ValueError(f"Поле «{label}» слишком длинное.")
        arguments[name] = value

    if tool == "pubmed_search_articles":
        arguments["maxResults"] = max(1, min(10, arguments["maxResults"]))
        arguments["summaryCount"] = arguments["maxResults"]
    return arguments
