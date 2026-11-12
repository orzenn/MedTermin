"""Streamlit entry point: python -m streamlit run app.py."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import streamlit as st

from medical_guide.agents import GuideService
from medical_guide.core import SourceError, export_markdown, safe_medline_url

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="МедТермин · Streamlit", page_icon="🌿", layout="wide", initial_sidebar_state="auto")
st.html("""
<style>
 .stApp {font-family:'Segoe UI',sans-serif}
 .block-container {max-width:1150px;padding-top:2rem;padding-bottom:2rem}
 h1,h2,h3 {letter-spacing:-.035em}
 .med-brand {font-weight:650;font-size:1.45rem;color:#20594b;margin-bottom:1.3rem}
 .med-eyebrow {font-size:.68rem;letter-spacing:.16em;color:#6b8168;margin-bottom:1rem}
 .med-hero h1 {font-family:Georgia,serif;font-size:clamp(2.7rem,6vw,4.8rem);font-weight:400;line-height:1.06;margin:0 0 1rem;color:#183c36}
 .med-hero em {color:#788d60;font-weight:400}
 .med-hero p {color:#68766e;max-width:630px;font-size:1rem;line-height:1.8;margin-bottom:1.7rem}
 [data-testid="stSidebar"] {border-right:1px solid #dfe5dd}
 [data-testid="stForm"] {background:#fff;border:1px solid #dfe5dd;border-radius:12px;padding:1.4rem}
 [data-testid="stTabs"] {margin-top:1.2rem}
 .med-footer {font-size:.72rem;color:#78846f;padding:1.5rem 0;border-top:1px solid #dfe5dd;margin-top:2rem;line-height:1.8}
 @media(max-width:600px){.block-container{padding:1.2rem}.med-hero h1{font-size:3rem}}
</style>
""")


@st.cache_resource
def load_service(data_version: tuple[int, ...]) -> GuideService:
    return GuideService(ROOT / "data")


service = load_service(tuple((ROOT / path).stat().st_mtime_ns for path in (
    "data/topics.json", "data/mesh.json", "data/medline.json", "data/ru_terms.json", "medical_guide/agents.py", "medical_guide/translation.py",
)))
for key, value in {"saved": {}, "history": [], "result": None, "query": "", "navigation": "Справочник", "error": None}.items():
    if key not in st.session_state:
        st.session_state[key] = value


def readable_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except (ValueError, AttributeError):
        return "Дата не указана"


def result_key(result: dict) -> str:
    return result.get("mesh", {}).get("id") or result["medline"]["url"]


def queue_example(query: str) -> None:
    st.session_state["query"] = query
    st.session_state["pending_query"] = query


def open_saved(key: str) -> None:
    st.session_state["result"] = st.session_state["saved"][key]
    st.session_state["navigation"] = "Справочник"
    st.session_state["error"] = None


def toggle_save() -> None:
    result = st.session_state["result"]
    key = result_key(result)
    if key in st.session_state["saved"]:
        del st.session_state["saved"][key]
        st.session_state["notice"] = "Справка удалена из сохранённого"
    else:
        st.session_state["saved"] = {key: result, **st.session_state["saved"]}
        st.session_state["notice"] = "Справка сохранена в этом сеансе"


def source_caption(source: dict) -> None:
    provenance = source.get("provenance")
    labels = {"live": "Получено из источника сейчас", "snapshot": "Сохранённая справка", "dataset": "Официальная выгрузка MedlinePlus", "unavailable": "Не подтверждено"}
    suffix = f" · {readable_date(source['checkedAt'])}" if source.get("checkedAt") else ""
    st.caption(labels.get(provenance, "Не подтверждено") + suffix)


def run_search(query: str, selected_id: str | None = None, selected_english: str | None = None) -> None:
    st.session_state["result"] = None
    st.session_state["error"] = None
    with st.status("Собираем объяснение…", expanded=True) as status:
        lines = {}

        def on_event(event: dict) -> None:
            if event["key"] not in lines:
                lines[event["key"]] = st.empty()
            symbol = {"running": "⏳", "done": "✓", "error": "!"}[event["status"]]
            duration = f" · {event['durationMs'] / 1000:.1f} с" if "durationMs" in event else ""
            lines[event["key"]].write(f"{symbol} {event['name']}{duration}")

        try:
            outcome = service.search(query, online=st.session_state["online"], selected_id=selected_id,
                                     selected_english=selected_english, on_event=on_event)
            st.session_state["result"] = outcome
            if outcome["type"] == "result":
                st.session_state["history"] = [outcome] + [r for r in st.session_state["history"] if result_key(r) != result_key(outcome)]
                st.session_state["history"] = st.session_state["history"][:8]
                status.update(label="Справка готова", state="complete", expanded=False)
            else:
                status.update(label="Нужно уточнить понятие", state="complete", expanded=False)
        except SourceError as exc:
            st.session_state["error"] = str(exc)
            status.update(label="Справка не получена", state="error", expanded=False)
        except Exception:
            logging.getLogger(__name__).exception("Unexpected search failure")
            st.session_state["error"] = "Произошла ошибка при обработке запроса. Попробуйте снова."
            status.update(label="Справка не получена", state="error", expanded=False)


def render_result(result: dict) -> None:
    if result["type"] == "translation_choices":
        st.subheader("Уточните перевод термина")
        st.write(result["reason"])
        st.caption("Переводчик предложил: " + result["translatedQuery"] + ". Сходство написания не означает одинаковое медицинское значение.")
        for index, choice in enumerate(result["choices"]):
            if st.button(choice["english"], key=f"translation_choice_{index}", width="stretch"):
                run_search(result["query"], selected_english=choice["english"])
                st.rerun()
        return
    if result["type"] == "choices":
        st.subheader("Уточните понятие")
        st.write("В MeSH найдено несколько совпадений. Выберите нужное значение.")
        for choice in result["choices"]:
            if st.button(f"{choice['label']} · {choice['id']}", key="choice_" + choice["id"], width="stretch"):
                run_search(result["query"], choice["id"], selected_english=result.get("selectedEnglish"))
                st.rerun()
        return
    if result["type"] == "related":
        st.subheader("Найдены связанные материалы")
        st.info("Точного соответствия нет. Эти страницы относятся к выбранному понятию; они показаны отдельно от готового объяснения.")
        for choice in result["choices"]:
            if safe_medline_url(choice["url"]):
                st.link_button(choice["title"], choice["url"])
        return
    st.divider()
    main, actions = st.columns([3, 1], vertical_alignment="center")
    with main:
        st.caption(result["category"].upper())
        st.header(result["title"])
        st.caption(result["english"] + (f" · {result['mesh']['id']}" if result["mesh"].get("id") else ""))
    with actions:
        saved = result_key(result) in st.session_state["saved"]
        st.button("Убрать из сохранённого" if saved else "Сохранить справку", key="save_result", on_click=toggle_save, width="stretch")
        st.download_button("Скачать справку", export_markdown(result), file_name=f"medterm-{result['mesh'].get('id') or 'guide'}.md", mime="text/markdown", key="download_result", width="stretch")
    explanation, sources, agents = st.tabs(["Объяснение", "Источники", "Работа агентов"])
    with explanation:
        st.write(result["text"])
        if result["language"] != "ru":
            st.warning(result.get("translationNote", "Перевод отключён или временно недоступен. Показан исходный английский фрагмент."))
        source_caption(result["medline"])
        if safe_medline_url(result["medline"]["url"]):
            st.link_button("Открыть материал MedlinePlus ↗", result["medline"]["url"])
        if result.get("facts"):
            st.markdown("**Что важно запомнить**")
            for fact in result["facts"]:
                st.write("• " + fact)
        label = "Сохранённый пересказ на английском" if result["medline"]["provenance"] == "snapshot" else "Исходный английский фрагмент"
        with st.expander(label):
            st.text(result["original"])
        if result.get("translationPairs"):
            with st.expander("Сопоставить перевод с оригиналом"):
                for index, pair in enumerate(result["translationPairs"], 1):
                    st.caption(f"Фрагмент {index}")
                    left, right = st.columns(2)
                    left.write(pair["original"])
                    right.write(pair["translated"])
                st.caption("Совпадение чисел проверено программно. Смысл и медицинская точность перевода требуют отдельной оценки.")
        notes = {
            "live": "Использован материал, полученный в текущем запросе.",
            "snapshot": "Показана подготовленная справка по сохранённому материалу. Это не текущий ответ MedlinePlus.",
            "dataset": "Использована официальная выгрузка MedlinePlus от 11.11.2026.",
        }
        st.caption(notes.get(result["medline"]["provenance"], "") + " " + result["mode"] + ".")
    with sources:
        st.subheader("MeSH · точность понятия")
        source_caption(result["mesh"])
        if result["mesh"].get("id"):
            st.write(f"{result['mesh']['label']} · {result['mesh']['id']}")
            st.link_button("Открыть запись MeSH ↗", result["mesh"]["url"])
            if result["mesh"].get("definition"):
                with st.expander("Определение MeSH на английском"):
                    st.text(result["mesh"]["definition"])
        else:
            st.warning("Запись MeSH не подтверждена. Соответствие двум источникам не заявляется.")
        st.subheader("MedlinePlus · справочный материал")
        source_caption(result["medline"])
        st.write(result["medline"]["title"])
        if safe_medline_url(result["medline"]["url"]):
            st.link_button("Открыть первоисточник ↗", result["medline"]["url"])
        st.caption("Оба ресурса относятся к NLM и выполняют разные роли. Это не две независимые медицинские проверки.")
    with agents:
        st.subheader("Что выполнило приложение")
        st.dataframe([{"Агент": item["name"], "Результат": "Выполнен", "Время, с": round(item["durationMs"] / 1000, 2)} for item in result["trace"]], hide_index=True, width="stretch")
        for check in result["verification"]["checks"]:
            st.write(("✓ " if check["ok"] else "! ") + check["label"])
        st.caption(result["verification"]["note"])
        st.caption("Понимание запроса: " + result["translation"] + ". Способ объяснения: " + result["mode"] + ".")


def render_search() -> None:
    st.html('<div class="med-hero"><div class="med-eyebrow">ВАШ ПРОВОДНИК В МЕДИЦИНСКИХ ТЕРМИНАХ</div><h1>Понятно о <em>сложном.</em></h1><p>Разберитесь в незнакомом медицинском понятии.<br>Объяснение, точный термин и первоисточники — рядом.</p></div>')
    with st.form("search_form"):
        st.subheader("Какое понятие разберём?")
        entry, submit = st.columns([4, 1], vertical_alignment="bottom")
        with entry:
            st.text_input("Название заболевания или состояния", key="query", max_chars=120, placeholder="Например, бронхиальная астма")
        with submit:
            submitted = st.form_submit_button("Объяснить ↗", type="primary", width="stretch")
        st.caption("Русский или английский термин. Не вводите симптомы, имена или личные медицинские данные.")
    examples = [("Астма", "Бронхиальная астма", "asthma"), ("Гипертензия", "Артериальная гипертензия", "hypertension"), ("Анемия", "Анемия", "anemia"), ("Мигрень", "Мигрень", "migraine")]
    for column, (label, query, key) in zip(st.columns(4), examples):
        column.button(label, key="example_" + key, on_click=queue_example, args=(query,), width="stretch")
    pending = st.session_state.pop("pending_query", None)
    if submitted or pending:
        run_search(pending or st.session_state["query"])
    if st.session_state["error"]:
        st.error(st.session_state["error"])
    if st.session_state["result"]:
        render_result(st.session_state["result"])
    else:
        st.write("")
        left, right = st.columns(2)
        with left:
            with st.container(border=True):
                st.subheader("01 · MeSH")
                st.write("Стандартизированный медицинский словарь: уточняет понятие и помогает отличить похожие названия.")
                st.link_button("Открыть MeSH ↗", "https://id.nlm.nih.gov/mesh/")
        with right:
            with st.container(border=True):
                st.subheader("02 · MedlinePlus")
                st.write("Материалы для пациентов. Для устойчивой работы доступна официальная выгрузка на 1017 тем.")
                st.link_button("Открыть MedlinePlus ↗", "https://medlineplus.gov/")


def render_saved() -> None:
    st.header("Сохранённые понятия")
    st.caption("Справки хранятся в текущем сеансе Streamlit. Скачайте их, чтобы сохранить после закрытия или перезагрузки страницы.")
    saved = st.session_state["saved"]
    if not saved:
        st.info("Пока пусто. Найдите понятие и нажмите «Сохранить справку».")
        return
    st.download_button("Скачать все справки", "\n\n---\n\n".join(export_markdown(result) for result in saved.values()), "medterm-collection.md", "text/markdown", key="download_collection")
    for key, result in list(saved.items()):
        with st.container(border=True):
            details, actions = st.columns([3, 1])
            with details:
                st.subheader(result["title"])
                st.caption(result["english"] + " · " + readable_date(result["createdAt"]))
            with actions:
                st.button("Открыть", key="open_" + key, on_click=open_saved, args=(key,), width="stretch")
                if st.button("Удалить", key="remove_" + key, width="stretch"):
                    del st.session_state["saved"][key]
                    st.rerun()


def render_about() -> None:
    st.header("От понятия к пониманию")
    st.write("Учебный медицинский справочник на Streamlit. Python-агенты собирают и обрабатывают сведения MeSH и MedlinePlus.")
    st.subheader("Пять этапов обработки")
    descriptions = [
        ("Планировщик", "Нормализует запрос и определяет английское понятие. Для незнакомых русских терминов использует перевод MyMemory."),
        ("Агент MeSH", "Находит запись словаря и определение. При неоднозначности предлагает выбрать понятие."),
        ("Агент MedlinePlus", "Ищет справку по теме; при сбоях сервиса обращается к официальной выгрузке."),
        ("Агент объяснения", "Извлекает содержательные предложения и переводит их. Для шести сохранённых примеров доступна подготовленная русская справка."),
        ("Агент проверки", "Проверяет ссылки, наличие оригинала и запись MeSH. Не выполняет клиническую экспертизу."),
    ]
    for index, (name, text) in enumerate(descriptions, 1):
        with st.container(border=True):
            st.markdown(f"**{index:02d} · {name}**")
            st.write(text)
    st.subheader("Прозрачность и ограничения")
    st.write("В этой версии агенты работают по заданным правилам. Генеративная языковая модель не подключена. Варианты перевода MyMemory сопоставляются со словарём источника; похожие и неоднозначные названия предлагаются на выбор. В переводе справок проверяется сохранение чисел, а проверенные фрагменты кешируются. Эти проверки не гарантируют правильность медицинского смысла.")
    st.write(f"Локальная база содержит {len(service.dataset)} англоязычных тем MedlinePlus из официальной выгрузки от 11.11.2026. Её дата и происхождение указаны в ответе. Подготовлены {len(service.topics)} русских примеров и проверенные записи MeSH.")
    st.caption("Источник материалов: MedlinePlus, National Library of Medicine. Русские объяснения не являются официальным переводом NLM.")
    for label, url in [("Документация MeSH", "https://id.nlm.nih.gov/mesh/"), ("Документация MedlinePlus", "https://medlineplus.gov/about/developers/webservices/"), ("Официальные выгрузки MedlinePlus", "https://medlineplus.gov/xml.html"), ("Условия использования MedlinePlus", "https://medlineplus.gov/about/using/usingcontent/")]:
        st.link_button(label + " ↗", url)


with st.sidebar:
    st.html('<div class="med-brand">МедТермин<span style="color:#8da574">+</span></div>')
    st.radio("Раздел", ["Справочник", "Сохранённое", "О проекте"], key="navigation", label_visibility="collapsed")
    st.divider()
    st.checkbox("Проверять источники онлайн", value=True, key="online", help="Если выключено, сеть не используется. Шесть примеров доступны на русском, остальные материалы из выгрузки — на английском.")
    st.caption("При сбоях источников приложение использует сохранённые материалы и явно отмечает это.")
    st.metric("Тем в базе MedlinePlus", len(service.dataset))
    st.caption(f"Сохранено в сеансе: {len(st.session_state['saved'])}")
    st.divider()
    st.caption("MeSH уточняет понятие. MedlinePlus даёт объяснение. Оба источника относятся к NLM.")

if st.session_state.pop("notice", None):
    st.toast("Сохранённое обновлено")
{"Справочник": render_search, "Сохранённое": render_saved, "О проекте": render_about}[st.session_state["navigation"]]()
st.info("Справочная информация помогает понять термин. Она не устанавливает диагноз и не заменяет консультацию специалиста.", icon="ℹ️")
st.html('<div class="med-footer">МедТермин · Учебный проект, 2026<br>Источник справочных материалов: MedlinePlus, National Library of Medicine.</div>')
