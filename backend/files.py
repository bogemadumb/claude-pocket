"""Файлы проекта: обзор папки, чтение, приём с телефона, отдача в Telegram.

Ограничений по путям нет намеренно: агент и так работает на этом сервере под sudo,
а доступ к API есть только у владельца. Защищаемся от другого — от бинарников и
гигантских файлов, которые незачем тащить в телефон.
"""
import base64
import json
import mimetypes
import os
import subprocess
import time


import config

# По пути, а не через sys.path: рядом лежит свой tg.py, и `import tg`
# вернул бы его вместо общего модуля.
import telegram_api

SKRYTYE = {".git", "__pycache__", "node_modules", "venv", ".venv", ".DS_Store"}
PREDEL_CHTENIYA = 400_000        # символов
PREDEL_OTPRAVKI = 49 * 1024 * 1024

# Какие картинки модель понимает и до какого размера их вообще имеет смысл слать.
KARTINKI = {"image/jpeg", "image/png", "image/gif", "image/webp"}
PREDEL_KARTINKI = 3_500_000    # байт: выше — API откажет, отдадим путём


def obzor(put: str) -> dict:
    put = os.path.abspath(os.path.expanduser(put))
    if not os.path.isdir(put):
        raise ValueError(f"это не папка: {put}")
    papki, fayly = [], []
    with os.scandir(put) as eto:
        for z in eto:
            if z.name in SKRYTYE or z.name.startswith(".") and z.is_dir():
                continue
            try:
                info = z.stat()
            except OSError:
                continue
            zapis = {"name": z.name, "path": os.path.join(put, z.name),
                      "size": info.st_size, "mtime": info.st_mtime}
            (papki if z.is_dir() else fayly).append(zapis)
    papki.sort(key=lambda z: z["name"].lower())
    fayly.sort(key=lambda z: -z["mtime"])
    roditel = os.path.dirname(put.rstrip("/")) or "/"
    return {"path": put, "parent": roditel, "dirs": papki, "files": fayly}


def prochitat(put: str) -> dict:
    put = os.path.abspath(os.path.expanduser(put))
    if not os.path.isfile(put):
        raise ValueError(f"файла нет: {put}")
    razmer = os.path.getsize(put)
    s_diska = open(put, "rb").read(PREDEL_CHTENIYA + 1)
    if b"\0" in s_diska[:8000]:
        return {"path": put, "size": razmer, "binary": True, "text": ""}
    tekst = s_diska.decode("utf-8", errors="replace")
    obrezan = len(tekst) > PREDEL_CHTENIYA
    return {"path": put, "size": razmer, "binary": False,
            "truncated": obrezan, "text": tekst[:PREDEL_CHTENIYA]}


def poisk(koren: str, zapros: str, predel: int = 40) -> list[str]:
    """Для @-подсказок: быстрый поиск по именам файлов внутри проекта."""
    koren = os.path.abspath(koren)
    zapros = (zapros or "").lower()
    naydeno = []
    for katalog, papki, fayly in os.walk(koren):
        papki[:] = [p for p in papki if p not in SKRYTYE and not p.startswith(".")]
        for f in fayly:
            if zapros in f.lower():
                naydeno.append(os.path.join(katalog, f))
                if len(naydeno) >= predel:
                    return naydeno
    return naydeno


def prinyat(imya: str, soderzhimoe: bytes, chat_id: int | None = None) -> str:
    """Вложение с телефона: своя папка на чат. В общую с Маком кладём только итоги,
    иначе туда синхронизируется каждый присланный скриншот."""
    bezopasnoe = os.path.basename(imya).replace("/", "_") or f"файл-{int(time.time())}"
    papka = os.path.join(config.UPLOAD_DIR, f"чат-{chat_id}" if chat_id else "no-chat")
    os.makedirs(papka, exist_ok=True)
    tsel = os.path.join(papka, bezopasnoe)
    osnova, rasshirenie = os.path.splitext(tsel)
    schyot = 1
    while os.path.exists(tsel):
        tsel = f"{osnova}-{schyot}{rasshirenie}"
        schyot += 1
    with open(tsel, "wb") as f:
        f.write(soderzhimoe)
    return tsel


def otpravit_v_telegram(put: str) -> str:
    put = os.path.abspath(put)
    if not os.path.isfile(put):
        raise ValueError("файла нет")
    if os.path.getsize(put) > PREDEL_OTPRAVKI:
        raise ValueError("больше 49 МБ — Telegram не примет")
    # Общий модуль собирает multipart сам, поэтому старая ловушка curl
    # (запятая в имени файла как разделитель списка) больше не воспроизводится.
    with open(put, "rb") as f:
        soderzhimoe = f.read()
    try:
        otvet = telegram_api.poslat_dokument(
            config.BOT_TOKEN, config.OWNER_ID, os.path.basename(put),
            soderzhimoe, taymaut=300)
    except Exception as e:
        raise ValueError(f"Telegram отказал: {str(e)[:200]}")
    if not otvet.get("ok"):
        raise ValueError(f"Telegram отказал: {str(otvet)[:200]}")
    return os.path.basename(put)


def tip(put: str) -> str:
    return mimetypes.guess_type(put)[0] or "application/octet-stream"


def eto_kartinka(put: str) -> bool:
    return tip(put) in KARTINKI


def blok_kartinki(put: str) -> dict | None:
    """Картинку кладём прямо в сообщение: путь модель «увидеть» не может, картинку — может.
    Не картинка, слишком тяжёлая или пропала с диска — вернём None, уйдёт путём."""
    try:
        if not eto_kartinka(put) or os.path.getsize(put) > PREDEL_KARTINKI:
            return None
        dannye = base64.b64encode(open(put, "rb").read()).decode()
    except OSError:
        return None
    return {"type": "image",
            "source": {"type": "base64", "media_type": tip(put), "data": dannye}}


def skilly() -> list[dict]:
    """Что умеет агент помимо базовых инструментов — читаем фронтматтер SKILL.md."""
    koren = os.path.expanduser("~/.claude/skills")
    itog = []
    if not os.path.isdir(koren):
        return itog
    for imya in sorted(os.listdir(koren)):
        fayl = os.path.join(koren, imya, "SKILL.md")
        opisanie = ""
        if os.path.isfile(fayl):
            head = open(fayl, encoding="utf-8", errors="replace").read(2000)
            for stroka in head.splitlines():
                if stroka.startswith("description:"):
                    opisanie = stroka.split(":", 1)[1].strip().strip('"')
                    break
        elif not os.path.isdir(os.path.join(koren, imya)):
            continue
        itog.append({"name": imya, "description": opisanie[:300]})
    return itog
