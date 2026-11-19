import argparse
import logging
import sys
from typing import get_type_hints

from mcp.server import MCPServer
from pydantic import TypeAdapter

from .calculators import CATALOG, NETWORK_TOOLS, STDIO_TOOLS, CalculatorCatalog, CalculatorMetadata


def build_server(transport: str) -> MCPServer:
    calculators = STDIO_TOOLS if transport == "stdio" else NETWORK_TOOLS
    server = MCPServer(f"МедТермин: {transport}")
    for calculator in calculators:
        server.tool(title=CATALOG[calculator.__name__]["title"], structured_output=True)(calculator)

    @server.tool(title="Каталог клинических инструментов · list_calculators", structured_output=True)
    def list_calculators() -> CalculatorCatalog:
        """Методика, единицы и допустимые диапазоны входных параметров."""
        return CalculatorCatalog(calculators=[
            CalculatorMetadata(name=tool.__name__, **CATALOG[tool.__name__], parameters={
                name: TypeAdapter(annotation).json_schema()
                for name, annotation in get_type_hints(tool, include_extras=True).items() if name != "return"
            })
            for tool in calculators
        ])

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "sse"), default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    options = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    server = build_server(options.transport)
    if options.transport == "sse":
        server.run(transport="sse", host="127.0.0.1", port=options.port, sse_path="/sse", message_path="/messages/")
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
