const UI = window.BX_UI || {};
const CN = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
const levelName = (n) => `第${n <= 10 ? CN[n] : n}关`;
const fmt = (n) => (typeof n === "number" ? n.toLocaleString("zh-CN") : "–");

if (UI.accent_color) {
  document.documentElement.style.setProperty("--accent", UI.accent_color);
}

const chat = document.getElementById("chat");
const form = document.getElementById("guess-form");
const input = document.getElementById("guess-input");
const sendBtn = document.getElementById("send-btn");
const quotaEl = document.getElementById("quota");

const el = {
  level: document.getElementById("stat-level"),
  length: document.getElementById("stat-length"),
  total: document.getElementById("stat-total"),
  levelGuesses: document.getElementById("stat-level-guesses"),
};

let state = null;
let pending = false;

function addMessage(kind, html, chips) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${kind}`;
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = kind === "user" ? "我" : "游";
  const body = document.createElement("div");
  body.className = "body";
  body.innerHTML = html;
  if (chips && chips.length) {
    const row = document.createElement("div");
    row.className = "chips";
    chips.forEach((c) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = c;
      row.appendChild(chip);
    });
    body.appendChild(row);
  }
  wrap.append(avatar, body);
  chat.appendChild(wrap);
  while (chat.children.length > (UI.max_chat_messages || 60)) chat.removeChild(chat.firstChild);
  chat.scrollTop = chat.scrollHeight;
  return wrap;
}

function banner(text) {
  const b = document.createElement("div");
  b.className = "banner";
  b.textContent = text;
  chat.appendChild(b);
  chat.scrollTop = chat.scrollHeight;
}

function renderStats(s) {
  el.level.textContent = s.finished ? "通关" : levelName(s.level);
  el.length.textContent = s.password_length ?? "–";
  el.total.textContent = fmt(s.total_guesses);
  el.levelGuesses.textContent = fmt(s.level_guesses);
  if (s.finished) {
    quotaEl.textContent = "全部关卡已通过";
  } else if (typeof s.guesses_left_this_minute === "number") {
    quotaEl.textContent = `本分钟剩余 ${s.guesses_left_this_minute}/${s.rate_limit_per_minute} 次`;
  }
}

function describeRules() {
  const hint = UI.show_charset_hint ? "" : "服务端只公布密码长度，字符范围不透露。";
  addMessage(
    "system",
    `<b>规则</b><br>1. 全网共用一个密码，后台只保存它的哈希。<br>2. 猜对即过关，服务端自动开启下一关。<br>3. 进度记在 Cookie 里，第一关没过就打不开第二关。<br>4. 当前密码长度见上方统计。${hint}`
  );
}

async function loadStatus() {
  const res = await fetch("/api/status", { credentials: "same-origin" });
  const s = await res.json();
  const first = state === null;
  if (!first && s.global_level > state.global_level) {
    banner(`全网已过${levelName(state.global_level)}，${levelName(s.global_level)}开始（长度 ${s.password_length}）`);
  }
  if (!first && s.level > state.level) {
    banner(`你的进度：已进入${levelName(s.level)}`);
  }
  state = s;
  renderStats(s);
  if (first) {
    describeRules();
    addMessage("assistant", `现在是${levelName(s.level)}，密码长度 <span class="code">${s.password_length}</span> 位。全网已经猜了 <b>${fmt(s.total_guesses)}</b> 次。`);
    if (UI.poll_interval_ms > 0) setInterval(loadStatus, UI.poll_interval_ms);
  }
  return s;
}

async function submitGuess() {
  if (pending) return;
  const guess = input.value.trim();
  if (!guess) return;
  pending = true;
  sendBtn.disabled = true;
  addMessage("user", `猜：<span class="code">${escapeHtml(guess)}</span>`);
  input.value = "";
  try {
    const res = await fetch("/api/guess", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ guess }),
    });
    const data = await res.json();
    state = data.status || state;
    renderStats(state);
    const chips = [
      `全网总次数 ${fmt(state.total_guesses)}`,
      `${levelName(state.level || 1)}已猜 ${fmt(state.level_guesses)}`,
    ];
    if (data.correct) {
      addMessage(
        "result-ok",
        `<b>正确 ✅</b> ${data.advanced ? `自动进入${levelName(data.next_level)}，新密码长度 <span class="code">${data.next_length}</span> 位。` : data.finished ? "这是最后一关，你已通关！" : "本关全网已被破解，按自己的进度继续。"}`,
        chips
      );
      banner(data.finished ? "全部通关" : `进入${levelName(data.next_level || state.level)}`);
    } else if (data.ok) {
      addMessage(
        "result-bad",
        `${data.message}${data.length_matches ? "（长度对了）" : ""}`,
        chips
      );
    } else {
      addMessage("result-bad", data.message || "提交失败");
    }
  } catch (err) {
    addMessage("result-bad", `网络错误：${err.message}`);
  } finally {
    pending = false;
    sendBtn.disabled = state && state.finished;
    input.focus();
  }
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  submitGuess();
});

loadStatus().catch(() => {
  addMessage("result-bad", "无法连接服务端，请确认已运行 <span class=\"code\">python app.py</span>");
});
