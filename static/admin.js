const A = window.BX_ADMIN || {};
const $ = (id) => document.getElementById(id);
const levelName = (n) => `第${["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"][n] || n}关`;

let csrf = A.csrf;
let data = null;
let revealed = new Set();
let editor = null; // {level, mode}
let busy = false;

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "text") el.textContent = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function toast(message, kind) {
  const t = $("toast");
  t.textContent = message;
  t.className = `toast ${kind || ""}`;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

function fmtTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

function fmtBytes(n) {
  if (typeof n !== "number") return "–";
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;
}

async function request(path, payload) {
  const res = await fetch(`/admin/api/${path}`, {
    method: payload === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers: payload === undefined ? {} : { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" },
    body: payload === undefined ? undefined : JSON.stringify({ csrf, ...payload }),
  });
  let json = {};
  try {
    json = await res.json();
  } catch (err) {
    throw new Error(`服务端返回非 JSON（${res.status}）`);
  }
  if (json.csrf) csrf = json.csrf;
  if (!res.ok || json.ok === false) {
    const err = new Error(json.message || `请求失败（${res.status}）`);
    err.status = res.status;
    err.code = json.error;
    throw err;
  }
  return json;
}

// -- gate (login / first-time setup) --------------------------------------
function showGate(mode) {
  $("dashboard").hidden = true;
  $("gate").hidden = false;
  $("logout-btn").hidden = true;
  $("conn").textContent = mode === "setup" ? "需要初始化" : "未登录";
  $("conn").className = "pill bad";
  $("token-row").hidden = mode !== "setup";
  $("pw2-row").hidden = mode !== "setup";
  $("pw-label").textContent = mode === "setup" ? `设置管理密码（至少 ${A.minPasswordLength} 位）` : "管理密码";
  $("gate-title").textContent = mode === "setup" ? "首次进入：用 admin token 设置密码" : "管理员登录";
  $("gate-hint").textContent =
    mode === "setup"
      ? "token 在服务启动时打印在控制台，也写在 data/admin_token.txt；设置成功后 token 立即作废。"
      : "后续都用这个密码登录。忘记密码请在服务器执行 python app.py admin-reset 重新发放 token。";
  $("gate-submit").textContent = mode === "setup" ? "设置并登录" : "登录";
  $("gate-form").dataset.mode = mode;
  document.querySelectorAll("#gate input").forEach((i) => (i.value = ""));
  $("gate-msg").textContent = "";
}

$("gate-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const mode = e.currentTarget.dataset.mode;
  const pw = $("pw-input").value;
  const msg = $("gate-msg");
  msg.className = "msg-line";
  msg.textContent = "提交中…";
  try {
    if (mode === "setup") {
      if ($("pw2-input").value !== pw) throw new Error("两次输入的密码不一致");
      await request("setup", { token: $("token-input").value.trim(), password: pw });
    } else {
      await request("login", { password: pw });
    }
    msg.textContent = "";
    enterDashboard();
  } catch (err) {
    msg.className = "msg-line bad";
    msg.textContent = err.message;
  }
});

function enterDashboard() {
  $("gate").hidden = true;
  $("dashboard").hidden = false;
  $("logout-btn").hidden = false;
  $("min-pw").textContent = A.minPasswordLength;
  fillCharsets($("add-charset"), ["", ...A.charsets]);
  refresh();
}

function fillCharsets(select, options) {
  select.replaceChildren(
    ...options.map((c) => h("option", { value: c, text: c || "跟随 config.json" }))
  );
}

// -- dashboard ------------------------------------------------------------
async function refresh() {
  busy = true;
  try {
    data = await request("overview");
    $("conn").textContent = "已登录";
    $("conn").className = "pill ok";
    renderKpis();
    renderLevels();
    renderLog();
  } catch (err) {
    if (err.status === 401) {
      showGate("login");
      return;
    }
    $("conn").textContent = "刷新失败";
    $("conn").className = "pill bad";
    toast(err.message, "bad");
  } finally {
    busy = false;
  }
}

function renderKpis() {
  const items = [
    ["当前关卡", levelName(data.current_level), true],
    ["全网总猜测", data.total_guesses.toLocaleString("zh-CN"), true],
    ["最近 1 小时", data.guesses_last_hour.toLocaleString("zh-CN")],
    ["已解开关卡", `${data.solved_levels} / ${data.levels.length}`],
    ["日志内玩家", data.distinct_players],
    ["状态文件", fmtBytes(data.state_size_bytes)],
  ];
  $("kpis").replaceChildren(
    ...items.map(([label, value, accent]) =>
      h("div", { class: `kpi${accent ? " accent" : ""}` }, h("div", { class: "k-label", text: label }), h("div", { class: "k-value", text: value }))
    )
  );
  $("reveal-note").textContent = data.reveal_passwords
    ? "明文密码保存在 data/state.json，只显示给登录后的管理员"
    : "admin.reveal_passwords=false：明文已不显示、也不再写入文件";
}

function passwordCell(row) {
  if (!data.reveal_passwords) return h("td", {}, h("span", { class: "muted", text: "已隐藏（reveal_passwords=false）" }));
  if (!row.password) {
    return h("td", {}, h("span", { class: "muted", text: row.password_stored ? "已隐藏" : "未保存明文（历史关卡）" }));
  }
  const shown = revealed.has(row.level);
  const value = h("span", { class: "mono", text: shown ? row.password : "•".repeat(Math.max(6, row.password.length)) });
  return h(
    "td",
    {},
    value,
    h("button", {
      class: "btn mini ghost",
      type: "button",
      text: shown ? "隐藏" : "显示",
      onclick: () => {
        if (shown) revealed.delete(row.level);
        else revealed.add(row.level);
        renderLevels();
      },
    }),
    h("button", {
      class: "btn mini ghost",
      type: "button",
      text: "复制",
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(row.password);
          toast(`已复制第 ${row.level} 关密码`);
        } catch (err) {
          toast("浏览器不允许剪贴板读取", "bad");
        }
      },
    })
  );
}

function statusCell(row) {
  const kids = [h("span", { class: `tag ${row.solved ? "solved" : "open"}`, text: row.solved ? "已破解" : "未破解" })];
  if (row.is_current) kids.push(h("span", { class: "tag current", text: "当前关" }));
  if (row.custom) kids.push(h("span", { class: "tag", text: "管理员自定义" }));
  if (row.solved_at) kids.push(h("div", { class: "muted", text: `${fmtTime(row.solved_at)} · ${row.solved_by || ""}` }));
  return h("td", {}, kids);
}

function editorRow(row) {
  const cols = 8;
  if (editor.mode === "set") {
    const input = h("input", { type: "text", placeholder: "新密码（不含空格，最长 64）", value: "" });
    return h("td", { colspan: cols }, h("div", { class: "editor-form" },
      h("label", { class: "field" }, h("span", { text: `第 ${row.level} 关明文密码` }), input),
      h("button", { class: "btn primary", type: "button", text: "保存", onclick: async () => {
        try {
          const r = await request("level/password", { level: row.level, password: input.value.trim() });
          toast(`${r.message}：新密码 ${r.password || "（未保存明文）"}，长度 ${r.length} 位`);
          editor = null;
          refresh();
        } catch (err) { toast(err.message, "bad"); }
      } }),
      h("button", { class: "btn ghost", type: "button", text: "取消", onclick: () => { editor = null; renderLevels(); } })
    ));
  }
  const length = h("input", { type: "number", min: 1, max: 64, value: row.length, placeholder: "长度" });
  const charset = h("select", {});
  fillCharsets(charset, ["", ...A.charsets]);
  if (row.charset) charset.value = row.charset;
  return h("td", { colspan: cols }, h("div", { class: "editor-form" },
    h("label", { class: "field narrow" }, h("span", { text: "长度" }), length),
    h("label", { class: "field" }, h("span", { text: "字符集" }), charset),
    h("button", { class: "btn primary", type: "button", text: "随机生成", onclick: async () => {
      try {
        const r = await request("level/regenerate", { level: row.level, length: length.value || null, charset: charset.value || null });
        toast(`第 ${r.level} 关新密码：${r.password || "（未保存明文）"} · ${r.length} 位`);
        editor = null;
        refresh();
      } catch (err) { toast(err.message, "bad"); }
    } }),
    h("button", { class: "btn ghost", type: "button", text: "取消", onclick: () => { editor = null; renderLevels(); } })
  ));
}

function renderLevels() {
  const tbody = $("levels-table").querySelector("tbody");
  tbody.replaceChildren(
    ...data.levels.map((row) => {
      const tr = h("tr", {},
        h("td", { class: "mono", text: `${row.level}` }),
        passwordCell(row),
        h("td", { text: row.length ?? "–" }),
        h("td", { class: "muted", text: row.charset || "–" }),
        h("td", { text: row.guesses.toLocaleString("zh-CN") }),
        statusCell(row),
        h("td", { class: "muted", text: fmtTime(row.password_set_at || row.created_at) }),
        h("td", { class: "col-actions" }, h("div", { class: "actions" },
          h("button", { class: "btn mini", type: "button", text: "改密码", onclick: () => { editor = { level: row.level, mode: "set" }; renderLevels(); } }),
          h("button", { class: "btn mini", type: "button", text: "随机重生成", onclick: () => { editor = { level: row.level, mode: "gen" }; renderLevels(); } }),
          h("button", { class: "btn mini ghost", type: "button", text: "清零本关", onclick: async () => {
            try {
              await request("counters/reset", { level: row.level });
              toast(`第 ${row.level} 关计数已清零`);
              refresh();
            } catch (err) { toast(err.message, "bad"); }
          } })
        ))
      );
      if (editor && editor.level === row.level) tr.classList.add("editing");
      const rows = [tr];
      if (editor && editor.level === row.level) rows.push(h("tr", { class: "editor" }, editorRow(row)));
      return rows;
    }).flat()
  );
}

function renderLog() {
  const tbody = $("log-table").querySelector("tbody");
  if (!data.guess_log.length) {
    tbody.replaceChildren(h("tr", {}, h("td", { colspan: 6, class: "muted", text: "还没有任何猜测记录" })));
    return;
  }
  tbody.replaceChildren(
    ...data.guess_log.map((e) =>
      h("tr", {},
        h("td", { class: "muted", text: fmtTime(e.at) }),
        h("td", { text: levelName(e.level) }),
        h("td", { class: "mono muted", text: (e.pid || "").slice(0, 8) }),
        h("td", { class: "mono", text: e.guess ?? "（未记录原文）" }),
        h("td", { text: e.length }),
        h("td", {}, h("span", { class: `tag ${e.correct ? "solved" : "open"}`, text: e.correct ? "命中" : "未中" }))
      )
    )
  );
}

// -- panel actions --------------------------------------------------------
$("refresh-btn").addEventListener("click", refresh);

$("logout-btn").addEventListener("click", async () => {
  try {
    await request("logout", {});
  } catch (err) { /* logging out even if the call fails */ }
  A.setupRequired = false;
  showGate("login");
});

$("add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await request("levels/add", {
      count: Number($("add-count").value) || 1,
      length: $("add-length").value ? Number($("add-length").value) : null,
      charset: $("add-charset").value || null,
      password: $("add-password").value.trim() || null,
    });
    toast(r.message + "：" + r.added.map((l) => `${levelName(l.level)}=${l.password || l.length + " 位"}`).join("、"));
    $("add-password").value = "";
    editor = null;
    refresh();
  } catch (err) { toast(err.message, "bad"); }
});

$("jump-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await request("round/current", { level: Number($("jump-level").value) });
    toast(r.message);
    refresh();
  } catch (err) { toast(err.message, "bad"); }
});

$("reset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const level = $("reset-level").value;
  const scope = level ? `第 ${level} 关` : "全部关卡和全网总次数";
  if (!confirm(`确认清零 ${scope} 的猜测计数？`)) return;
  try {
    await request("counters/reset", level ? { level: Number(level) } : {});
    toast("计数已清零");
    refresh();
  } catch (err) { toast(err.message, "bad"); }
});

$("pwd-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await request("change_password", { old_password: $("old-pw").value, new_password: $("new-pw").value });
    toast(r.message);
    $("old-pw").value = "";
    $("new-pw").value = "";
    showGate("login");
  } catch (err) { toast(err.message, "bad"); }
});

// -- boot -----------------------------------------------------------------
(async function boot() {
  try {
    const s = await request("session");
    csrf = s.csrf || csrf;
    if (s.authed) return enterDashboard();
    showGate(s.setup_required ? "setup" : "login");
  } catch (err) {
    $("conn").textContent = "连接失败";
    $("conn").className = "pill bad";
    showGate(A.setupRequired ? "setup" : "login");
    $("gate-msg").className = "msg-line bad";
    $("gate-msg").textContent = err.message;
  }
  const interval = (A.ui && A.ui.poll_interval_ms) || 5000;
  setInterval(() => {
    if (!$("dashboard").hidden && !editor && !document.hidden && !busy) refresh();
  }, Math.max(2000, interval));
})();
