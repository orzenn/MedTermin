import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def port_ready(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.15)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=8511)
    options = parser.parse_args()
    ports = (options.mcp_port, options.web_port)
    if any(not 1 <= port <= 65535 for port in ports) or options.mcp_port == options.web_port:
        parser.error("Выберите два разных порта от 1 до 65535.")
    for port in ports:
        if port_ready(port):
            parser.error(f"Порт {port} занят. Укажите другой порт.")
    env = {**os.environ, "MEDTERM_MCP_PORT": str(options.mcp_port)}
    server = subprocess.Popen([sys.executable, "-m", "medterm_mcp.server", "--transport", "sse", "--port", str(options.mcp_port)], cwd=ROOT)
    web = None
    try:
        deadline = time.monotonic() + 10
        while not port_ready(options.mcp_port):
            if server.poll() is not None or time.monotonic() >= deadline:
                print("Не удалось запустить локальный SSE-сервер. Проверьте журнал.", file=sys.stderr)
                return 1
            time.sleep(0.15)
        web = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1", "--server.port", str(options.web_port), "--server.headless", "true"], cwd=ROOT, env=env)
        return web.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if web is not None:
            stop(web)
        stop(server)


if __name__ == "__main__":
    raise SystemExit(main())
