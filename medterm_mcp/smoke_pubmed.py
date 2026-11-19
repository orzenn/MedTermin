from .client import PUBMED_TOOL, exchange_with
from .sanitize import DEMOS, exposed_schema, sanitize


def main() -> None:
    listing = exchange_with("pubmed")
    if listing.error or not listing.tools:
        raise SystemExit(listing.error or "Инструмент PubMed не объявлен сервером.")
    schema = exposed_schema(listing.tools[0])
    arguments = sanitize(PUBMED_TOOL, schema, {**DEMOS[PUBMED_TOOL], "maxResults": 2})
    exchange = exchange_with("pubmed", PUBMED_TOOL, arguments)
    result = exchange.result or {}
    data = result.get("structuredContent") or {}
    if exchange.error or result.get("isError") or not data.get("summaries"):
        raise SystemExit(exchange.error or str(result.get("content", [])))
    print(f"PubMed MCP: {data['totalCount']} matches; sample PMIDs: {[item['pmid'] for item in data['summaries']]}")


if __name__ == "__main__":
    main()
