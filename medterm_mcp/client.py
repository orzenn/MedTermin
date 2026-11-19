import asyncio
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx2
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

PUBMED_URL = "https://pubmed.caseyjhand.com/mcp"
PUBMED_TOOL = "pubmed_search_articles"
SSE_PORT = int(os.environ.get("MEDTERM_MCP_PORT", "8000"))
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Exchange:
    packets: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None

    def capture(self, direction, item):
        if not isinstance(item, Exception):
            self.packets.append({
                "direction": direction,
                "message": item.message.model_dump(mode="json", by_alias=True, exclude_none=True),
            })

    @property
    def call_started(self) -> bool:
        return any(packet["message"].get("method") == "tools/call" for packet in self.packets)


class RecordingReceiver(ObjectReceiveStream):
    def __init__(self, stream, exchange):
        self.stream, self.exchange = stream, exchange

    async def receive(self):
        item = await self.stream.receive()
        self.exchange.capture("Сервер → клиент", item)
        return item

    async def aclose(self):
        await self.stream.aclose()


class RecordingSender(ObjectSendStream):
    def __init__(self, stream, exchange):
        self.stream, self.exchange = stream, exchange

    async def send(self, item):
        self.exchange.capture("Клиент → сервер", item)
        await self.stream.send(item)

    async def aclose(self):
        await self.stream.aclose()


@asynccontextmanager
async def connect(source: str):
    if source == "stdio":
        params = StdioServerParameters(command=sys.executable, args=["-m", "medterm_mcp.server"], cwd=ROOT)
        async with stdio_client(params) as streams:
            yield streams
    elif source == "sse":
        async with sse_client(f"http://127.0.0.1:{SSE_PORT}/sse", timeout=2, sse_read_timeout=8) as streams:
            yield streams
    elif source == "pubmed":
        async with httpx2.AsyncClient(timeout=8.0) as http:
            async with streamable_http_client(PUBMED_URL, http_client=http) as streams:
                yield streams[:2]
    else:
        raise ValueError(f"Неизвестный источник: {source}")


def exception_leaves(error):
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            yield from exception_leaves(nested)
    else:
        yield error


async def request(source: str, name: str | None = None, arguments: dict | None = None) -> Exchange:
    attempts = 3 if source == "sse" else 1
    for attempt in range(attempts):
        exchange = Exchange()
        try:
            with anyio.fail_after(12 if source == "pubmed" else 8):
                async with connect(source) as (reader, writer):
                    async with ClientSession(RecordingReceiver(reader, exchange), RecordingSender(writer, exchange)) as session:
                        await session.initialize()
                        if name is None:
                            listing = await session.list_tools()
                            exchange.tools = [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in listing.tools]
                            if source == "pubmed":
                                exchange.tools = [tool for tool in exchange.tools if tool["name"] == PUBMED_TOOL]
                                if not exchange.tools:
                                    exchange.error = "Внешний сервер не объявил инструмент поиска PubMed."
                        else:
                            result = await session.call_tool(name, arguments or {})
                            exchange.result = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            return exchange
        except Exception as error:
            causes = tuple(exception_leaves(error))
            if source == "sse" and attempt + 1 < attempts and not exchange.call_started and any(
                isinstance(item, (httpx2.ConnectError, httpx2.ConnectTimeout)) for item in causes
            ):
                await anyio.sleep(0.3 * (attempt + 1))
                continue
            if any(isinstance(item, (TimeoutError, httpx2.TimeoutException)) for item in causes):
                exchange.error = "Сервер не успел ответить. Повторите запрос позже."
            elif source == "sse":
                exchange.error = f"Не удалось связаться с SSE-сервером на порту {SSE_PORT}. Запустите приложение через python -m medterm_mcp.launch."
            elif source == "pubmed":
                exchange.error = "Внешний MCP PubMed недоступен. Проверьте сеть и повторите запрос позже."
            else:
                exchange.error = "Не удалось завершить обмен с дочерним MCP-процессом. Проверьте окружение и журнал запуска."
            return exchange
    raise AssertionError("Число попыток должно быть положительным")


def exchange_with(source: str, name: str | None = None, arguments: dict | None = None) -> Exchange:
    return asyncio.run(request(source, name, arguments))
