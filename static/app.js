const UI = window.BX_UI || {};
const PAGE_LEVEL = document.body.dataset.level ? Number(document.body.dataset.level) : null;
const CN = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
const levelName = (n) => `第${n <= 10 ? CN[n] : n}关`;
const levelUrl = (n) => (n === 1 ? "/" : `/level/${n}`);
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
  riddle: document.getElementById("riddle"),
  riddleLevel: document.getElementById("riddle-level"),
  riddleTag: document.getElementById("riddle-tag"),
  riddleText: document.getElementById("riddle-text"),
  riddleLength: document.getElementById("riddle-length"),
  riddleHint: document.getElementById("riddle-hint"),
  hintBtn: document.getElementById("hint-btn"),
  nav: document.getElementById("levels-nav"),
};

let state = null;
let view = null; // the level shown on this page; may be a cleared earlier level
let progress = null;
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

// 题面卡：内容全部来自服务端，未解锁的关卡服务端就不会下发题面。
function renderRiddle(card) {
  el.riddle.hidden = false;
  el.riddleLevel.textContent = levelName(card.level);
  const tags = [];
  if (card.is_current) tags.push("当前关");
  if (card.solved) tags.push("已被破解");
  if (!card.is_playable) tags.push("回看");
  el.riddleTag.textContent = tags.join(" · ");
  el.riddleText.textContent =
    card.prompt || (card.mode === "puzzle" ? "本题没有题面：这一关是随机密码，只能靠长度猜。" : "随机密码模式：没有题面，只知道长度。");
  el.riddleLength.textContent = `答案长度 ${card.password_length ?? card.length ?? "–"}`;
  if (card.hint && UI.show_hint !== false) {
    el.hintBtn.hidden = false;
    el.hintBtn.onclick = () => {
      el.riddleHint.hidden = false;
      el.riddleHint.textContent = card.hint;
      el.hintBtn.hidden = true;
    };
  } else {
    el.hintBtn.hidden = true;
    el.riddleHint.hidden = true;
  }
  const readOnly = !card.is_playable;
  input.disabled = readOnly || (state && state.finished);
  input.placeholder = readOnly ? "这一关已经过去了，回到当前关继续猜" : "输入答案，回车提交";
  sendBtn.disabled = readOnly || (state && state.finished);
}

function renderNav() {
  if (!progress) return;
  const kids = progress.levels.map((l) => {
    if (l.locked) return document.createElement("span");
    const a = document.createElement("a");
    a.href = levelUrl(l.level);
    a.textContent = levelName(l.level);
    if (l.solved) a.classList.add("solved");
    if (view && l.level === view.level) a.classList.add("current");
    return a;
  });
  const lockedCount = progress.levels.filter((l) => l.locked).length;
  if (lockedCount) {
    const s = document.createElement("span");
    s.className = "locked";
    s.textContent = `后面还有 ${lockedCount} 关未解锁`;
    kids.push(s);
  }
  el.nav.replaceChildren(...kids);
}

async function loadLevelCard() {
  const target = PAGE_LEVEL ?? (state ? state.level : 1);
  if (!target) return;
  try {
    const res = await fetch(`/api/level/${target}`, { credentials: "same-origin" });
    const data = await res.json();
    if (data.mode === undefined) data.mode = state ? state.mode : "puzzle";
    if (!res.ok) {
      if (data.own_level) {
        banner(`第 ${target} 关还没解锁，已带你回到${levelName(data.own_level)}`);
        view = { level: data.own_level, password_length: state ? state.password_length : null, prompt: null };
        renderRiddle({ ...view, mode: data.mode });
      }
      return;
    }
    view = data;
    renderRiddle(data);
  } catch (err) {
    addMessage("result-bad", `题面加载失败：${err.message}`);
  }
}

async function loadProgress() {
  try {
    progress = await (await fetch("/api/progress", { credentials: "same-origin" })).json();
    renderNav();
  } catch (err) {
    /* 导航失败不影响猜题 */
  }
}

function describeRules() {
  const hint = UI.show_charset_hint ? "" : "字符范围不透露，只给长度。";
  addMessage(
    "system",
    `<b>规则</b><br>1. 每关一道题，答案就是密码；后台只保存答案的哈希。<br>2. 任意人猜对，全网自动进入下一关。<br>3. 进度记在 Cookie 里，第一关没过就打不开第二关——未解锁的关卡连题面都不会下发。<br>4. 上面是题面，${hint}猜对后自动跳下一层。`
  );
}

async function loadStatus() {
  const res = await fetch("/api/status", { credentials: "same-origin" });
  const s = await res.json();
  const first = state === null;
  if (!first && s.global_level > state.global_level) {
    banner(`全网已过${levelName(state.global_level)}，${levelName(s.global_level)}开始`);
    loadProgress();
  }
  state = s;
  renderStats(s);
  if (first) {
    describeRules();
    await loadLevelCard();
    await loadProgress();
    addMessage("assistant", `你在${levelName(s.level)}，题面在上方。全网已经猜了 <b>${fmt(s.total_guesses)}</b> 次。`);
    if (UI.poll_interval_ms > 0) setInterval(poll, UI.poll_interval_ms);
  }
  return s;
}

async function poll() {
  if (pending || document.hidden) return;
  const res = await fetch("/api/status", { credentials: "same-origin" });
  const s = await res.json();
  if (s.global_level > state.global_level || s.level > state.level) {
    state = s;
    renderStats(s);
    banner(`进入${levelName(s.level)}，正在跳转…`);
    if (UI.auto_redirect !== false) setTimeout(() => (location.href = levelUrl(s.level)), 800);
    return;
  }
  state = s;
  renderStats(s);
}

async function submitGuess() {
  if (pending || (view && !view.is_playable)) return;
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
        `<b>正确 ✅</b> ${data.advanced ? `全网自动进入${levelName(data.next_level)}。` : data.finished ? "这是最后一关，你已通关！" : "本关别人早已破解，你按自己的进度继续。"}`,
        chips
      );
      if (data.finished) {
        banner("全部通关");
      } else if (UI.auto_redirect !== false && !data.advanced) {
        banner(`进入${levelName(state.level)}，正在跳转…`);
        setTimeout(() => (location.href = levelUrl(state.level)), 900);
      } else if (UI.auto_redirect !== false) {
        banner(`进入${levelName(data.next_level)}，正在跳转…`);
        setTimeout(() => (location.href = levelUrl(data.next_level)), 900);
      } else {
        banner(`进入${levelName(data.next_level || state.level)}`);
        await loadLevelCard();
        await loadProgress();
      }
    } else if (data.ok) {
      addMessage("result-bad", `${data.message}${data.length_matches ? "（长度对了）" : ""}`, chips);
    } else {
      addMessage("result-bad", data.message || "提交失败");
    }
  } catch (err) {
    addMessage("result-bad", `网络错误：${err.message}`);
  } finally {
    pending = false;
    if (!input.disabled) sendBtn.disabled = false;
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
