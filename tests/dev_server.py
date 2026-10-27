"""Локальный стенд без Telegram: API на /code-api, Mini App на /code с подставленным initData владельца.

    BOT_TOKEN=123:dev OWNER_ID=1 python tests/dev_server.py   → http://127.0.0.1:8031/code/

Только для разработки: initData подписывается тем же BOT_TOKEN, что проверяет сервер.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import app as backend  # noqa: E402
import config  # noqa: E402
from initdata import make_init_data  # noqa: E402

dev = FastAPI()
dev.mount("/code-api", backend.app)

STUB = """<script>
window.Telegram = { WebApp: { initData: %s, ready() {}, expand() {}, setHeaderColor() {},
  showConfirm(text, cb) { cb(confirm(text)) },
  BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
  HapticFeedback: { impactOccurred() {}, notificationOccurred() {}, selectionChanged() {} } } };
</script>
"""


@dev.get("/code/", response_class=HTMLResponse)
def index() -> str:
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    stub = STUB % json.dumps(make_init_data(config.BOT_TOKEN, config.OWNER_ID))
    return html.replace('<script type="module" src="app.js"></script>',
                        stub + '<script type="module" src="app.js"></script>')


dev.mount("/code", StaticFiles(directory=ROOT / "frontend"), name="frontend")

if __name__ == "__main__":
    uvicorn.run(dev, host="127.0.0.1", port=int(os.environ.get("PORT", "8031")))
