(function () {
  const CN = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
  const name = (n) => `第${n <= 10 ? CN[n] : n}关`;
  const url = (n) => (n === 1 ? "/" : `/level/${n}`);

  const box = document.querySelector(".bxw");
  if (!box) return;
  const cfg = box.dataset;
  const level = Number(cfg.level);
  const playable = cfg.playable === "1";
  const auto = cfg.autoRedirect !== "0";
  const form = box.querySelector(".bxw-form");
  const input = box.querySelector(".bxw-input");
  const btn = box.querySelector(".bxw-btn");
  const msg = box.querySelector(".bxw-msg");
  const totalEl = box.querySelector(".bxw-total");
  const ownEl = box.querySelector(".bxw-own");
  const lenEl = box.querySelector(".bxw-len");
  let state = null;
  let busy = false;

  box.querySelector(".bxw-level").textContent = name(level);
  if (!playable) {
    box.classList.add("locked");
    input.disabled = true;
    btn.disabled = true;
    say('这一关已经过去了，<a href="#" class="bxw-back">回到当前关卡</a>', "");
  }

  function backLink(target) {
    box.querySelectorAll(".bxw-back").forEach((a) => (a.href = url(target)));
  }

  function say(html, kind) {
    msg.className = `bxw-msg ${kind || ""}`.trim();
    msg.innerHTML = html;
  }

  function paint(s) {
    state = s;
    totalEl.textContent = Number(s.total_guesses).toLocaleString("zh-CN");
    ownEl.textContent = Number(s.level_guesses).toLocaleString("zh-CN");
    if (s.password_length) lenEl.textContent = s.password_length;
    if (!playable) backLink(s.level);
  }

  async function refresh() {
    if (busy || document.hidden) return;
    try {
      const s = await fetch("/api/status", { credentials: "same-origin" }).then((r) => r.json());
      if (state && (s.global_level > state.global_level || s.level > state.level)) {
        paint(s);
        if (!playable) return;
        say(`全网已进入${name(s.level)}，正在跳转…`, "ok");
        if (auto) setTimeout(() => (location.href = url(s.level)), 800);
        return;
      }
      paint(s);
    } catch (err) {
      /* 网络抖动就下一次轮询再补 */
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!playable || busy) return;
    const guess = input.value.trim();
    if (!guess) return;
    busy = true;
    btn.disabled = true;
    say("校验中…", "");
    try {
      const data = await fetch("/api/guess", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ guess }),
      }).then((r) => r.json());
      if (data.status) paint(data.status);
      if (data.correct) {
        const target = data.status ? data.status.level : level + 1;
        say(`✅ 正确！${data.finished ? "这是最后一关，你已通关。" : `正在进入${name(target)}…`}`, "ok");
        if (auto && !data.finished) setTimeout(() => (location.href = url(target)), 900);
      } else if (data.ok) {
        say(`❌ ${data.message || "答案不对"}${data.length_matches ? "（长度是对的）" : ""}`, "bad");
      } else {
        say(`❌ ${data.message || "提交失败"}`, "bad");
      }
    } catch (err) {
      say(`❌ 网络错误：${err.message}`, "bad");
    } finally {
      busy = false;
      if (playable) btn.disabled = false;
      input.focus();
    }
  });

  refresh();
  const ms = Math.max(2000, Number(cfg.pollMs) || 5000);
  setInterval(refresh, ms);
})();
