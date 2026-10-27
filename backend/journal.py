"""Журнал чатов и событий (SQLite).

Журнал — источник правды. Живой поток нужен только для скорости: всё, что показано
в приложении, лежит здесь и переживает закрытие Telegram, обрыв связи и рестарт сервиса.
"""
import json
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SKHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT,
    project           TEXT NOT NULL,
    model             TEXT NOT NULL DEFAULT 'sonnet',
    effort            TEXT NOT NULL DEFAULT 'high',
    permission_mode   TEXT NOT NULL DEFAULT 'bypassPermissions',
    claude_session_id TEXT,
    status            TEXT NOT NULL DEFAULT 'idle',
    last_seq          INTEGER NOT NULL DEFAULT 0,
    created           REAL NOT NULL,
    updated           REAL NOT NULL,
    archived          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    chat_id INTEGER NOT NULL,
    seq     INTEGER NOT NULL,
    ts      REAL NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (chat_id, seq)
);
CREATE INDEX IF NOT EXISTS events_chat ON events(chat_id, seq);
"""


def podklyuchit() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SKHEMA)
        _migratsii(_conn)
        _conn.commit()
    return _conn


def _migratsii(c: sqlite3.Connection):
    """Добавляем недостающие колонки, чтобы обновление не требовало сноса базы."""
    est = {r["name"] for r in c.execute("PRAGMA table_info(chats)")}
    novye = {
        "fork_pending": "INTEGER NOT NULL DEFAULT 0",   # первый прогон ответвится от сессии
    }
    for imya, opredelenie in novye.items():
        if imya not in est:
            c.execute(f"ALTER TABLE chats ADD COLUMN {imya} {opredelenie}")


def sozdat_chat(project: str, title: str | None = None, model: str = "sonnet",
                effort: str = "high", permission_mode: str = "bypassPermissions",
                claude_session_id: str | None = None) -> dict:
    now = time.time()
    with _lock:
        c = podklyuchit()
        cur = c.execute(
            "INSERT INTO chats (title, project, model, effort, permission_mode, "
            "claude_session_id, created, updated) VALUES (?,?,?,?,?,?,?,?)",
            (title, project, model, effort, permission_mode, claude_session_id, now, now))
        c.commit()
        return poluchit_chat(cur.lastrowid)


def poluchit_chat(chat_id: int) -> dict | None:
    r = podklyuchit().execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    return dict(r) if r else None


def spisok_chatov(archived: bool = False) -> list[dict]:
    rows = podklyuchit().execute(
        "SELECT * FROM chats WHERE archived=? ORDER BY updated DESC",
        (1 if archived else 0,)).fetchall()
    return [dict(r) for r in rows]


def obnovit_chat(chat_id: int, **polya) -> dict | None:
    if ne_pusto := {k: v for k, v in polya.items() if v is not None or k == "title"}:
        ne_pusto["updated"] = time.time()
        kuski = ", ".join(f"{k}=?" for k in ne_pusto)
        with _lock:
            c = podklyuchit()
            c.execute(f"UPDATE chats SET {kuski} WHERE id=?",
                      (*ne_pusto.values(), chat_id))
            c.commit()
    return poluchit_chat(chat_id)


def dobavit_sobytie(chat_id: int, tip: str, dannye: dict) -> dict:
    """Пишет событие и возвращает его вместе с порядковым номером."""
    now = time.time()
    with _lock:
        c = podklyuchit()
        cur = c.execute("UPDATE chats SET last_seq = last_seq + 1, updated=? WHERE id=?",
                        (now, chat_id))
        if cur.rowcount == 0:
            raise ValueError(f"чат {chat_id} не найден")
        seq = c.execute("SELECT last_seq FROM chats WHERE id=?", (chat_id,)).fetchone()[0]
        c.execute("INSERT INTO events (chat_id, seq, ts, type, payload) VALUES (?,?,?,?,?)",
                  (chat_id, seq, now, tip, json.dumps(dannye, ensure_ascii=False)))
        c.commit()
    return {"seq": seq, "ts": now, "type": tip, **dannye}


def sobytiya(chat_id: int, posle: int = 0, predel: int = 2000) -> list[dict]:
    rows = podklyuchit().execute(
        "SELECT seq, ts, type, payload FROM events WHERE chat_id=? AND seq>? "
        "ORDER BY seq LIMIT ?", (chat_id, posle, predel)).fetchall()
    return [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], **json.loads(r["payload"])}
            for r in rows]


def khvost(chat_id: int, skolko: int = 300) -> list[dict]:
    """Последние события — то, что нужно при открытии чата."""
    rows = podklyuchit().execute(
        "SELECT seq, ts, type, payload FROM events WHERE chat_id=? ORDER BY seq DESC LIMIT ?",
        (chat_id, skolko)).fetchall()
    rows = list(reversed(rows))
    return [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], **json.loads(r["payload"])}
            for r in rows]


def udalit_chat(chat_id: int):
    with _lock:
        c = podklyuchit()
        c.execute("DELETE FROM events WHERE chat_id=?", (chat_id,))
        c.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        c.commit()
