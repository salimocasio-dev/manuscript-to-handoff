"""Start the real Streamlit server and check its HTTP shell, health, and JS asset.

This is an HTTP smoke check, not browser rendering or widget-interaction QA.
The widget workflows are exercised separately by tests/test_ui.py.
"""
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    def get(path):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()
    with tempfile.TemporaryDirectory(prefix="mth-server-") as temporary:
        env = {**os.environ, "MTH_DB_PATH": str(Path(temporary) / "workflow.sqlite3")}
        env.pop("OPENAI_API_KEY", None)
        with (Path(temporary) / "server.log").open("wb") as log:
            server = subprocess.Popen([
                sys.executable, "-m", "streamlit", "run", "app.py",
                "--server.address", "127.0.0.1", "--server.port", str(port),
                "--server.headless", "true", "--browser.gatherUsageStats", "false",
                "--browser.serverAddress", "127.0.0.1",
            ], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        status, health = get("/_stcore/health")
                        if status == 200:
                            break
                    except (OSError, http.client.HTTPException):
                        pass
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Streamlit did not become healthy within 20 seconds")
                    time.sleep(.1)
                root_status, html = get("/")
                assert root_status == 200
                script = re.search(rb'<script[^>]+src="([^"]+\.js)"', html)
                assert script, "No JavaScript entrypoint in the served app shell"
                asset = "/" + script.group(1).decode().removeprefix("./").lstrip("/")
                asset_status, payload = get(asset)
                assert asset_status == 200 and len(payload) > 100
                return {"executed_at": datetime.now(timezone.utc).isoformat(),
                        "root_http_status": root_status, "health_http_status": status,
                        "health_body": health.decode(), "frontend_asset_http_status": asset_status,
                        "frontend_asset_bytes": len(payload),
                        "scope": "Real HTTP server and frontend asset smoke check. Does not prove browser rendering or clicks."}
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
