"""Смоук API без Telegram и без Claude: вход по initData, чаты, журнал событий.
Живые сессии Claude здесь не запускаются — если claude_agent_sdk не установлен, подставляется заглушка."""
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

BOT_TOKEN = "123456:TEST-token-for-tests-only"
OWNER = 1000


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ.update(BOT_TOKEN=BOT_TOKEN, OWNER_ID=str(OWNER),
                      POCKET_DATA_DIR=str(tmp_path_factory.mktemp("data")))
    if importlib.util.find_spec("claude_agent_sdk") is None:
        stub = types.ModuleType("claude_agent_sdk")
        for name in ("AssistantMessage", "ClaudeAgentOptions", "ClaudeSDKClient", "PermissionResultAllow",
                     "PermissionResultDeny", "ResultMessage", "StreamEvent", "SystemMessage", "TextBlock",
                     "ThinkingBlock", "ToolResultBlock", "ToolUseBlock", "UserMessage"):
            setattr(stub, name, type(name, (), {}))
        sys.modules["claude_agent_sdk"] = stub
    from fastapi.testclient import TestClient
    import app
    with TestClient(app.app) as c:
        yield c


def login(client, user_id=OWNER, token=BOT_TOKEN):
    from initdata import make_init_data
    return client.post("/auth", json={"init_data": make_init_data(token, user_id)})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_vhod_vladeltsa(client):
    r = login(client)
    assert r.status_code == 200
    d = r.json()
    assert d["token"] and d["modeli"] == ["opus", "sonnet", "haiku"]


def test_chuzhoy_polzovatel_403(client):
    assert login(client, user_id=OWNER + 1).status_code == 403


def test_poddelnaya_podpis_401(client):
    assert login(client, token="999:other-bot").status_code == 401


def test_bez_tokena_401(client):
    assert client.get("/chats").status_code == 401


def test_chat_i_zhurnal(client, tmp_path):
    h = {"Authorization": f"Bearer {login(client).json()['token']}"}
    r = client.post("/chats", headers=h, json={"project": str(tmp_path), "title": "Проверка", "effort": "low"})
    assert r.status_code == 200, r.text
    chat = r.json()
    assert chat["status"] == "idle" and chat["effort"] == "low"
    assert any(c["id"] == chat["id"] for c in client.get("/chats", headers=h).json()["chats"])
    r = client.patch(f"/chats/{chat['id']}", headers=h, json={"title": "Новое имя"})
    assert r.status_code == 200 and r.json()["title"] == "Новое имя"
    r = client.get(f"/chats/{chat['id']}", headers=h)
    assert r.status_code == 200 and r.json()["chat"]["id"] == chat["id"]
    assert client.delete(f"/chats/{chat['id']}", headers=h).status_code == 200


def test_neizvestnaya_model_422(client):
    h = {"Authorization": f"Bearer {login(client).json()['token']}"}
    assert client.post("/chats", headers=h, json={"project": "/tmp", "model": "gpt"}).status_code == 422
