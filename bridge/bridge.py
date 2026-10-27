#!/usr/bin/env python3
"""Бот-мост Mini App.

Бот сам claude не запускает. Его работа:
  • кнопки «Разрешить/Запретить» из пушей приложения — ответ уходит в API;
  • быстрый ввод: текст или файл, присланный боту, попадает задачей в выбранный чат;
  • вход в приложение (кнопка меню в личке и ссылка в ответах).
"""
import json
import os
import time
import urllib.parse
import urllib.request

DATA = os.environ.get("POCKET_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "data"))


def _secret(name):
    value = os.environ.get(name, "")
    path = os.environ.get(f"{name}_FILE")
    if not value and path and os.path.exists(path):
        value = open(path).read().strip()
    return value


BOT = _secret("BOT_TOKEN")
OWNER = int(os.environ.get("OWNER_ID", "0"))
GROUP = int(os.environ.get("GROUP_ID", "0") or 0)
API = f"https://api.telegram.org/bot{BOT}"

APP = os.environ.get("POCKET_API", "http://127.0.0.1:8030")
APP_URL = os.environ.get("APP_URL", "https://example.com/code/")
SERVICE_TOKEN = open(os.path.join(DATA, "service-token")).read().strip()
SHARED = os.environ.get("BRIDGE_SHARED_DIR", os.path.join(DATA, "shared"))
SOSTOYANIE = os.path.join(DATA, "bridge-state.json")   # какой чат считается активным


# ---------- Telegram ----------
def tg(metod, **parametry):
    try:
        chistye = {k: v for k, v in parametry.items() if v is not None}
        dannye = urllib.parse.urlencode(chistye).encode()
        with urllib.request.urlopen(f"{API}/{metod}", data=dannye, timeout=75) as r:
            return json.load(r)
    except Exception as e:
        print("tg err:", e, flush=True)
        return {}


def knopka_prilozheniya(chat_id=None):
    if chat_id == OWNER:
        return [{"text": "Открыть Claude", "web_app": {"url": APP_URL}}]
    return [{"text": "Открыть Claude", "url": APP_URL}]


def otvetit(chat_id, tekst, thread=None, knopki=None):
    tg("sendMessage", chat_id=chat_id, message_thread_id=thread, text=tekst,
       disable_web_page_preview="true",
       reply_markup=json.dumps({"inline_keyboard": knopki}) if knopki else None)


# ---------- API приложения ----------
def prilozhenie(put, metod="GET", telo=None):
    zapros = urllib.request.Request(
        APP + put, method=metod,
        data=json.dumps(telo).encode() if telo is not None else None,
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(zapros, timeout=30) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        syroe = e.read().decode(errors="replace")
        try:
            prichina = json.loads(syroe).get("detail", syroe)
        except Exception:
            prichina = syroe
        return None, f"{e.code}: {prichina}"
    except Exception as e:
        return None, str(e)


def aktivnyy_chat() -> int | None:
    try:
        return json.load(open(SOSTOYANIE)).get("chat_id")
    except Exception:
        return None


def zapomnit_chat(chat_id: int):
    json.dump({"chat_id": chat_id}, open(SOSTOYANIE, "w"))


def vybrat_chat_knopkami(kuda, thread=None):
    dannye, oshibka = prilozhenie("/chats")
    if oshibka:
        otvetit(kuda, f"Приложение недоступно: {oshibka}", thread)
        return
    chaty = (dannye or {}).get("chats", [])[:8]
    if not chaty:
        otvetit(kuda, "Чатов ещё нет — создай первый в приложении.", thread,
                 [knopka_prilozheniya(kuda)])
        return
    knopki = [[{"text": (ch["title"] or f"чат {ch['id']}")[:40],
                "callback_data": f"c:{ch['id']}"}] for ch in chaty]
    otvetit(kuda, "Куда отправлять задачи из Telegram?", thread, knopki)


# ---------- обработка ----------
def handle_button(cq):
    dannye = cq.get("data", "")
    soobshchenie = cq.get("message") or {}
    kuda = (soobshchenie.get("chat") or {}).get("id", OWNER)
    if (cq.get("from") or {}).get("id") != OWNER:
        tg("answerCallbackQuery", callback_query_id=cq["id"], text="Не твой бот")
        return

    if dannye.startswith("p:"):
        _, pid, otvet = dannye.split(":", 2)
        itog, oshibka = prilozhenie(f"/permissions/{pid}/answer", "POST", {"answer": otvet})
        podpis = {"allow": "Разрешено", "always": "Разрешено навсегда", "deny": "Запрещено"}
        tekst = podpis.get(otvet, otvet) if ne_oshibka(itog) else f"Не вышло — {oshibka}"
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=tekst)
        if soobshchenie.get("message_id"):
            tg("editMessageReplyMarkup", chat_id=kuda, message_id=soobshchenie["message_id"],
               reply_markup=json.dumps({"inline_keyboard": [knopka_prilozheniya(kuda)]}))
            tg("sendMessage", chat_id=kuda, text=f"{tekst} · {pid}",
               reply_to_message_id=soobshchenie["message_id"])
        return

    if dannye.startswith("c:"):
        chat_id = int(dannye[2:])
        zapomnit_chat(chat_id)
        tg("answerCallbackQuery", callback_query_id=cq["id"], text="Запомнил")
        otvetit(kuda, f"Задачи из Telegram пойдут в чат {chat_id}.")
        return

    tg("answerCallbackQuery", callback_query_id=cq["id"])


def ne_oshibka(itog):
    return bool(itog and itog.get("ok"))


def skachat_fayl(file_id, imya):
    r = tg("getFile", file_id=file_id)
    put = (r.get("result") or {}).get("file_path")
    if not put:
        return None
    bezopasnoe = os.path.basename(imya).replace("/", "_") or "файл"
    tsel = os.path.join(SHARED, bezopasnoe)
    try:
        urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{BOT}/{put}", tsel)
        return tsel
    except Exception as e:
        print("download err:", e, flush=True)
        return None


def handle_message(m):
    kuda = (m.get("chat") or {}).get("id")
    thread = m.get("message_thread_id") if m.get("is_topic_message") else None
    if (m.get("from") or {}).get("id") != OWNER or kuda not in (OWNER, GROUP):
        return
    tekst = (m.get("text") or m.get("caption") or "").strip()

    if tekst.lower() in ("/start", "/app", "/открыть", "открыть"):
        otvetit(kuda, "Claude переехал в приложение: полная лента, инструменты, разрешения, "
                       "файлы. Бот остался для уведомлений и быстрых задач.",
                 thread, [knopka_prilozheniya(kuda)])
        return
    if tekst.lower() in ("/чат", "/chat", "/куда"):
        vybrat_chat_knopkami(kuda, thread)
        return

    fayl = None
    if "document" in m:
        fayl = skachat_fayl(m["document"]["file_id"], m["document"].get("file_name", "файл"))
    elif "photo" in m:
        f = m["photo"][-1]
        fayl = skachat_fayl(f["file_id"], f"фото-{f['file_unique_id']}.jpg")

    if not tekst and not fayl:
        return

    chat_id = aktivnyy_chat()
    if not chat_id:
        vybrat_chat_knopkami(kuda, thread)
        return

    zadacha = tekst or ""
    if fayl:
        zadacha = (f"{zadacha}\n\n(Присланный файл: {fayl})" if zadacha
                  else f"Пользователь прислал файл {fayl}. Посмотри, что это, и скажи, "
                       f"что с ним можно сделать.")
    itog, oshibka = prilozhenie(f"/chats/{chat_id}/message", "POST", {"text": zadacha})
    if oshibka:
        otvetit(kuda, f"Не отправилось: {oshibka}", thread, [knopka_prilozheniya(kuda)])
    else:
        otvetit(kuda, "Задача принята — ход работы видно в приложении, "
                       "итог придёт сюда, если приложение закрыто.",
                 thread, [knopka_prilozheniya(kuda)])


def main():
    offset = None
    for u in tg("getUpdates", timeout=0).get("result", []):
        offset = u["update_id"] + 1          # старый бэклог не выполняем
    print("мост запущен", flush=True)
    while True:
        obnovleniya = tg("getUpdates", offset=offset, timeout=60)
        for u in obnovleniya.get("result", []):
            offset = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    handle_button(u["callback_query"])
                elif "message" in u:
                    handle_message(u["message"])
            except Exception as e:
                print("ошибка обработки:", e, flush=True)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
