"""Живые сессии Claude: один ClaudeSDKClient на чат, поток событий в журнал и подписчикам.

Прогон живёт в фоне и не зависит от того, открыто ли приложение: клиент может уйти,
задача продолжится, а вернувшийся клиент дочитает журнал с нужного места.
"""
import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
import uuid

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                              PermissionResultAllow, PermissionResultDeny, ResultMessage,
                              StreamEvent, SystemMessage, TextBlock, ThinkingBlock,
                              ToolResultBlock, ToolUseBlock, UserMessage)

import config
import files as files_mod
import journal
import tg

try:
    import llm_slot
except ImportError:
    llm_slot = None

log = logging.getLogger("claude-app.sessions")


async def slot_zhurnal(model):
    # интерактив не ограничиваем: слот только пишет вызов в журнал, отказ и сбой хаба игнорируются
    if not llm_slot:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(llm_slot.sprosit, "claude-app", "interaktiv", model or None), 8)
    except Exception:
        return None


async def slot_zhurnal_konec(slot, oshibka):
    if not slot:
        return
    try:
        await asyncio.wait_for(asyncio.to_thread(
            llm_slot.konec, slot, "oshibka" if oshibka else "ok", oshibka), 8)
    except Exception:
        pass

# Ultracode: CLI не принимает его как значение --effort, воспроизводим по определению
ULTRACODE = (
    "Ultracode is on: optimize for the most exhaustive, correct answer — not the fastest "
    "or cheapest. Use the Workflow tool on every substantive task; token cost is not a "
    "constraint. For multi-phase work (understand → design → implement → review), that often "
    "means several workflows in sequence — one per phase — so you stay in the loop between "
    "them. Lean toward orchestrating with workflows and adversarially verifying your findings, "
    "unless the work is trivial or already verified. Solo only on conversational turns or "
    "trivial mechanical edits. Note: this machine has 2 CPU cores, so workflow agents run "
    "nearly one at a time — prefer a few well-scoped agents over wide fan-out."
)

PREDEL_TEKSTA = 6000        # столько символов инструментального ввода/вывода храним
PREDEL_OCHEREDI = 400
PREDEL_KARTINOK = 8        # столько картинок за раз вкладываем в сообщение
PREDEL_VSEKH_KARTINOK = 12_000_000   # и не больше стольких байт base64 суммарно

# Агент отдаёт файл строкой «SEND_FILE: /путь». Показываем её карточкой в ленте,
# а не сырым текстом, как было после переезда со старого бота.
OTDACHA_FAYLA = re.compile(r"(?m)^[-*\s]*SEND_FILE:\s*(.+?)\s*$")


def vynut_fayly(tekst: str, koren: str = "") -> tuple[str, list[dict]]:
    """Возвращает текст без служебных строк и описания найденных файлов.
    Относительный путь считаем от папки проекта чата, а не от папки службы."""
    puti = [p.strip("'\"`") for p in OTDACHA_FAYLA.findall(tekst or "")]
    if not puti:
        return tekst, []
    chistyy = OTDACHA_FAYLA.sub("", tekst).strip()
    fayly, poteryannye = [], []
    for put in puti:
        razvyornutyy = os.path.expanduser(put)
        polnyy = (razvyornutyy if os.path.isabs(razvyornutyy)
                  else os.path.abspath(os.path.join(koren or os.getcwd(), razvyornutyy)))
        if not os.path.isfile(polnyy):
            poteryannye.append(put)
            continue
        fayly.append({"path": polnyy, "name": os.path.basename(polnyy),
                      "size": os.path.getsize(polnyy), "mime": files_mod.tip(polnyy),
                      "kartinka": files_mod.eto_kartinka(polnyy)})
    if poteryannye:      # молча терять файл нельзя — пусть будет видно, что он не нашёлся
        chistyy = (chistyy + "\n\n(файл не найден: " + ", ".join(poteryannye) + ")").strip()
    return chistyy, fayly


def obrezat(znachenie, predel: int = PREDEL_TEKSTA):
    """Инструменты умеют возвращать мегабайты — в журнал кладём хвост разумного размера."""
    if isinstance(znachenie, str):
        return znachenie if len(znachenie) <= predel else znachenie[:predel] + f"\n…(обрезано, всего {len(znachenie)} симв.)"
    if isinstance(znachenie, (dict, list)):
        syroe = json.dumps(znachenie, ensure_ascii=False)
        if len(syroe) <= predel:
            return znachenie
        return {"_obrezano": syroe[:predel] + f"…(всего {len(syroe)} симв.)"}
    return znachenie


class ZhivoyChat:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.client: ClaudeSDKClient | None = None
        self.zadacha: asyncio.Task | None = None
        self.podpischiki: set[asyncio.Queue] = set()
        self.bufer = ""              # незавершённый текст текущего ответа
        self.bufer_razmyshleniya = ""
        self.posledniy_itog = ""
        self.status = "idle"         # idle | running | waiting | error
        self.aktivnost = time.time()
        self.metrika = {}            # токены/время текущего прогона
        self.fayly_progona: list[dict] = []   # что агент отдал через SEND_FILE
        self.ozhidaniya: dict[str, asyncio.Future] = {}   # запросы разрешений без ответа
        self.voprosy: dict[str, list] = {}   # заданные AskUserQuestion, ждут ответа
        self.ochered: list[dict] = []        # задачи, набранные пока чат был занят
        self.avto: set[str] = set()  # инструменты, одобренные «всегда» в этом чате
        self._lok = asyncio.Lock()
        self._parametry: tuple | None = None   # с какими настройками открыт клиент

    # ---------- служебное ----------
    def _zapisat(self, tip: str, dannye: dict):
        sobytie = journal.dobavit_sobytie(self.chat_id, tip, dannye)
        self._razoslat(sobytie)
        return sobytie

    def _razoslat(self, sobytie: dict):
        for q in list(self.podpischiki):
            try:
                q.put_nowait(sobytie)
            except asyncio.QueueFull:
                pass

    def _status(self, status: str):
        self.status = status
        journal.obnovit_chat(self.chat_id, status=status)
        self._razoslat({"type": "status", "status": status, "metrika": self.metrika})

    # ---------- клиент ----------
    def _sobrat_optsii(self, chat: dict) -> tuple[ClaudeAgentOptions, tuple]:
        effort = chat["effort"]
        ultra = effort == "ultracode"
        sistemnyy = {"type": "preset", "preset": "claude_code"}
        if ultra:
            sistemnyy["append"] = ULTRACODE
        optsii = ClaudeAgentOptions(
            cwd=chat["project"],
            env={"CLAUDE_CODE_OAUTH_TOKEN": config.oauth_token()},
            model=chat["model"],
            effort="xhigh" if ultra else effort,
            permission_mode=chat["permission_mode"],
            system_prompt=sistemnyy,
            include_partial_messages=True,
            setting_sources=["user", "project", "local"],
            skills="all",
            resume=chat.get("claude_session_id") or None,
            fork_session=bool(chat.get("fork_pending")),
            enable_file_checkpointing=True,
            can_use_tool=self._sprosit_razreshenie,
        )
        otpechatok = (chat["project"], chat["model"], chat["effort"], chat["permission_mode"],
                     chat.get("claude_session_id"), bool(chat.get("fork_pending")))
        return optsii, otpechatok

    async def _otkryt(self):
        chat = journal.poluchit_chat(self.chat_id)
        optsii, otpechatok = self._sobrat_optsii(chat)
        self.client = ClaudeSDKClient(options=optsii)
        await self.client.connect()
        self._parametry = otpechatok
        log.info("чат %s: клиент открыт (%s)", self.chat_id, otpechatok)

    async def _pereotkryt_esli_nado(self):
        """Смена папки/модели/эффорта/режима требует нового процесса — сессия продолжится по resume."""
        chat = journal.poluchit_chat(self.chat_id)
        _, otpechatok = self._sobrat_optsii(chat)
        if self.client is not None and otpechatok != self._parametry:
            await self.zakryt()
        if self.client is None:
            await self._otkryt()

    async def zakryt(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception as e:
                log.warning("чат %s: закрытие клиента: %s", self.chat_id, e)
            self.client = None
            self._parametry = None

    # ---------- разрешения ----------
    async def _sprosit_razreshenie(self, instrument: str, vkhod: dict, kontekst):
        """Вызывается SDK, когда режим требует подтверждения. Ждём ответа из приложения
        или из Telegram: что первое придёт, то и решает."""
        if instrument == "AskUserQuestion":
            return await self._sprosit_vybor(vkhod)
        if instrument in self.avto:
            return PermissionResultAllow()

        pid = uuid.uuid4().hex[:10]
        budushchee: asyncio.Future = asyncio.get_running_loop().create_future()
        self.ozhidaniya[pid] = budushchee
        kratko = json.dumps(vkhod, ensure_ascii=False)[:400] if not isinstance(vkhod, str) else vkhod
        self._zapisat("permission", {"pid": pid, "tool": instrument,
                                      "input": obrezat(vkhod), "kratko": kratko})
        prezhniy = self.status
        self._status("waiting")
        chat = journal.poluchit_chat(self.chat_id) or {}
        tg.sprosit_razreshenie(self.chat_id, pid, chat.get("title") or f"чат {self.chat_id}",
                               instrument, kratko)
        try:
            otvet = await asyncio.wait_for(budushchee, timeout=config.PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            otvet = "timeout"
        finally:
            self.ozhidaniya.pop(pid, None)
            self._status(prezhniy if prezhniy != "waiting" else "running")

        self._zapisat("permission_answer", {"pid": pid, "answer": otvet})
        if otvet == "always":
            self.avto.add(instrument)
        if otvet in ("allow", "always"):
            return PermissionResultAllow()
        prichina = ("Не дождались ответа за 30 минут — считаем это отказом."
                   if otvet == "timeout" else "Владелец отклонил вызов.")
        return PermissionResultDeny(message=prichina)

    def otvetit_na_razreshenie(self, pid: str, otvet) -> bool:
        budushchee = self.ozhidaniya.get(pid)
        if budushchee is None or budushchee.done():
            return False

        def postavit():
            # Проверяем повторно уже в цикле событий: ответить могли одновременно
            # из приложения и кнопкой в Telegram, второй set_result упал бы.
            if not budushchee.done():
                budushchee.set_result(otvet)

        budushchee.get_loop().call_soon_threadsafe(postavit)
        return True

    # ---------- вопросы с вариантами ----------
    async def _sprosit_vybor(self, vkhod: dict):
        """AskUserQuestion — это не «разрешить или запретить», а вопрос с вариантами.
        Показываем его карточкой, а выбранное возвращаем подменой входа: дальше CLI сам
        оформит это результатом инструмента, и модель увидит ответ."""
        voprosy = [v for v in (vkhod.get("questions") or []) if (v or {}).get("question")]
        if not voprosy:
            return PermissionResultAllow()

        pid = uuid.uuid4().hex[:10]
        budushchee: asyncio.Future = asyncio.get_running_loop().create_future()
        self.ozhidaniya[pid] = budushchee
        self.voprosy[pid] = voprosy
        self._zapisat("question", {"pid": pid, "questions": voprosy})
        prezhniy = self.status
        self._status("waiting")
        chat = journal.poluchit_chat(self.chat_id) or {}
        tg.sprosit_vybor(pid, chat.get("title") or f"чат {self.chat_id}", voprosy)
        try:
            otvety = await asyncio.wait_for(budushchee, timeout=config.PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            otvety = None
        finally:
            self.ozhidaniya.pop(pid, None)
            self.voprosy.pop(pid, None)
            self._status(prezhniy if prezhniy != "waiting" else "running")

        if not otvety:      # за владельца не выбираем: пусть модель знает, что ответа нет
            self._zapisat("question_answer", {"pid": pid, "answers": {}, "net_otveta": True})
            return PermissionResultDeny(
                message="Владелец не ответил на вопрос. Не выбирай за него: "
                        "либо действуй по разумному умолчанию и скажи об этом, либо спроси текстом.")
        self._zapisat("question_answer", {"pid": pid, "answers": otvety})
        return PermissionResultAllow(updated_input={**vkhod, "answers": otvety})

    def otvetit_na_vopros(self, pid: str, otvety: dict | None = None,
                           vybor: int | None = None) -> bool:
        """Из приложения приходит готовый словарь ответов, из Telegram — номер варианта
        (в кнопку больше 64 байт не влезает, поэтому метку разбираем здесь)."""
        voprosy = self.voprosy.get(pid)
        if voprosy is None:
            return False
        if vybor is not None:
            varianty = (voprosy[0].get("options") or [])
            if not 0 <= vybor < len(varianty):
                return False
            otvety = {voprosy[0]["question"]: varianty[vybor].get("label", "")}
        otvety = {k: v for k, v in (otvety or {}).items() if str(v).strip()}
        if not otvety:
            return False
        return self.otvetit_na_razreshenie(pid, otvety)

    # ---------- прогон ----------
    async def otpravit(self, tekst: str, vlozheniya: list[dict] | None = None) -> dict:
        """Отвечаем сразу: запуск процесса claude занимает секунды, ждать его в HTTP-запросе
        значит показывать пользователю пустой экран после нажатия «отправить».
        Чат занят — не отфутболиваем, а ставим задачу в очередь: мысль приходит тогда,
        когда приходит, и ждать конца прогона ради неё глупо."""
        vlozheniya = vlozheniya or []
        async with self._lok:
            if self.status in ("running", "waiting"):
                qid = uuid.uuid4().hex[:8]
                self.ochered.append({"qid": qid, "text": tekst, "attachments": vlozheniya})
                self._zapisat("queued", {"qid": qid, "text": tekst, "attachments": vlozheniya,
                                          "mesto": len(self.ochered)})
                return {"queued": True, "qid": qid}
            self._zapisat("user", {"text": tekst, "attachments": vlozheniya})
            self.metrika = {"nachalo": time.time(), "tokeny": 0}
            self._status("running")
            self.zadacha = asyncio.create_task(self._progon(tekst, vlozheniya))
            return {"queued": False}

    def otmenit_iz_ocheredi(self, qid: str) -> bool:
        do = len(self.ochered)
        self.ochered = [z for z in self.ochered if z["qid"] != qid]
        if len(self.ochered) == do:
            return False
        self._zapisat("queue_cancel", {"qid": qid})
        return True

    async def _vzyat_iz_ocheredi(self):
        """Прогон закончился — сразу берём следующую задачу, если она набрана."""
        async with self._lok:
            if not self.ochered or self.status in ("running", "waiting"):
                return
            zadacha = self.ochered.pop(0)
            # qid в событии — чтобы приложение убрало карточку «в очереди» и не задвоило её.
            self._zapisat("user", {"text": zadacha["text"],
                                    "attachments": zadacha["attachments"], "qid": zadacha["qid"]})
            self.metrika = {"nachalo": time.time(), "tokeny": 0}
            self._status("running")
            self.zadacha = asyncio.create_task(
                self._progon(zadacha["text"], zadacha["attachments"]))

    async def progret(self):
        """Открытие чата — повод поднять процесс заранее, пока пишется первая задача."""
        if self.client is not None or self.status == "running":
            return
        try:
            if len(aktivnye()) < config.MAX_ACTIVE_SESSIONS:
                await self._pereotkryt_esli_nado()
        except Exception as e:
            log.warning("чат %s: прогрев не удался: %s", self.chat_id, e)

    async def prervat(self):
        """«Стоп» останавливает всё: и текущий прогон, и то, что стоит за ним в очереди —
        так же ведёт себя Claude Code. Карточки в ленте помечаются отменёнными."""
        for zadacha in list(self.ochered):
            self.otmenit_iz_ocheredi(zadacha["qid"])
        # Прогон мог стоять на вопросе или запросе разрешения: пока ожидание не снято,
        # прерывать нечего — он так и висел бы до получасового таймаута.
        for pid in list(self.ozhidaniya):
            self.otvetit_na_razreshenie(pid, None if pid in self.voprosy else "deny")
        if self.client is not None and self.status in ("running", "waiting"):
            await self.client.interrupt()

    def _zapros(self, tekst: str, vlozheniya: list[dict]):
        """Без вложений шлём строкой — как раньше. С вложениями собираем сообщение из блоков:
        картинки уходят целиком (модель их видит), остальные файлы — путями."""
        if not vlozheniya:
            return tekst
        bloki, perechen, kartinok, ves = [], [], 0, 0
        for v in vlozheniya:
            put = (v or {}).get("path") or ""
            imya = (v or {}).get("name") or os.path.basename(put)
            vlezaet = kartinok < PREDEL_KARTINOK and ves < PREDEL_VSEKH_KARTINOK
            blok = files_mod.blok_kartinki(put) if vlezaet else None
            if blok:
                bloki.append(blok)
                kartinok += 1
                ves += len(blok["source"]["data"])
                perechen.append(f"- {imya} — картинка выше, файл лежит тут: {put}")
            elif files_mod.tip(put).startswith("image/"):
                # Картинка, которую нельзя показать: heic с айфона, слишком тяжёлая
                # или сверх лимита за сообщение. Честно предупреждаем, а не молчим.
                perechen.append(f"- {imya}: {put} (картинку показать не могу — "
                                f"формат или размер; посмотри файл инструментом)")
            else:
                perechen.append(f"- {imya}: {put}")
        podpis = "Вложения к сообщению:\n" + "\n".join(perechen)
        bloki.append({"type": "text", "text": (tekst + "\n\n" if tekst else "") + podpis})
        soobshchenie = {"type": "user", "message": {"role": "user", "content": bloki},
                     "parent_tool_use_id": None}

        async def potok():
            yield soobshchenie

        return potok()

    async def _progon(self, tekst: str, vlozheniya: list[dict] | None = None):
        self.fayly_progona = []
        self.bufer = ""
        self.bufer_razmyshleniya = ""
        slot_zap, slot_oshibka = None, None
        try:
            if self.client is None:
                self._razoslat({"type": "phase", "text": "Поднимаю сессию…"})
            await osvobodit_mesto(krome=self.chat_id)
            await self._pereotkryt_esli_nado()
            # Режим переустанавливаем перед каждым ходом: CLI сам сбрасывает план после
            # первого ответа, и второй запрос в «плановом» чате уже правил бы файлы.
            chat = journal.poluchit_chat(self.chat_id) or {}
            try:
                await self.client.set_permission_mode(chat.get("permission_mode", "default"))
            except Exception as e:
                log.warning("чат %s: режим не переустановился: %s", self.chat_id, e)
            slot_zap = await slot_zhurnal(chat.get("model"))
            self._razoslat({"type": "phase", "text": "Думаю…"})
            await self.client.query(self._zapros(tekst, vlozheniya or []))
            async for soobshchenie in self.client.receive_response():
                self._handle(soobshchenie)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("чат %s: прогон упал", self.chat_id)
            slot_oshibka = f"{type(e).__name__}: {e}"
            self._zapisat("error", {"text": f"{type(e).__name__}: {e}"})
            self._status("error")
            self._push_itoga(oshibka=f"{type(e).__name__}: {e}")
            # Очередь берём и после падения: сбой бывает разовым, а молча выбрасывать
            # уже набранные задачи хуже, чем показать вторую ошибку. Не нравится — «Стоп».
            await self._vzyat_iz_ocheredi()
            return
        finally:
            self.aktivnost = time.time()
            await slot_zhurnal_konec(slot_zap, slot_oshibka)
        self._status("idle")
        self._push_itoga()
        await self._vzyat_iz_ocheredi()

    def _otdat_fayly_v_telegram(self):
        """Приложение закрыто — файлы догоняют владельца документами в Telegram.
        Отдельным потоком: curl на десятки мегабайт не должен держать цикл событий."""
        fayly = list(self.fayly_progona)
        if not fayly:
            return

        def zadacha():
            for f in fayly:
                try:
                    files_mod.otpravit_v_telegram(f["path"])
                except Exception as e:
                    log.warning("чат %s: файл %s не ушёл: %s", self.chat_id, f["path"], e)
                    # Молчать нельзя: иначе «Готово» приходит без файла и без объяснений.
                    tg.uvedomit_ob_oshibke(f["name"], f"файл не ушёл в Telegram: {e}")

        threading.Thread(target=zadacha, daemon=True).start()

    def _push_itoga(self, oshibka: str | None = None):
        """Если приложение закрыто — итог уходит в Telegram, чтобы не сидеть и не ждать."""
        if self.podpischiki:
            return
        if not oshibka:
            self._otdat_fayly_v_telegram()
        chat = journal.poluchit_chat(self.chat_id) or {}
        imya = chat.get("title") or f"чат {self.chat_id}"
        if oshibka:
            tg.uvedomit_ob_oshibke(imya, oshibka)
            return
        sekundy = int(time.time() - (self.metrika.get("nachalo") or time.time()))
        tokeny = self.metrika.get("tokeny")
        metrika = f"{sekundy} с" + (f" · {tokeny} ток" if tokeny else "")
        tg.uvedomit_o_gotovnosti(imya, self.posledniy_itog or "(без текста)", metrika)

    def _handle(self, m):
        if isinstance(m, StreamEvent):
            self._potok(m)
        elif isinstance(m, AssistantMessage):
            self._otvet(m)
        elif isinstance(m, UserMessage):
            self._rezultaty_instrumentov(m)
        elif isinstance(m, ResultMessage):
            self._itog(m)
        elif isinstance(m, SystemMessage):
            dannye = getattr(m, "data", {}) or {}
            if dannye.get("subtype") == "compact_boundary":
                self._zapisat("system", {"text": "Контекст сжат (compact)"})

    def _potok(self, m: StreamEvent):
        ev = m.event or {}
        tip = ev.get("type")
        if tip == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "thinking_delta":
                self.bufer_razmyshleniya += delta.get("thinking", "")
                self._razoslat({"type": "delta", "kind": "thinking",
                                 "text": delta.get("thinking", "")})
            elif "text" in delta:
                self.bufer += delta["text"]
                self._razoslat({"type": "delta", "kind": "text", "text": delta["text"]})
        elif tip == "message_start":
            usage = ((ev.get("message") or {}).get("usage") or {})
            if usage:
                self.metrika["vkhod"] = usage.get("input_tokens", 0)

    def _otvet(self, m: AssistantMessage):
        sid = getattr(m, "session_id", None)
        if sid:
            self._zapomnit_sessiyu(sid)
        koren = (journal.poluchit_chat(self.chat_id) or {}).get("project") or ""
        for b in m.content:
            if isinstance(b, TextBlock):
                tekst, fayly = vynut_fayly(b.text, koren)
                self.fayly_progona.extend(fayly)
                if tekst.strip() or fayly:
                    self._zapisat("text", {"text": tekst, "files": fayly})
            elif isinstance(b, ThinkingBlock):
                if b.thinking.strip():
                    self._zapisat("thinking", {"text": b.thinking})
            elif isinstance(b, ToolUseBlock):
                dannye = {"id": b.id, "name": b.name, "input": obrezat(b.input)}
                if b.name == "TodoWrite":
                    dannye["todos"] = (b.input or {}).get("todos", [])
                self._zapisat("tool_use", dannye)
        usage = getattr(m, "usage", None) or {}
        if usage.get("output_tokens"):
            self.metrika["tokeny"] = usage["output_tokens"]
        self.bufer = ""
        self.bufer_razmyshleniya = ""

    def _rezultaty_instrumentov(self, m: UserMessage):
        soderzhimoe = m.content if isinstance(m.content, list) else []
        for b in soderzhimoe:
            if isinstance(b, ToolResultBlock):
                self._zapisat("tool_result", {"id": b.tool_use_id,
                                               "content": obrezat(b.content),
                                               "is_error": bool(b.is_error)})

    def _itog(self, m: ResultMessage):
        sid = getattr(m, "session_id", None)
        if sid:
            self._zapomnit_sessiyu(sid)
        self.posledniy_itog = vynut_fayly((getattr(m, "result", None) or "").strip())[0]
        usage = getattr(m, "usage", None) or {}
        # Размер контекста = свежий ввод плюс всё, что прочитано из кэша.
        kontekst = sum(int(usage.get(k) or 0) for k in
                       ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        self._zapisat("result", {
            "text": getattr(m, "result", None) or "",
            "is_error": bool(getattr(m, "is_error", False)),
            "duration_ms": getattr(m, "duration_ms", None),
            "cost_usd": getattr(m, "total_cost_usd", None),
            "tokens_out": usage.get("output_tokens"),
            "tokens_in": kontekst or usage.get("input_tokens"),
            "terminal_reason": getattr(m, "terminal_reason", None),
        })

    def _zapomnit_sessiyu(self, sid: str):
        chat = journal.poluchit_chat(self.chat_id)
        if chat and chat.get("claude_session_id") != sid:
            # Ветка уже отделилась — дальше продолжаем её, а не исходную сессию.
            journal.obnovit_chat(self.chat_id, claude_session_id=sid, fork_pending=0)
            if self._parametry:
                self._parametry = (*self._parametry[:4], sid, False)

    # ---------- подписка ----------
    def snimok(self) -> dict:
        """Что показать подключившемуся прямо сейчас, помимо журнала."""
        return {"type": "snapshot", "status": self.status, "bufer": self.bufer,
                "bufer_razmyshleniya": self.bufer_razmyshleniya, "metrika": self.metrika}


_chaty: dict[int, ZhivoyChat] = {}


def zhivoy(chat_id: int) -> ZhivoyChat:
    if chat_id not in _chaty:
        _chaty[chat_id] = ZhivoyChat(chat_id)
    return _chaty[chat_id]


async def zabyt(chat_id: int):
    """Чат удаляют — живую сессию надо снести целиком, иначе она попробует дописать
    события в несуществующий чат и оставит очередь висеть в памяти."""
    zh = _chaty.pop(chat_id, None)
    if zh is None:
        return
    zh.ochered.clear()
    for pid in list(zh.ozhidaniya):
        zh.otvetit_na_razreshenie(pid, "deny")
    if zh.zadacha and not zh.zadacha.done():
        zh.zadacha.cancel()
    await zh.zakryt()


def aktivnye() -> list[ZhivoyChat]:
    return [zh for zh in _chaty.values() if zh.client is not None]


def vse() -> list[ZhivoyChat]:
    return list(_chaty.values())


async def pribratsya():
    """Гасим простаивающие клиенты: каждый процесс claude — сотни мегабайт, а их на
    сервере 4 ГБ на всё. Закрытый чат продолжится по resume, контекст не теряется."""
    seychas = time.time()
    for zh in list(_chaty.values()):
        if zh.client is None or zh.status in ("running", "waiting"):
            continue
        prostoy = seychas - zh.aktivnost
        nikto_ne_smotrit = not zh.podpischiki
        if (nikto_ne_smotrit and prostoy > config.IDLE_UNLOAD_SECONDS) or prostoy > 1800:
            log.info("чат %s: выгружаю клиент (простой %.0f c, зрителей %d)",
                     zh.chat_id, prostoy, len(zh.podpischiki))
            await zh.zakryt()


async def osvobodit_mesto(krome: int | None = None):
    """Перед новым прогоном: если живых клиентов больше лимита — гасим самый старый простой."""
    while len([zh for zh in aktivnye() if zh.chat_id != krome]) >= config.MAX_ACTIVE_SESSIONS:
        kandidaty = [zh for zh in aktivnye() if zh.status != "running" and zh.chat_id != krome]
        if not kandidaty:
            raise RuntimeError(
                f"уже работает {config.MAX_ACTIVE_SESSIONS} сессии — дождись или останови одну")
        stareyshiy = min(kandidaty, key=lambda zh: zh.aktivnost)
        await stareyshiy.zakryt()
