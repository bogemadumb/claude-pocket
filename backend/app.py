"""Claude в кармане — backend Mini App.

Этап 1: чаты, живой поток событий (NDJSON), отправка задач, прерывание.
Вход только по Telegram initData владельца.
"""
import asyncio
import json
import logging
import os
import time

import jwt
from fastapi import (Depends, FastAPI, File, Form, Header, HTTPException, Query,
                     UploadFile)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import config
import files as files_mod
import journal
import sessions
import tg_auth

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("claude-app")

app = FastAPI(title="Claude в кармане", version="0.2.0")

MODELI = ["opus", "sonnet", "haiku"]
EFFORTY = ["low", "medium", "high", "xhigh", "max", "ultracode"]
REZHIMY = ["bypassPermissions", "acceptEdits", "default", "plan"]


@app.on_event("startup")
async def start():
    journal.podklyuchit()
    # Прогоны не переживают рестарт сервиса: помечаем осиротевшие чаты честно.
    for chat in journal.spisok_chatov():
        # waiting — это тоже прерванный прогон: он ждал ответа на вопрос, которого
        # уже некому дождаться. Иначе статус остаётся в базе навсегда.
        if chat["status"] in ("running", "waiting"):
            journal.obnovit_chat(chat["id"], status="idle")
            journal.dobavit_sobytie(chat["id"], "system",
                                     {"text": "Сервис перезапустился — прогон прерван. "
                                              "Контекст сохранён, можно продолжать."})
    asyncio.create_task(_uborka())


async def _uborka():
    while True:
        await asyncio.sleep(300)
        try:
            await sessions.pribratsya()
        except Exception:
            log.exception("уборка")


@app.get("/health")
def health():
    aktivnye = sessions.aktivnye()
    return {"status": "ok", "stage": 1,
            "zhivykh_sessiy": len(aktivnye),
            "running": [zh.chat_id for zh in aktivnye if zh.status == "running"]}


# ---------- вход ----------
class AuthIn(BaseModel):
    init_data: str


@app.post("/auth")
def auth(body: AuthIn):
    try:
        dannye = tg_auth.validate_init_data(body.init_data, config.BOT_TOKEN,
                                            config.INITDATA_MAX_AGE)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"invalid initData: {e}")
    user = dannye.get("user") or {}
    uid = int(user.get("id", 0))
    if uid != config.OWNER_ID:
        log.warning("Отказ: чужой user.id=%s", uid)
        raise HTTPException(status_code=403, detail="not owner")
    token = jwt.encode({"uid": uid, "exp": int(time.time()) + config.JWT_TTL_SECONDS},
                       config.JWT_SECRET, algorithm="HS256")
    return {"token": token,
            "user": {"id": uid, "first_name": user.get("first_name")},
            "projects": config.PROJECTS,
            "modeli": MODELI, "efforty": EFFORTY, "rezhimy": REZHIMY}


def _proverit_token(token: str) -> int:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"bad token: {e}")
    if int(payload.get("uid", 0)) != config.OWNER_ID:
        raise HTTPException(status_code=403, detail="not owner")
    return config.OWNER_ID


def require_auth(authorization: str = Header(None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="no token")
    klyuch = authorization[7:]
    if klyuch == config.SERVICE_TOKEN:      # бот-мост: он ходит без Telegram initData
        return config.OWNER_ID
    return _proverit_token(klyuch)


@app.get("/projects")
def projects(_: int = Depends(require_auth)):
    return {"projects": config.PROJECTS}


# ---------- чаты ----------
class ChatIn(BaseModel):
    project: str
    title: str | None = None
    model: str = "sonnet"
    effort: str = "high"
    permission_mode: str = "bypassPermissions"
    claude_session_id: str | None = None


class ChatPatch(BaseModel):
    title: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    project: str | None = None
    archived: int | None = None


class AttachmentIn(BaseModel):
    path: str
    name: str | None = None
    mime: str | None = None
    size: int | None = None
    kartinka: bool | None = None


class MessageIn(BaseModel):
    text: str = ""
    attachments: list[AttachmentIn] = []


def _proverit_polya(model=None, effort=None, permission_mode=None):
    if model and model not in MODELI:
        raise HTTPException(422, f"модель? {model}")
    if effort and effort not in EFFORTY:
        raise HTTPException(422, f"эффорт? {effort}")
    if permission_mode and permission_mode not in REZHIMY:
        raise HTTPException(422, f"режим? {permission_mode}")


@app.get("/chats")
def chats(archived: int = 0, _: int = Depends(require_auth)):
    spisok = journal.spisok_chatov(archived=bool(archived))
    zhivye = {zh.chat_id: zh.status for zh in sessions.aktivnye()}
    for ch in spisok:
        ch["zhivoy"] = ch["id"] in zhivye
    return {"chats": spisok}


@app.post("/chats")
def create_chat(body: ChatIn, _: int = Depends(require_auth)):
    _proverit_polya(body.model, body.effort, body.permission_mode)
    if not os.path.isdir(body.project):
        raise HTTPException(422, f"папки нет: {body.project}")
    return journal.sozdat_chat(project=body.project, title=body.title, model=body.model,
                               effort=body.effort, permission_mode=body.permission_mode,
                               claude_session_id=body.claude_session_id)


def _chat_ili_404(chat_id: int) -> dict:
    chat = journal.poluchit_chat(chat_id)
    if not chat:
        raise HTTPException(404, "чата нет")
    return chat


@app.get("/chats/{chat_id}")
async def get_chat(chat_id: int, tail: int = 300, _: int = Depends(require_auth)):
    chat = _chat_ili_404(chat_id)
    zh = sessions.zhivoy(chat_id)
    asyncio.create_task(zh.progret())      # пока читаешь ленту, процесс уже поднимается
    return {"chat": chat, "events": journal.khvost(chat_id, tail), "snapshot": zh.snimok()}


@app.patch("/chats/{chat_id}")
def patch_chat(chat_id: int, body: ChatPatch, _: int = Depends(require_auth)):
    _chat_ili_404(chat_id)
    _proverit_polya(body.model, body.effort, body.permission_mode)
    polya = {k: v for k, v in body.model_dump().items() if v is not None}
    return journal.obnovit_chat(chat_id, **polya)


@app.delete("/chats/{chat_id}")
async def delete_chat(chat_id: int, _: int = Depends(require_auth)):
    _chat_ili_404(chat_id)
    await sessions.zabyt(chat_id)
    journal.udalit_chat(chat_id)
    return {"ok": True}


@app.post("/chats/{chat_id}/fork")
def fork_chat(chat_id: int, _: int = Depends(require_auth)):
    """Ветка от текущего состояния: старый чат остаётся нетронутым."""
    chat = _chat_ili_404(chat_id)
    novyy = journal.sozdat_chat(
        project=chat["project"], title=f"{chat['title'] or 'Чат'} (ветка)",
        model=chat["model"], effort=chat["effort"], permission_mode=chat["permission_mode"],
        claude_session_id=chat["claude_session_id"])
    journal.obnovit_chat(novyy["id"], fork_pending=1)
    journal.dobavit_sobytie(novyy["id"], "system",
                             {"text": f"Ветка от чата «{chat['title'] or chat_id}»"})
    # Вопросы, разрешения и очередь копировать нельзя: в новой сессии таких pid и qid
    # нет, карточки в ленте остались бы живыми на вид и мёртвыми на деле.
    ZHIVYE = {"question", "question_answer", "permission", "permission_answer",
             "queued", "queue_cancel"}
    for sobytie in journal.khvost(chat_id, 60):
        if sobytie["type"] in ZHIVYE:
            continue
        tip = sobytie.pop("type")
        sobytie.pop("seq", None)
        sobytie.pop("ts", None)
        journal.dobavit_sobytie(novyy["id"], tip, sobytie)
    return journal.poluchit_chat(novyy["id"])


@app.get("/chats/{chat_id}/events")
def get_events(chat_id: int, after: int = 0, _: int = Depends(require_auth)):
    _chat_ili_404(chat_id)
    return {"events": journal.sobytiya(chat_id, after)}


@app.post("/chats/{chat_id}/message")
async def send_message(chat_id: int, body: MessageIn, _: int = Depends(require_auth)):
    chat = _chat_ili_404(chat_id)
    tekst = body.text.strip()
    vlozheniya = [v.model_dump() for v in body.attachments]
    for v in vlozheniya:
        if not os.path.isfile(v["path"]):
            raise HTTPException(422, f"вложение потерялось: {v['path']}")
        # Дополняем на сервере: клиент шлёт только путь, а ленте нужны имя, вес и
        # признак картинки — иначе при перечитывании истории вместо фото будет плашка.
        v["name"] = v.get("name") or os.path.basename(v["path"])
        v["mime"] = v.get("mime") or files_mod.tip(v["path"])
        v["size"] = os.path.getsize(v["path"])
        v["kartinka"] = files_mod.eto_kartinka(v["path"])
    if not tekst and not vlozheniya:
        raise HTTPException(422, "пустое сообщение")
    if not chat["title"]:
        imena = ", ".join(v["name"] for v in vlozheniya)
        journal.obnovit_chat(chat_id, title=(tekst or imena)[:40])
    try:
        itog = await sessions.zhivoy(chat_id).otpravit(tekst, vlozheniya)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, **itog}


@app.delete("/chats/{chat_id}/queue/{qid}")
def cancel_queued(chat_id: int, qid: str, _: int = Depends(require_auth)):
    _chat_ili_404(chat_id)
    if not sessions.zhivoy(chat_id).otmenit_iz_ocheredi(qid):
        raise HTTPException(404, "в очереди такого нет")
    return {"ok": True}


@app.post("/chats/{chat_id}/interrupt")
async def interrupt(chat_id: int, _: int = Depends(require_auth)):
    _chat_ili_404(chat_id)
    await sessions.zhivoy(chat_id).prervat()
    return {"ok": True}


# ---------- файлы ----------
@app.get("/files")
def files(path: str, _: int = Depends(require_auth)):
    try:
        return files_mod.obzor(path)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/file")
def file_content(path: str, _: int = Depends(require_auth)):
    try:
        return files_mod.prochitat(path)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/file/download")
def file_download(path: str, _: int = Depends(require_auth)):
    if not os.path.isfile(path):
        raise HTTPException(404, "файла нет")
    return FileResponse(path, filename=os.path.basename(path), media_type=files_mod.tip(path))


@app.get("/skills")
def skills(_: int = Depends(require_auth)):
    return {"skills": files_mod.skilly()}


@app.get("/file/search")
def file_search(root: str, q: str = "", _: int = Depends(require_auth)):
    return {"files": files_mod.poisk(root, q)}


class PathIn(BaseModel):
    path: str


@app.post("/file/to-telegram")
def file_to_telegram(body: PathIn, _: int = Depends(require_auth)):
    try:
        return {"ok": True, "name": files_mod.otpravit_v_telegram(body.path)}
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/upload")
async def upload(file: UploadFile = File(...), chat_id: int | None = Form(None),
                 _: int = Depends(require_auth)):
    soderzhimoe = await file.read()
    put = files_mod.prinyat(file.filename or "файл", soderzhimoe, chat_id)
    return {"path": put, "name": os.path.basename(put), "size": len(soderzhimoe),
            "mime": files_mod.tip(put), "kartinka": files_mod.eto_kartinka(put)}


# ---------- разрешения ----------
class QuestionIn(BaseModel):
    answers: dict[str, str] | None = None    # из приложения: вопрос -> выбранное
    choice: int | None = None                # из Telegram: номер варианта первого вопроса


@app.post("/questions/{pid}/answer")
def answer_question(pid: str, body: QuestionIn, _: int = Depends(require_auth)):
    for zh in sessions.vse():
        if zh.otvetit_na_vopros(pid, body.answers, body.choice):
            return {"ok": True}
    raise HTTPException(404, "вопрос уже закрыт или ответ пустой")


class PermissionIn(BaseModel):
    answer: str          # allow | deny | always


@app.post("/permissions/{pid}/answer")
def answer_permission(pid: str, body: PermissionIn, _: int = Depends(require_auth)):
    if body.answer not in ("allow", "deny", "always"):
        raise HTTPException(422, "answer? allow|deny|always")
    for zh in sessions.vse():
        if zh.otvetit_na_razreshenie(pid, body.answer):
            return {"ok": True, "chat_id": zh.chat_id}
    raise HTTPException(404, "запрос уже закрыт или устарел")


@app.get("/permissions")
def pending_permissions(_: int = Depends(require_auth)):
    zhdut = []
    for zh in sessions.vse():
        for pid in zh.ozhidaniya:
            zhdut.append({"pid": pid, "chat_id": zh.chat_id})
    return {"pending": zhdut}


# ---------- живой поток ----------
def _stroka(sobytie: dict) -> str:
    return json.dumps(sobytie, ensure_ascii=False, default=str) + "\n"


@app.get("/chats/{chat_id}/stream")
async def stream(chat_id: int, after: int = 0, token: str = Query(None),
                 authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        _proverit_token(authorization[7:])
    elif token:
        _proverit_token(token)
    else:
        raise HTTPException(401, "no token")
    _chat_ili_404(chat_id)

    async def generator():
        zh = sessions.zhivoy(chat_id)
        ochered: asyncio.Queue = asyncio.Queue(maxsize=sessions.PREDEL_OCHEREDI)
        zh.podpischiki.add(ochered)
        try:
            maks = after
            for sobytie in journal.sobytiya(chat_id, after):
                maks = sobytie["seq"]
                yield _stroka(sobytie)
            yield _stroka(zh.snimok())
            while True:
                try:
                    sobytie = await asyncio.wait_for(ochered.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _stroka({"type": "ping", "ts": time.time()})
                    continue
                seq = sobytie.get("seq")
                if seq is not None:
                    if seq <= maks:
                        continue
                    maks = seq
                yield _stroka(sobytie)
        finally:
            zh.podpischiki.discard(ochered)
            zh.aktivnost = time.time()

    return StreamingResponse(generator(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})
