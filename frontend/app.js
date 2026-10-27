// Claude в кармане — логика приложения: вход, список чатов, лента, живой поток.
import * as ui from "./ui.js";

const API = "/code-api";
const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);

const sostoyanie = {
  token: null,
  polzovatel: null,
  proekty: [],
  spravochniki: { modeli: [], efforty: [], rezhimy: [] },
  chaty: [],
  chat: null,            // текущий чат (объект)
  posledniySeq: 0,
  instrumenty: new Map(),
  razresheniya: new Map(),
  chernovik: null,
  chernovikRazmyshleniya: null,
  kontroller: null,     // AbortController активного потока
  taymer: null,
  nachaloProgona: 0,
  metrika: {},
  novyy: { proekt: null, model: "sonnet", effort: "high", rezhim: "bypassPermissions" },
};

// ---------- запросы ----------
async function zapros(put, optsii = {}) {
  const zagolovki = { "Content-Type": "application/json", ...(optsii.headers || {}) };
  if (sostoyanie.token) zagolovki.Authorization = `Bearer ${sostoyanie.token}`;
  const otvet = await fetch(API + put, { ...optsii, headers: zagolovki });
  if (!otvet.ok) {
    let tekst = "";
    try { tekst = (await otvet.json()).detail || ""; } catch { tekst = await otvet.text(); }
    throw new Error(tekst || `ошибка ${otvet.status}`);
  }
  return otvet.json();
}

// ---------- мелочи интерфейса ----------
function ekran(imya) {
  for (const e of ["zagruzka", "ekran-spisok", "ekran-chat", "oshibka"]) {
    const uzel = $(e);
    if (uzel) uzel.hidden = e !== imya;
  }
  if (imya === "ekran-chat") tg?.BackButton?.show();
  else tg?.BackButton?.hide();
}

function sboy(tekst) {
  $("oshibka-tekst").textContent = tekst;
  ekran("oshibka");
}

let tostTaymer = null;
function tost(tekst) {
  const u = $("tost");
  u.textContent = tekst;
  u.hidden = false;
  clearTimeout(tostTaymer);
  tostTaymer = setTimeout(() => { u.hidden = true; }, 3000);
}

function kogda(ts) {
  const d = new Date(ts * 1000);
  const seychas = new Date();
  const segodnya = d.toDateString() === seychas.toDateString();
  return segodnya
    ? d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

// ---------- список чатов ----------
async function zagruzitSpisok() {
  const dannye = await zapros("/chats");
  sostoyanie.chaty = dannye.chats;
  const uzel = $("spisok-chatov");
  uzel.innerHTML = "";
  if (!sostoyanie.chaty.length) {
    uzel.appendChild(ui.uzelPlashki("Чатов пока нет. Нажми «+», чтобы открыть первый."));
  }
  for (const ch of sostoyanie.chaty) {
    const kartochka = document.createElement("div");
    kartochka.className = "chat-kartochka";
    kartochka.innerHTML =
      `<div class="chat-verkh"><span class="chat-imya"></span><span class="chat-vremya"></span></div>` +
      `<div class="chat-niz"><span class="chat-proekt"></span><span class="chat-model"></span></div>`;
    kartochka.querySelector(".chat-imya").textContent = ch.title || "Без названия";
    kartochka.querySelector(".chat-vremya").textContent = kogda(ch.updated);
    kartochka.querySelector(".chat-proekt").textContent = ui.sokratitPut(ch.project);
    kartochka.querySelector(".chat-model").textContent =
      `${ch.model} · ${ch.effort}` + (ch.status === "running" ? " · работает" : "");
    if (ch.status === "running") kartochka.classList.add("running");
    kartochka.onclick = () => otkrytChat(ch.id);
    uzel.appendChild(kartochka);
  }
}

// ---------- лента ----------
function dobavit(uzel) {
  const lenta = $("lenta");
  const vnizu = lenta.scrollHeight - lenta.scrollTop - lenta.clientHeight < 140;
  lenta.appendChild(uzel);
  if (vnizu) prokrutitVniz();
  else $("knopka-vniz").hidden = false;
  return uzel;
}

function prokrutitVniz() {
  const lenta = $("lenta");
  lenta.scrollTop = lenta.scrollHeight;
  $("knopka-vniz").hidden = true;
}

function ubratChernovik(vid) {
  const klyuch = vid === "thinking" ? "chernovikRazmyshleniya" : "chernovik";
  if (sostoyanie[klyuch]) {
    sostoyanie[klyuch].remove();
    sostoyanie[klyuch] = null;
  }
}

function vChernovik(vid, tekst) {
  const klyuch = vid === "thinking" ? "chernovikRazmyshleniya" : "chernovik";
  if (!sostoyanie[klyuch]) {
    const uzel = vid === "thinking" ? ui.uzelRazmyshleniya("") : ui.uzelTeksta("");
    uzel.classList.add("chernovik");
    sostoyanie[klyuch] = dobavit(uzel);
    sostoyanie[klyuch].dataset.tekst = "";
  }
  const uzel = sostoyanie[klyuch];
  uzel.dataset.tekst = (uzel.dataset.tekst || "") + tekst;
  if (vid === "thinking") uzel.querySelector(".razmyshlenie-tekst").textContent = uzel.dataset.tekst;
  else uzel.innerHTML = ui.markdown(uzel.dataset.tekst);
  const lenta = $("lenta");
  if (lenta.scrollHeight - lenta.scrollTop - lenta.clientHeight < 200) prokrutitVniz();
}

function primenit(sobytie) {
  switch (sobytie.type) {
    case "user":
      dobavit(ui.uzelPolzovatelya(sobytie.text));
      break;
    case "text":
      ubratChernovik("text");
      dobavit(ui.uzelTeksta(sobytie.text));
      break;
    case "thinking":
      ubratChernovik("thinking");
      dobavit(ui.uzelRazmyshleniya(sobytie.text));
      break;
    case "tool_use": {
      ubratChernovik("text");
      if (sobytie.name === "TodoWrite" && sobytie.todos) {
        dobavit(ui.uzelZadach(sobytie.todos));
      } else {
        const uzel = dobavit(ui.uzelInstrumenta(sobytie));
        sostoyanie.instrumenty.set(sobytie.id, uzel);
      }
      obnovitProgress(`Выполняю: ${sobytie.name}`);
      break;
    }
    case "tool_result": {
      const uzel = sostoyanie.instrumenty.get(sobytie.id);
      if (uzel) ui.dopolnitRezultatom(uzel, sobytie);
      obnovitProgress("Думаю…");
      break;
    }
    case "permission": {
      ubratChernovik("text");
      const uzel = dobavit(ui.uzelRazresheniya(sobytie, otvetitNaRazreshenie));
      sostoyanie.razresheniya.set(sobytie.pid, uzel);
      tg?.HapticFeedback?.notificationOccurred?.("warning");
      break;
    }
    case "permission_answer": {
      const uzel = sostoyanie.razresheniya.get(sobytie.pid);
      if (uzel) ui.zakrytRazreshenie(uzel, sobytie.answer);
      sostoyanie.razresheniya.delete(sobytie.pid);
      break;
    }
    case "result":
      ubratChernovik("text");
      dobavit(ui.uzelItoga(sobytie));
      if (sobytie.tokens_in) {
        sostoyanie.kontekst = sobytie.tokens_in;
        podpisChata();
      }
      break;
    case "error":
      dobavit(ui.uzelPlashki(sobytie.text, "plokho"));
      break;
    case "system":
      dobavit(ui.uzelPlashki(sobytie.text));
      break;
    case "delta":
      vChernovik(sobytie.kind, sobytie.text);
      break;
    case "status":
      sostoyanie.metrika = sobytie.metrika || {};
      ustanovitStatus(sobytie.status);
      break;
    case "snapshot":
      sostoyanie.metrika = sobytie.metrika || {};
      ustanovitStatus(sobytie.status);
      if (sobytie.bufer) vChernovik("text", sobytie.bufer);
      if (sobytie.bufer_razmyshleniya) vChernovik("thinking", sobytie.bufer_razmyshleniya);
      break;
    case "phase":
      obnovitProgress(sobytie.text);
      break;
    case "ping":
      break;
  }
  if (sobytie.seq) sostoyanie.posledniySeq = Math.max(sostoyanie.posledniySeq, sobytie.seq);
}

async function otvetitNaRazreshenie(pid, otvet) {
  try {
    await zapros(`/permissions/${pid}/answer`, {
      method: "POST", body: JSON.stringify({ answer: otvet }),
    });
  } catch (e) {
    tost(e.message);           // могли ответить кнопкой в Telegram — тогда запрос уже закрыт
    const uzel = sostoyanie.razresheniya.get(pid);
    if (uzel) ui.zakrytRazreshenie(uzel, "закрыт");
  }
}

// ---------- индикатор работы ----------
function ustanovitStatus(status) {
  const idyot = status === "running" || status === "waiting";
  $("progress-line").hidden = !idyot;
  $("knopka-stop").hidden = !idyot;
  $("knopka-otpravit").hidden = idyot;
  if (status === "waiting") $("progress-text").textContent = "Жду ответа на разрешение…";
  if (sostoyanie.chat) sostoyanie.chat.status = status;
  clearInterval(sostoyanie.taymer);
  if (idyot) {
    sostoyanie.nachaloProgona = (sostoyanie.metrika.nachalo || Date.now() / 1000) * 1000;
    obnovitProgress();
    sostoyanie.taymer = setInterval(obnovitProgress, 1000);
  } else {
    ubratChernovik("text");
    ubratChernovik("thinking");
  }
}

function obnovitProgress(tekst) {
  if (tekst) $("progress-text").textContent = tekst;
  const sekundy = Math.max(0, Math.round((Date.now() - sostoyanie.nachaloProgona) / 1000));
  const tokeny = sostoyanie.metrika.tokeny ? ` · ${sostoyanie.metrika.tokeny} ток` : "";
  $("progress-metrics").textContent = `${sekundy} с${tokeny}`;
}

// ---------- поток ----------
async function chitatPotok(chatId) {
  const kontroller = new AbortController();
  sostoyanie.kontroller = kontroller;
  const otvet = await fetch(`${API}/chats/${chatId}/stream?after=${sostoyanie.posledniySeq}`, {
    headers: { Authorization: `Bearer ${sostoyanie.token}` },
    signal: kontroller.signal,
  });
  if (!otvet.ok) throw new Error(`поток: ${otvet.status}`);
  const chitatel = otvet.body.getReader();
  const dekoder = new TextDecoder();
  let bufer = "";
  while (true) {
    const { value, done } = await chitatel.read();
    if (done) break;
    bufer += dekoder.decode(value, { stream: true });
    let kray;
    while ((kray = bufer.indexOf("\n")) >= 0) {
      const stroka = bufer.slice(0, kray).trim();
      bufer = bufer.slice(kray + 1);
      if (!stroka) continue;
      try { primenit(JSON.parse(stroka)); } catch (e) { console.warn("строка потока", e); }
    }
  }
}

async function derzhatPotok(chatId) {
  let pauza = 800;
  while (sostoyanie.chat && sostoyanie.chat.id === chatId) {
    try {
      await chitatPotok(chatId);
      pauza = 800;
    } catch (e) {
      if (e.name === "AbortError") { if (!sostoyanie.chat || sostoyanie.chat.id !== chatId) return; }
      else pauza = Math.min(pauza * 1.6, 8000);
    }
    if (!sostoyanie.chat || sostoyanie.chat.id !== chatId) return;
    await new Promise((r) => setTimeout(r, pauza));
  }
}

// ---------- открытие чата ----------
async function otkrytChat(chatId) {
  sostoyanie.kontroller?.abort();
  sostoyanie.chat = { id: chatId };
  sostoyanie.posledniySeq = 0;
  sostoyanie.instrumenty.clear();
  sostoyanie.chernovik = sostoyanie.chernovikRazmyshleniya = null;
  $("lenta").innerHTML = "";
  ekran("ekran-chat");

  const dannye = await zapros(`/chats/${chatId}`);
  sostoyanie.chat = dannye.chat;
  $("chat-zagolovok").textContent = dannye.chat.title || "Без названия";
  $("chat-podpis").textContent =
    `${ui.sokratitPut(dannye.chat.project)} · ${dannye.chat.model} · ${dannye.chat.effort}`;
  for (const sobytie of dannye.events) primenit(sobytie);
  primenit({ ...dannye.snapshot, type: "snapshot" });
  prokrutitVniz();
  derzhatPotok(chatId);
}

function zakrytChat() {
  sostoyanie.kontroller?.abort();
  sostoyanie.chat = null;
  clearInterval(sostoyanie.taymer);
  ekran("ekran-spisok");
  zagruzitSpisok();
}

// ---------- отправка ----------
async function otpravit() {
  const pole = $("pole");
  const tekst = pole.value.trim();
  if (!tekst || !sostoyanie.chat) return;
  pole.value = "";
  pole.style.height = "auto";
  tg?.HapticFeedback?.impactOccurred?.("light");
  if (!sostoyanie.chat.title) {          // бэкенд назовёт чат так же — по первой задаче
    sostoyanie.chat.title = tekst.slice(0, 40);
    $("chat-zagolovok").textContent = sostoyanie.chat.title;
  }
  try {
    await zapros(`/chats/${sostoyanie.chat.id}/message`, {
      method: "POST", body: JSON.stringify({ text: tekst }),
    });
    ustanovitStatus("running");
  } catch (e) {
    tost(e.message);
    pole.value = tekst;
  }
}

async function stop() {
  if (!sostoyanie.chat) return;
  try {
    await zapros(`/chats/${sostoyanie.chat.id}/interrupt`, { method: "POST" });
    tost("Останавливаю…");
  } catch (e) { tost(e.message); }
}

// ---------- шторка нового чата ----------
function knopkiVybora(konteyner, znacheniya, vybrannoe, priVybore, podpisi = {}) {
  konteyner.innerHTML = "";
  for (const z of znacheniya) {
    const b = document.createElement("button");
    b.className = "vybor-knopka" + (z === vybrannoe ? " aktivna" : "");
    b.textContent = podpisi[z] || z;
    b.onclick = () => {
      priVybore(z);
      konteyner.querySelectorAll(".vybor-knopka").forEach((k) => k.classList.remove("aktivna"));
      b.classList.add("aktivna");
    };
    konteyner.appendChild(b);
  }
}

const PODPISI_REZHIMOV = {
  bypassPermissions: "Автопилот — ничего не спрашивать",
  acceptEdits: "Правки без спроса, остальное спрашивать",
  default: "Спрашивать всё",
  plan: "Только план, без изменений",
};

function otkrytShtorku() {
  const n = sostoyanie.novyy;
  n.proekt = n.proekt || sostoyanie.proekty[0]?.path;
  const vybor = $("vybor-proekta");
  vybor.innerHTML = "";
  for (const p of sostoyanie.proekty) {
    const b = document.createElement("button");
    b.className = "vybor-knopka shirokaya" + (p.path === n.proekt ? " aktivna" : "");
    b.innerHTML = `<b></b><span></span>`;
    b.querySelector("b").textContent = p.name;
    b.querySelector("span").textContent = p.path;
    b.onclick = () => {
      n.proekt = p.path;
      $("svoy-put").value = "";
      vybor.querySelectorAll(".vybor-knopka").forEach((k) => k.classList.remove("aktivna"));
      b.classList.add("aktivna");
    };
    vybor.appendChild(b);
  }
  knopkiVybora($("vybor-modeli"), sostoyanie.spravochniki.modeli, n.model, (z) => (n.model = z));
  knopkiVybora($("vybor-efforta"), sostoyanie.spravochniki.efforty, n.effort, (z) => (n.effort = z));
  knopkiVybora($("vybor-rezhima"), sostoyanie.spravochniki.rezhimy, n.rezhim, (z) => (n.rezhim = z),
    PODPISI_REZHIMOV);
  $("shtorka").hidden = false;
}

async function sozdatChat() {
  const n = sostoyanie.novyy;
  const put = $("svoy-put").value.trim() || n.proekt;
  try {
    const chat = await zapros("/chats", {
      method: "POST",
      body: JSON.stringify({ project: put, model: n.model, effort: n.effort,
                             permission_mode: n.rezhim }),
    });
    $("shtorka").hidden = true;
    await otkrytChat(chat.id);
  } catch (e) { tost(e.message); }
}

// ---------- настройки чата ----------
function podpisChata() {
  const ch = sostoyanie.chat;
  // Точный размер контекста даёт только «/context» — в usage он приходит без кэша,
  // поэтому в шапке показываем лишь заметные значения.
  const kontekst = sostoyanie.kontekst > 1000
    ? ` · ${Math.round(sostoyanie.kontekst / 1000)}k контекст` : "";
  $("chat-podpis").textContent =
    `${ui.sokratitPut(ch.project)} · ${ch.model} · ${ch.effort}${kontekst}`;
  $("chat-zagolovok").textContent = ch.title || "Без названия";
}

async function izmenitChat(polya) {
  try {
    sostoyanie.chat = await zapros(`/chats/${sostoyanie.chat.id}`, {
      method: "PATCH", body: JSON.stringify(polya),
    });
    podpisChata();
  } catch (e) { tost(e.message); }
}

function otkrytNastroykiChata() {
  const ch = sostoyanie.chat;
  if (!ch) return;
  $("chat-imya-pole").value = ch.title || "";
  $("chat-imya-pole").onchange = (e) => izmenitChat({ title: e.target.value.trim() });
  knopkiVybora($("chat-model"), sostoyanie.spravochniki.modeli, ch.model,
    (z) => izmenitChat({ model: z }));
  knopkiVybora($("chat-effort"), sostoyanie.spravochniki.efforty, ch.effort,
    (z) => izmenitChat({ effort: z }));
  knopkiVybora($("chat-rezhim"), sostoyanie.spravochniki.rezhimy, ch.permission_mode,
    (z) => izmenitChat({ permission_mode: z }), PODPISI_REZHIMOV);
  $("chat-papka").textContent = ch.project;
  $("shtorka-chat").hidden = false;
}

async function forknut() {
  try {
    const novyy = await zapros(`/chats/${sostoyanie.chat.id}/fork`, { method: "POST" });
    $("shtorka-chat").hidden = true;
    tost("Создана ветка — исходный чат не тронут");
    await otkrytChat(novyy.id);
  } catch (e) { tost(e.message); }
}

async function vArkhiv() {
  await izmenitChat({ archived: 1 });
  $("shtorka-chat").hidden = true;
  zakrytChat();
}

function udalitChat() {
  const id = sostoyanie.chat.id;
  const ubrat = async () => {
    try {
      await zapros(`/chats/${id}`, { method: "DELETE" });
      $("shtorka-chat").hidden = true;
      zakrytChat();
    } catch (e) { tost(e.message); }
  };
  if (tg?.showConfirm) tg.showConfirm("Удалить чат вместе с историей?", (da) => da && ubrat());
  else if (confirm("Удалить чат?")) ubrat();
}

// ---------- команды и скиллы ----------
const KOMANDY = [
  ["/context", "Сколько занято контекста"],
  ["/compact", "Сжать историю, чтобы влезло больше"],
  ["/clear", "Начать разговор заново (файлы не трогает)"],
  ["/usage", "Расход по подписке"],
  ["/recap", "Краткий пересказ разговора"],
];

async function otkrytKomandy() {
  const komandy = $("spisok-komand");
  komandy.innerHTML = "";
  for (const [imya, poyasnenie] of KOMANDY) {
    const b = document.createElement("button");
    b.className = "vybor-knopka shirokaya";
    b.innerHTML = `<b></b><span></span>`;
    b.querySelector("b").textContent = imya;
    b.querySelector("span").textContent = poyasnenie;
    b.onclick = async () => {
      $("shtorka-komandy").hidden = true;
      $("pole").value = imya;
      await otpravit();
    };
    komandy.appendChild(b);
  }

  const skilly = $("spisok-skillov");
  skilly.innerHTML = "<div class=\"плашка\">Загружаю…</div>";
  try {
    const dannye = await zapros("/skills");
    skilly.innerHTML = "";
    for (const s of dannye.skills) {
      const b = document.createElement("button");
      b.className = "vybor-knopka shirokaya";
      b.innerHTML = `<b></b><span></span>`;
      b.querySelector("b").textContent = s.name;
      b.querySelector("span").textContent = s.description || "";
      b.onclick = () => {
        $("shtorka-komandy").hidden = true;
        const pole = $("pole");
        pole.value = `Используй скилл ${s.name}: ` + pole.value;
        pole.focus();
      };
      skilly.appendChild(b);
    }
    if (!dannye.skills.length) skilly.innerHTML = "<div class=\"плашка\">Скиллов нет</div>";
  } catch (e) {
    skilly.innerHTML = "";
    skilly.appendChild(ui.uzelPlashki("Не загрузились: " + e.message, "plokho"));
  }
  $("shtorka-komandy").hidden = false;
}

// ---------- файлы ----------
async function otkrytPapku(put) {
  try {
    const dannye = await zapros(`/files?path=${encodeURIComponent(put)}`);
    sostoyanie.papka = dannye.path;
    $("fayl-prosmotr").hidden = true;
    $("fayly-spisok").hidden = false;
    $("fayly-put").textContent = dannye.path;
    const spisok = $("fayly-spisok");
    spisok.innerHTML = "";
    if (dannye.parent && dannye.parent !== dannye.path) {
      spisok.appendChild(strokaFayla("..", () => otkrytPapku(dannye.parent), "papka"));
    }
    for (const p of dannye.dirs) {
      spisok.appendChild(strokaFayla(p.name + "/", () => otkrytPapku(p.path), "papka"));
    }
    for (const f of dannye.files) {
      spisok.appendChild(strokaFayla(f.name, () => otkrytFayl(f.path), "fayl",
        f.size > 1024 ? `${Math.round(f.size / 1024)} КБ` : `${f.size} Б`));
    }
    $("shtorka-fayly").hidden = false;
  } catch (e) { tost(e.message); }
}

function strokaFayla(imya, priKlike, klass, sprava = "") {
  const div = document.createElement("div");
  div.className = "fayl-stroka " + klass;
  div.innerHTML = `<span class="fayl-imya"></span><span class="fayl-sprava"></span>`;
  div.querySelector(".fayl-imya").textContent = imya;
  div.querySelector(".fayl-sprava").textContent = sprava;
  div.onclick = priKlike;
  return div;
}

async function otkrytFayl(put) {
  try {
    const dannye = await zapros(`/file?path=${encodeURIComponent(put)}`);
    sostoyanie.fayl = put;
    $("fayly-spisok").hidden = true;
    $("fayl-prosmotr").hidden = false;
    $("fayly-put").textContent = put;
    $("fayl-tekst").textContent = dannye.binary
      ? `(двоичный файл, ${Math.round(dannye.size / 1024)} КБ — можно отправить в Telegram)`
      : dannye.text + (dannye.truncated ? "\n…(показан не весь файл)" : "");
  } catch (e) { tost(e.message); }
}

async function faylVTelegram() {
  try {
    await zapros("/file/to-telegram", {
      method: "POST", body: JSON.stringify({ path: sostoyanie.fayl }),
    });
    tost("Отправил в Telegram");
  } catch (e) { tost(e.message); }
}

function upomyanutFayl() {
  const pole = $("pole");
  pole.value = (pole.value ? pole.value.trimEnd() + " " : "") + sostoyanie.fayl + " ";
  $("shtorka-fayly").hidden = true;
  $("shtorka-chat").hidden = true;
  pole.focus();
}

async function prikrepit(fayl) {
  const forma = new FormData();
  forma.append("file", fayl);
  try {
    tost("Загружаю…");
    const otvet = await fetch(`${API}/upload`, {
      method: "POST", headers: { Authorization: `Bearer ${sostoyanie.token}` }, body: forma,
    });
    if (!otvet.ok) throw new Error(await otvet.text());
    const dannye = await otvet.json();
    const pole = $("pole");
    pole.value = (pole.value ? pole.value.trimEnd() + "\n" : "") + dannye.path;
    tost("Файл на сервере — опиши, что с ним сделать");
    pole.focus();
  } catch (e) { tost("Не загрузилось: " + e.message); }
}

// ---------- запуск ----------
async function start() {
  tg?.ready();
  tg?.expand();
  if (tg?.setHeaderColor) { try { tg.setHeaderColor("secondary_bg_color"); } catch {} }

  if (!tg?.initData) { sboy("Открой приложение из Telegram — вне мессенджера входа нет."); return; }
  try {
    const dannye = await zapros("/auth", {
      method: "POST", body: JSON.stringify({ init_data: tg.initData }),
    });
    sostoyanie.token = dannye.token;
    sostoyanie.polzovatel = dannye.user;
    sostoyanie.proekty = dannye.projects || [];
    sostoyanie.spravochniki = { modeli: dannye.modeli, efforty: dannye.efforty, rezhimy: dannye.rezhimy };
  } catch (e) { sboy("Вход не удался: " + e.message); return; }

  $("knopka-novyy").onclick = otkrytShtorku;
  $("knopka-sozdat").onclick = sozdatChat;
  $("knopka-nazad").onclick = zakrytChat;
  $("knopka-otpravit").onclick = otpravit;
  $("knopka-stop").onclick = stop;
  $("knopka-vniz").onclick = prokrutitVniz;
  $("knopka-nastroyki").onclick = otkrytNastroykiChata;
  $("knopka-fork").onclick = forknut;
  $("knopka-arkhiv").onclick = vArkhiv;
  $("knopka-udalit").onclick = udalitChat;
  $("knopka-komandy").onclick = otkrytKomandy;
  $("knopka-fayly").onclick = () => otkrytPapku(sostoyanie.chat.project);
  $("knopka-v-telegram").onclick = faylVTelegram;
  $("knopka-upomyanut").onclick = upomyanutFayl;
  $("knopka-k-spisku").onclick = () => otkrytPapku(sostoyanie.papka || sostoyanie.chat.project);
  $("knopka-skrepka").onclick = () => $("fayl-vkhod").click();
  $("fayl-vkhod").onchange = (e) => {
    const fayl = e.target.files?.[0];
    if (fayl) prikrepit(fayl);
    e.target.value = "";
  };
  for (const sh of ["shtorka", "shtorka-chat", "shtorka-fayly", "shtorka-komandy"]) {
    $(sh).querySelectorAll("[data-zakryt]").forEach((e) => {
      e.onclick = () => ($(sh).hidden = true);
    });
  }
  tg?.BackButton?.onClick(() => {
    const otkrytaya = ["shtorka", "shtorka-chat", "shtorka-fayly", "shtorka-komandy"]
      .find((sh) => !$(sh).hidden);
    if (otkrytaya) $(otkrytaya).hidden = true;
    else zakrytChat();
  });

  const pole = $("pole");
  pole.addEventListener("input", () => {
    pole.style.height = "auto";
    pole.style.height = Math.min(pole.scrollHeight, 160) + "px";
  });
  pole.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); otpravit(); }
  });
  $("lenta").addEventListener("scroll", () => {
    const l = $("lenta");
    if (l.scrollHeight - l.scrollTop - l.clientHeight < 60) $("knopka-vniz").hidden = true;
  });

  // Вернулись в приложение — рвём подвисшее соединение, цикл поднимет новое с нужного места.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && sostoyanie.chat) sostoyanie.kontroller?.abort();
  });

  await zagruzitSpisok();
  ekran("ekran-spisok");
}

start();
