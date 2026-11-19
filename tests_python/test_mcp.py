import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from medterm_mcp import client
from medterm_mcp.launch import port_ready, stop
from medterm_mcp.sanitize import sanitize
from medterm_mcp.ui import cached_external, route

ROOT = Path(__file__).resolve().parents[1]


def packet_for(exchange, method):
    request = next(item["message"] for item in exchange.packets if item["message"].get("method") == method)
    response = next(item["message"] for item in exchange.packets if item["direction"].startswith("Сервер") and item["message"].get("id") == request["id"])
    return request, response


class MCPTests(unittest.TestCase):
    def test_stdio_catalog_and_bmi_use_real_rpc(self):
        listing = client.exchange_with("stdio")
        self.assertIsNone(listing.error)
        self.assertEqual({item["name"] for item in listing.tools}, {"bmi", "body_surface_area", "glasgow_coma_scale", "list_calculators"})
        packet_for(listing, "initialize")
        packet_for(listing, "tools/list")
        catalog = client.exchange_with("stdio", "list_calculators").result["structuredContent"]["calculators"]
        bmi = next(item for item in catalog if item["name"] == "bmi")
        self.assertEqual(bmi["parameters"]["height_cm"]["minimum"], 100)
        self.assertEqual(bmi["units"]["result"], "кг/м²")
        exchange = client.exchange_with("stdio", "bmi", {"height_cm": 175, "weight_kg": 70, "adult_categories": True})
        sent, received = packet_for(exchange, "tools/call")
        self.assertEqual(sent["params"]["arguments"]["weight_kg"], 70)
        self.assertAlmostEqual(received["result"]["structuredContent"]["value"], 22.857142857)
        self.assertEqual(received["result"]["structuredContent"]["color"], "green")

    def test_sse_calculation_and_server_rejections(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-m", "medterm_mcp.server", "--transport", "sse", "--port", str(port)], cwd=ROOT, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.addCleanup(stop, server)
        with patch.object(client, "SSE_PORT", port):
            deadline = time.monotonic() + 10
            while not port_ready(port) and time.monotonic() < deadline and server.poll() is None:
                time.sleep(0.1)
            self.assertTrue(port_ready(port))
            listing = client.exchange_with("sse")
            self.assertIsNone(listing.error)
            self.assertEqual(len(listing.tools), 6)
            exchange = client.exchange_with("sse", "mean_arterial_pressure", {"systolic": 120, "diastolic": 78})
            _, received = packet_for(exchange, "tools/call")
            self.assertEqual(received["result"]["structuredContent"]["value"], 92)
            for invalid in ({"systolic": 70, "diastolic": 90}, {"systolic": 1000, "diastolic": 90}):
                with self.subTest(arguments=invalid):
                    exchange = client.exchange_with("sse", "mean_arterial_pressure", invalid)
                    _, received = packet_for(exchange, "tools/call")
                    self.assertTrue(received["result"]["isError"])

    def test_gcs_does_not_score_non_testable_component(self):
        exchange = client.exchange_with("stdio", "glasgow_coma_scale", {"eye": "4 — Спонтанно", "verbal": "NT — Не оценивается", "motor": "6 — Выполняет команды"})
        self.assertIsNone(exchange.error)
        self.assertIsNone(exchange.result["structuredContent"]["value"])

    def test_sanitizer_preserves_measurement_and_does_not_guess_it(self):
        schema = {"required": ["measure"], "properties": {"measure": {"type": "number"}, "extra": {"type": "integer", "default": 2}}}
        self.assertEqual(sanitize("test", schema, {"measure": "99,5"}), {"measure": 99.5, "extra": 2})
        for value in (None, "nan", "0x11"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sanitize("test", schema, {"measure": value})

    def test_pubmed_failures_not_cached_but_success_is(self):
        success = client.Exchange(result={"isError": False, "structuredContent": {"totalCount": 1}})
        failure = client.Exchange(error="Таймаут")
        cached_external.clear()
        self.addCleanup(cached_external.clear)
        with patch("medterm_mcp.ui.exchange_with", side_effect=[failure, success]) as exchange:
            arguments = {"query": "asthma"}
            self.assertEqual(route("pubmed", client.PUBMED_TOOL, arguments), failure)
            self.assertEqual(route("pubmed", client.PUBMED_TOOL, arguments), success)
            self.assertEqual(route("pubmed", client.PUBMED_TOOL, arguments), success)
            self.assertEqual(exchange.call_count, 2)

    def test_streamlit_generates_real_bmi_form_and_shows_rpc(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        app.radio(key="navigation").set_value("Инструменты MCP").run()
        app.selectbox(key="mcp_tool_stdio").set_value("bmi").run()
        self.assertIsNone(app.number_input(key="mcp_stdio_bmi_height_cm").value)
        app.button(key="mcp_stdio_bmi_example").click().run()
        self.assertEqual(app.number_input(key="mcp_stdio_bmi_height_cm").value, 175)
        app.button(key="FormSubmitter:mcp_stdio_bmi-Вызвать инструмент").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(metric.value == "22,86 кг/м²" for metric in app.metric))
        self.assertTrue(any('"method": "tools/call"' in item.value for item in app.code))
