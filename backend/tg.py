"""Пуши в Telegram: запросы разрешений с кнопками и уведомления о завершении.

Приложение шлёт сообщения само (тем же ботом), а бот-мост только принимает нажатия
кнопок и возвращает ответ в API — так не нужно держать состояние в двух местах.
"""
import json
import logging
import threading

import config

import telegram_api

log = logging.getLogger("claude-app.tg")

# Куда доставлять пуши: сначала личка, при отказе — форум-группа бота.
_kuda: int | None = None


def _vyzov(metod: str, **parametry) -> dict:
    try:
        return telegram_api.vyzov(config.BOT_TOKEN, metod, **parametry)
    except Exception as e:
        log.warning("telegram %s: %s", metod, e)
        return {}


def _adresat() -> int:
    global _kuda
    if _kuda is None:
        proba = _vyzov("sendChatAction", chat_id=config.OWNER_ID, action="typing")
        _kuda = config.OWNER_ID if proba.get("ok") else (config.GROUP_ID or config.OWNER_ID)
        log.info("пуши идут в чат %s", _kuda)
    return _kuda


def _otpravit(tekst: str, knopki: list[list[dict]] | None = None):
    """В фоне: сеть не должна тормозить поток событий."""
    def zadacha():
        _vyzov("sendMessage", chat_id=_adresat(), text=tekst,
               disable_web_page_preview="true",
               reply_markup=json.dumps({"inline_keyboard": knopki}) if knopki else None)
    threading.Thread(target=zadacha, daemon=True).start()


def knopka_prilozheniya() -> list[dict]:
    """web_app-кнопка живёт только в личке; в группе даём обычную ссылку."""
    if _adresat() == config.OWNER_ID:
        return [{"text": "Открыть Claude", "web_app": {"url": config.APP_URL}}]
    return [{"text": "Открыть Claude", "url": config.APP_URL}]


def sprosit_razreshenie(chat_id: int, pid: str, chat_imya: str, instrument: str, kratko: str):
    tekst = (f"Нужно разрешение · {chat_imya}\n\n{instrument}\n{kratko[:600]}")
    knopki = [
        [{"text": "Разрешить", "callback_data": f"p:{pid}:allow"},
         {"text": "Запретить", "callback_data": f"p:{pid}:deny"}],
        [{"text": f"Всегда разрешать {instrument}", "callback_data": f"p:{pid}:always"}],
        knopka_prilozheniya(),
    ]
    _otpravit(tekst, knopki)


def sprosit_vybor(pid: str, chat_imya: str, voprosy: list[dict]):
    """Вопрос с вариантами. В кнопку кладём только номер варианта: в callback_data
    всего 64 байта, а метки бывают длинные."""
    pervyy = voprosy[0]
    # Кнопки даём только когда вопрос один: ответ закрывает весь AskUserQuestion целиком,
    # и остальные вопросы остались бы без ответа, хотя пуш обещал бы обратное.
    if len(voprosy) > 1:
        perechen = "\n".join(f"- {v.get('question', '')}" for v in voprosy)
        _otpravit(f"Вопросы ({len(voprosy)}) · {chat_imya}\n\n{perechen}\n\n"
                   f"Ответить можно в приложении.", [knopka_prilozheniya()])
        return
    varianty = (pervyy.get("options") or [])[:8]
    knopki = [[{"text": (v.get("label") or "?")[:60], "callback_data": f"q:{pid}:{i}"}]
              for i, v in enumerate(varianty)]
    knopki.append(knopka_prilozheniya())
    _otpravit(f"Вопрос · {chat_imya}\n\n{pervyy.get('question', '')}", knopki)


def uvedomit_o_gotovnosti(chat_imya: str, itog: str, metrika: str = ""):
    tekst = f"Готово · {chat_imya}\n{metrika}\n\n{itog[:2500]}".strip()
    _otpravit(tekst, [knopka_prilozheniya()])


def uvedomit_ob_oshibke(chat_imya: str, tekst_oshibki: str):
    _otpravit(f"Сбой · {chat_imya}\n\n{tekst_oshibki[:1000]}", [knopka_prilozheniya()])
