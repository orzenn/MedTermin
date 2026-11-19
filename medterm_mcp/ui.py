import json

import streamlit as st

from .calculators import CATALOG
from .client import PUBMED_TOOL, SSE_PORT, Exchange, exchange_with
from .sanitize import DEMOS, exposed_schema, sanitize

GLAMA = "https://glama.ai/mcp/servers/eLambert2560/pubmed-mcp-server"
SOURCES = {
    "stdio": ("Локальный stdio", "Калькуляторы работают в отдельном процессе. Приложение обменивается с ним сообщениями через stdin и stdout."),
    "sse": (f"Локальный SSE · порт {SSE_PORT}", "Клиент подписывается на события через /sse и отправляет запросы через /messages/; сервер работает отдельным процессом."),
    "pubmed": ("Внешний PubMed · HTTPS", "Поиск публикаций через публичный MCP-сервер PubMed по Streamable HTTP. Вводите только общие темы и термины."),
}


class UncacheableResponse(Exception):
    def __init__(self, exchange: Exchange):
        self.exchange = exchange


@st.cache_data(ttl=900, max_entries=64, show_spinner=False)
def cached_external(name: str | None = None, arguments: dict | None = None) -> Exchange:
    exchange = exchange_with("pubmed", name, arguments)
    if exchange.error or (exchange.result or {}).get("isError"):
        raise UncacheableResponse(exchange)
    return exchange


def route(source: str, name: str | None = None, arguments: dict | None = None) -> Exchange:
    if source != "pubmed":
        return exchange_with(source, name, arguments)
    try:
        return cached_external(name, arguments)
    except UncacheableResponse as error:
        return error.exchange


def tool_title(tool: dict) -> str:
    if tool["name"] == PUBMED_TOOL:
        return "Поиск публикаций · PubMed"
    return tool.get("title") or tool["name"]


def category(tool: dict) -> str:
    return CATALOG.get(tool["name"], {}).get("category", "Литература" if tool["name"] == PUBMED_TOOL else "Служебные")


def input_widget(name: str, field: dict, namespace: str, required: bool):
    label = field.get("title", name)
    options = {"key": f"{namespace}_{name}", "help": field.get("description")}
    default = field.get("default")
    if "enum" in field:
        options_list = field["enum"]
        return st.selectbox(label, options_list, index=options_list.index(default) if default in options_list else None, **options)
    field_type = field.get("type")
    if field_type == "boolean":
        return st.checkbox(label, value=bool(default), **options)
    if field_type in {"integer", "number"}:
        step, convert = (1, int) if field_type == "integer" else (0.1, float)
        if "minimum" in field and not required:
            return st.slider(label, convert(field["minimum"]), convert(field["maximum"]), convert(default), step=step, **options)
        bounds = ", ".join(f"{word} {field[key]:g}" for key, word in [("minimum", "от"), ("maximum", "до")] if key in field)
        options["help"] = " · ".join(filter(None, [options["help"], f"Допустимый диапазон: {bounds}" if bounds else None])) or None
        return st.number_input(label, value=convert(default) if default is not None else None, step=step, **options)
    return st.text_input(label, value=default or "", max_chars=field.get("maxLength", 200), **options)


def show_packet_inspector(exchange: Exchange):
    with st.expander("Инспектор MCP · JSON-RPC"):
        for index, packet in enumerate(exchange.packets, 1):
            message = packet["message"]
            label = message.get("method") or ("Ошибка" if "error" in message else "Ответ")
            request_id = f" · id {message['id']}" if "id" in message else ""
            st.caption(f"{index}. {packet['direction']} · {label}{request_id}")
            st.code(json.dumps(message, ensure_ascii=False, indent=2), language="json", height="content")


def show_pubmed(data: dict):
    total = data.get("totalCount")
    if total is not None:
        st.metric("Совпадений в PubMed", total)
    if data.get("effectiveQuery"):
        st.caption(f"Запрос PubMed: {data['effectiveQuery']}")
    articles = data.get("summaries") or []
    for article in articles:
        if not isinstance(article, dict):
            continue
        pmid = str(article.get("pmid", ""))
        with st.container(border=True):
            st.text(article.get("title") or f"PMID {pmid}")
            st.caption(" · ".join(str(article[key]) for key in ("source", "pubDate") if article.get(key)))
            if pmid.isascii() and pmid.isdigit():
                st.link_button(f"Открыть в PubMed · {pmid}", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
    if not articles and total != 0:
        st.json(data)
    if total == 0:
        st.info("Публикаций не найдено. Попробуйте изменить запрос.")


def show_result(tool: dict, exchange: Exchange):
    if exchange.error:
        st.warning(exchange.error)
    elif exchange.result:
        result = exchange.result
        if result.get("isError"):
            st.warning("Сервер отклонил запрос. Проверьте значения и допустимые диапазоны.")
            for block in result.get("content", []):
                if block.get("type") == "text":
                    st.text(block["text"])
        elif data := result.get("structuredContent"):
            if tool["name"] == PUBMED_TOOL:
                show_pubmed(data)
            elif tool["name"] == "list_calculators":
                for item in data["calculators"]:
                    with st.container(border=True):
                        st.markdown(f"**{item['title']}**")
                        st.write(item["description"])
                        st.link_button("Источник методики", item["source"])
                        st.json(item["parameters"], expanded=False)
            else:
                value = data["value"]
                display = "Не определяется" if value is None else f"{value:.4g} {data['units']['result']}".replace(".", ",")
                st.metric(data["title"], display)
                status = {"green": st.success, "yellow": st.warning, "red": st.error, "neutral": st.info}
                status[data["color"]](data["label"])
                st.table([{"Показатель": key, "Значение": str(value)} for key, value in data["components"].items()])
                st.caption(data["note"])
                st.link_button("Источник методики", data["source"])
        else:
            for block in result.get("content", []):
                if block.get("type") == "text":
                    st.text(block["text"])
    if exchange.packets:
        show_packet_inspector(exchange)


def render_mcp() -> None:
    st.header("Инструменты MCP")
    st.caption("Практическая работа №2 · Клинические калькуляторы и поиск публикаций")
    source = st.radio("Источник инструментов", list(SOURCES), format_func=lambda item: SOURCES[item][0], horizontal=True, key="mcp_source")
    st.caption(SOURCES[source][1])
    if source == "pubmed":
        st.link_button("Карточка сервера в Glama", GLAMA)
        st.caption("Успешные запросы кешируются до 15 минут. Инспектор показывает исходный обмен, в том числе при повторном показе из кеша.")
    refresh = st.button("Обновить список инструментов", key="mcp_refresh")
    if st.session_state.get("mcp_active_source") != source or refresh:
        with st.spinner("Получаем список инструментов…"):
            st.session_state["mcp_listing"] = route(source)
        st.session_state["mcp_active_source"] = source
        st.session_state.pop("mcp_last_call", None)

    listing = st.session_state["mcp_listing"]
    if listing.error:
        st.warning(listing.error)
        if listing.packets:
            show_packet_inspector(listing)
        return
    if not listing.tools:
        st.info("Сервер не объявил доступных инструментов.")
        return

    categories = list(dict.fromkeys(map(category, listing.tools)))
    selected_category = st.selectbox("Раздел инструментов", ["Все", *categories], key=f"mcp_category_{source}") if len(categories) > 1 else "Все"
    available = {tool["name"]: tool for tool in listing.tools if selected_category in ("Все", category(tool))}
    name = st.selectbox("Инструмент · начните вводить название или сокращение", list(available),
                        format_func=lambda item: f"{tool_title(available[item])} · {item}", key=f"mcp_tool_{source}")
    tool = available[name]
    schema = exposed_schema(tool)
    namespace = f"mcp_{source}_{name}"
    local_meta = CATALOG.get(name, {})
    st.write(local_meta.get("description", "Поиск библиографических записей в базе PubMed." if name == PUBMED_TOOL else tool.get("description", "")))
    if name in DEMOS and st.button("Подставить учебный пример", key=f"{namespace}_example"):
        for key, value in DEMOS[name].items():
            st.session_state[f"{namespace}_{key}"] = value

    with st.form(namespace):
        values = {key: input_widget(key, spec, namespace, key in schema.get("required", []))
                  for key, spec in schema.get("properties", {}).items()}
        submit = st.form_submit_button("Искать публикации" if source == "pubmed" else "Вызвать инструмент", type="primary")
    if submit:
        try:
            arguments = sanitize(name, schema, values)
        except ValueError as error:
            st.warning(str(error))
            st.session_state.pop("mcp_last_call", None)
        else:
            with st.spinner("Ожидаем ответ MCP-сервера…"):
                st.session_state["mcp_last_call"] = (source, name, route(source, name, arguments))
    last = st.session_state.get("mcp_last_call")
    if last and last[:2] == (source, name):
        show_result(tool, last[2])
    else:
        show_packet_inspector(listing)
