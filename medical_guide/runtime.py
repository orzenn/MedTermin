"""Version the cached service and recover from stale objects after Streamlit hot reload."""
from __future__ import annotations

import hashlib
import importlib
import inspect
from pathlib import Path

import streamlit as st

CACHE_VERSION = "translation-service-v3"


def supports_current_api(service) -> bool:
    try:
        parameters = inspect.signature(service.search).parameters
        return "selected_english" in parameters and hasattr(service, "translator")
    except (AttributeError, TypeError, ValueError):
        return False


def source_version(data_dir: Path) -> str:
    digest = hashlib.sha256(CACHE_VERSION.encode())
    for folder, names in (
        (data_dir, ("topics.json", "mesh.json", "medline.json", "ru_terms.json")),
        (Path(__file__).parent, ("agents.py", "translation.py", "core.py")),
    ):
        for name in names:
            digest.update(name.encode())
            digest.update((folder / name).read_bytes())
    return digest.hexdigest()


@st.cache_resource
def load_cached_service(data_dir: str, version: str):
    module = importlib.import_module("medical_guide.agents")
    service = module.GuideService(Path(data_dir))
    if not supports_current_api(service):
        # A hot-reloaded page may still reference an older imported Python module.
        importlib.reload(importlib.import_module("medical_guide.translation"))
        module = importlib.reload(module)
        service = module.GuideService(Path(data_dir))
    return service


def get_service(data_dir: Path):
    version = source_version(data_dir)
    service = load_cached_service(str(data_dir), version)
    if not supports_current_api(service):
        load_cached_service.clear()
        service = load_cached_service(str(data_dir), version)
    if not supports_current_api(service):
        raise RuntimeError("Не удалось обновить обработчик приложения. Перезапустите Streamlit.")
    return service
