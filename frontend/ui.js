// Отрисовка ленты: markdown, карточки инструментов, служебные плашки.

const EKRAN = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const ekranirovat = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => EKRAN[c]);

// ---------- мини-markdown ----------
function strochnyy(tekst) {
  let t = ekranirovat(tekst);
  t = t.replace(/`([^`\n]+)`/g, (_, k) => `<code>${k}</code>`);
  t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  t = t.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<i>$2</i>");
  t = t.replace(/~~([^~]+)~~/g, "<s>$1</s>");
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return t;
}

export function markdown(tekst) {
  const stroki = String(tekst ?? "").split("\n");
  const chasti = [];
  let spisok = null;   // "ul" | "ol"
  let vKode = false;
  let kod = [];
  let yazyk = "";

  const zakrytSpisok = () => { if (spisok) { chasti.push(`</${spisok}>`); spisok = null; } };

  for (const stroka of stroki) {
    const zabor = stroka.match(/^\s*```(\w*)/);
    if (zabor) {
      if (vKode) {
        chasti.push(`<pre class="kod"${yazyk ? ` data-язык="${ekranirovat(yazyk)}"` : ""}>` +
                   `<code>${ekranirovat(kod.join("\n"))}</code></pre>`);
        kod = []; yazyk = ""; vKode = false;
      } else {
        zakrytSpisok();
        vKode = true; yazyk = zabor[1] || "";
      }
      continue;
    }
    if (vKode) { kod.push(stroka); continue; }

    if (!stroka.trim()) { zakrytSpisok(); continue; }

    const zagolovok = stroka.match(/^(#{1,4})\s+(.*)$/);
    if (zagolovok) {
      zakrytSpisok();
      const u = Math.min(zagolovok[1].length + 2, 6);
      chasti.push(`<h${u}>${strochnyy(zagolovok[2])}</h${u}>`);
      continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(stroka)) { zakrytSpisok(); chasti.push("<hr>"); continue; }

    const marker = stroka.match(/^\s*[-*•]\s+(.*)$/);
    const nomer = stroka.match(/^\s*\d+[.)]\s+(.*)$/);
    if (marker || nomer) {
      const nuzhen = marker ? "ul" : "ol";
      if (spisok !== nuzhen) { zakrytSpisok(); chasti.push(`<${nuzhen}>`); spisok = nuzhen; }
      chasti.push(`<li>${strochnyy((marker || nomer)[1])}</li>`);
      continue;
    }
    zakrytSpisok();
    chasti.push(`<p>${strochnyy(stroka)}</p>`);
  }
  if (vKode && kod.length) {
    chasti.push(`<pre class="kod"><code>${ekranirovat(kod.join("\n"))}</code></pre>`);
  }
  zakrytSpisok();
  return chasti.join("");
}

// ---------- инструменты ----------
const IKONKI = {
  Bash: "▸", Read: "◉", Write: "✎", Edit: "✎", Glob: "◎", Grep: "⌕",
  Task: "⚙", WebFetch: "⇩", WebSearch: "⌕", TodoWrite: "☑", Skill: "★",
  Workflow: "⚙", NotebookEdit: "✎", ExitPlanMode: "☰", AskUserQuestion: "?",
};

const PRAVKI = new Set(["Edit", "Write", "NotebookEdit", "MultiEdit"]);

// Построчный diff (LCS) — достаточно для правок, которые делает агент.
function razlichiya(bylo, stalo) {
  const a = bylo ? String(bylo).split("\n") : [];      // новый файл — только добавленные строки
  const b = stalo ? String(stalo).split("\n") : [];
  if (a.length * b.length > 250_000) {          // защита от гигантских правок
    return [...a.map((s) => ["-", s]), ...b.map((s) => ["+", s])];
  }
  const tablitsa = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      tablitsa[i][j] = a[i] === b[j] ? tablitsa[i + 1][j + 1] + 1
                                    : Math.max(tablitsa[i + 1][j], tablitsa[i][j + 1]);
    }
  }
  const itog = [];
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { itog.push([" ", a[i]]); i++; j++; }
    else if (tablitsa[i + 1][j] >= tablitsa[i][j + 1]) { itog.push(["-", a[i]]); i++; }
    else { itog.push(["+", b[j]]); j++; }
  }
  while (i < a.length) itog.push(["-", a[i++]]);
  while (j < b.length) itog.push(["+", b[j++]]);
  return itog;
}

function blokDiff(bylo, stalo) {
  const uzel = document.createElement("pre");
  uzel.className = "diff";
  for (const [znak, stroka] of razlichiya(bylo, stalo)) {
    const s = document.createElement("span");
    s.className = znak === "+" ? "plyus" : znak === "-" ? "minus" : "rovno";
    s.textContent = `${znak} ${stroka}`;
    uzel.appendChild(s);
  }
  return uzel;
}

function teloPravki(imya, vkhod = {}) {
  const obyortka = document.createElement("div");
  if (imya === "Write") {
    obyortka.appendChild(blokDiff("", vkhod.content || ""));
  } else if (imya === "MultiEdit" && Array.isArray(vkhod.edits)) {
    for (const pravka of vkhod.edits) {
      obyortka.appendChild(blokDiff(pravka.old_string || "", pravka.new_string || ""));
    }
  } else {
    obyortka.appendChild(blokDiff(vkhod.old_string || vkhod.old_source || "",
                                 vkhod.new_string || vkhod.new_source || ""));
  }
  return obyortka;
}

function kratkoeOpisanie(imya, vkhod = {}) {
  const v = vkhod || {};
  switch (imya) {
    case "Bash": return v.command || "";
    case "Read": case "Write": case "Edit": case "NotebookEdit":
      return sokratitPut(v.file_path || v.notebook_path || "");
    case "Glob": return `${v.pattern || ""} ${v.path ? "в " + sokratitPut(v.path) : ""}`.trim();
    case "Grep": return `${v.pattern || ""} ${v.path ? "в " + sokratitPut(v.path) : ""}`.trim();
    case "Task": return v.description || v.subagent_type || "";
    case "WebFetch": return v.url || "";
    case "WebSearch": return v.query || "";
    case "Skill": return v.skill || "";
    case "TodoWrite": return `${(v.todos || []).length} пунктов`;
    case "ExitPlanMode": return "план готов";
    default: {
      const pervoe = Object.values(v)[0];
      return typeof pervoe === "string" ? pervoe : "";
    }
  }
}

export function sokratitPut(put) {
  const s = String(put || "");
  const chasti = s.split("/").filter(Boolean);
  return chasti.length > 2 ? "…/" + chasti.slice(-2).join("/") : s;
}

function teloInstrumenta(vkhod) {
  if (vkhod && vkhod.command) return vkhod.command;
  return typeof vkhod === "string" ? vkhod : JSON.stringify(vkhod, null, 2);
}

function tekstRezultata(soderzhimoe) {
  if (soderzhimoe == null) return "";
  if (typeof soderzhimoe === "string") return soderzhimoe;
  if (Array.isArray(soderzhimoe)) {
    return soderzhimoe.map((ch) => (typeof ch === "string" ? ch : ch.text || JSON.stringify(ch))).join("\n");
  }
  return JSON.stringify(soderzhimoe, null, 2);
}

// ---------- узлы ленты ----------
export function uzelPolzovatelya(tekst) {
  const div = document.createElement("div");
  div.className = "soobshchenie moy";
  div.textContent = tekst;
  return div;
}

export function uzelTeksta(tekst) {
  const div = document.createElement("div");
  div.className = "soobshchenie otvet";
  div.innerHTML = markdown(tekst);
  return div;
}

export function uzelRazmyshleniya(tekst) {
  const d = document.createElement("details");
  d.className = "razmyshlenie";
  d.innerHTML = `<summary>Размышление</summary><div class="razmyshlenie-tekst"></div>`;
  d.querySelector(".razmyshlenie-tekst").textContent = tekst;
  return d;
}

export function uzelInstrumenta(sobytie) {
  const d = document.createElement("details");
  d.className = "instrument";
  d.dataset.id = sobytie.id || "";
  const ikonka = IKONKI[sobytie.name] || "•";
  d.innerHTML =
    `<summary><span class="i-znachok">${ikonka}</span>` +
    `<span class="i-imya"></span><span class="i-kratko"></span>` +
    `<span class="i-sostoyanie">…</span></summary>` +
    `<div class="i-telo"><pre class="i-vkhod"></pre><pre class="i-vykhod" hidden></pre></div>`;
  d.querySelector(".i-imya").textContent = sobytie.name;
  d.querySelector(".i-kratko").textContent = kratkoeOpisanie(sobytie.name, sobytie.input);
  const vkhod = d.querySelector(".i-vkhod");
  if (PRAVKI.has(sobytie.name) && sobytie.input && !sobytie.input._obrezano) {
    vkhod.replaceWith(teloPravki(sobytie.name, sobytie.input));
    d.classList.add("pravka");
    d.open = true;                    // правки показываем сразу: это главное, что стоит увидеть
  } else {
    vkhod.textContent = teloInstrumenta(sobytie.input);
  }
  return d;
}

export function dopolnitRezultatom(uzel, sobytie) {
  const vykhod = uzel.querySelector(".i-vykhod");
  vykhod.hidden = false;
  vykhod.textContent = tekstRezultata(sobytie.content) || "(пусто)";
  const sostoyanie = uzel.querySelector(".i-sostoyanie");
  sostoyanie.textContent = sobytie.is_error ? "ошибка" : "готово";
  sostoyanie.classList.toggle("plokho", !!sobytie.is_error);
  uzel.classList.toggle("sboy", !!sobytie.is_error);
}

export function uzelRazresheniya(sobytie, otvetit) {
  const plan = sobytie.tool === "ExitPlanMode";
  const div = document.createElement("div");
  div.className = "razreshenie" + (plan ? " plan" : "");
  div.dataset.pid = sobytie.pid;

  const shapka = document.createElement("div");
  shapka.className = "razreshenie-shapka";
  shapka.textContent = plan ? "План готов — принять?" : `Разрешить ${sobytie.tool}?`;
  div.appendChild(shapka);

  const telo = document.createElement("div");
  telo.className = "razreshenie-telo";
  if (plan) telo.innerHTML = markdown((sobytie.input || {}).plan || "_(план без описания)_");
  else telo.textContent = kratkoeOpisanie(sobytie.tool, sobytie.input) ||
                          JSON.stringify(sobytie.input || {}).slice(0, 400);
  div.appendChild(telo);

  const knopki = document.createElement("div");
  knopki.className = "razreshenie-knopki";
  const nabor = plan
    ? [["Принять план", "allow"], ["Не сейчас", "deny"]]
    : [["Разрешить", "allow"], ["Всегда", "always"], ["Запретить", "deny"]];
  for (const [podpis, otvet] of nabor) {
    const b = document.createElement("button");
    b.className = "knopka-vtorostepennaya" + (otvet === "deny" ? " opasno" : "");
    b.textContent = podpis;
    b.onclick = () => otvetit(sobytie.pid, otvet);
    knopki.appendChild(b);
  }
  div.appendChild(knopki);
  return div;
}

export function zakrytRazreshenie(uzel, otvet) {
  const podpisi = { allow: "разрешено", always: "разрешено навсегда",
                    deny: "запрещено", timeout: "истекло время — отказ" };
  uzel.querySelector(".razreshenie-knopki")?.remove();
  const itog = document.createElement("div");
  itog.className = "razreshenie-itog" + (otvet === "allow" || otvet === "always" ? "" : " plokho");
  itog.textContent = podpisi[otvet] || otvet;
  uzel.appendChild(itog);
}

export function uzelZadach(todos) {
  const div = document.createElement("div");
  div.className = "zadachi";
  const gotovo = todos.filter((t) => t.status === "completed").length;
  div.innerHTML = `<div class="zadachi-shapka">План · ${gotovo}/${todos.length}</div>`;
  for (const t of todos) {
    const stroka = document.createElement("div");
    stroka.className = "zadacha " + (t.status || "pending");
    stroka.textContent = (t.status === "completed" ? "✓ " :
                          t.status === "in_progress" ? "▸ " : "· ") + (t.content || "");
    div.appendChild(stroka);
  }
  return div;
}

export function uzelPlashki(tekst, klass = "") {
  const div = document.createElement("div");
  div.className = "plashka " + klass;
  div.textContent = tekst;
  return div;
}

export function uzelItoga(sobytie) {
  const chasti = [];
  if (sobytie.duration_ms) chasti.push(`${(sobytie.duration_ms / 1000).toFixed(1)} с`);
  if (sobytie.tokens_out) chasti.push(`${sobytie.tokens_out} ток`);
  if (sobytie.cost_usd) chasti.push(`$${Number(sobytie.cost_usd).toFixed(3)}`);
  if (sobytie.terminal_reason && sobytie.terminal_reason.startsWith("aborted")) chasti.push("прервано");
  const div = document.createElement("div");
  div.className = "itog" + (sobytie.is_error ? " plokho" : "");
  div.textContent = chasti.join(" · ") || "готово";
  return div;
}
