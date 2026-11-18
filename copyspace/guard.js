(function () {
  "use strict";

  for (var i = 2; i <= 32; i++) {
    try { sessionStorage.removeItem("token_step" + i); } catch (err) {}
  }
  try { sessionStorage.removeItem("copyspace_progress_v2"); } catch (err) {}

  function pageFor(step) {
    return "/copyspace/step" + step + ".html";
  }

  function revealPage() {
    document.documentElement.style.visibility = "visible";
  }

  function showError(text) {
    var el = document.getElementById("msg") || document.getElementById("result");
    if (el) { el.textContent = text; }
  }

  function goTo(path) {
    if (typeof path === "string" && path.indexOf("/copyspace/") === 0) {
      location.replace(path);
    } else {
      location.replace(pageFor(1));
    }
  }

  async function readJson(res) {
    try {
      return await res.json();
    } catch (err) {
      return { message: "HTTP " + res.status };
    }
  }

  async function enter(step) {
    try {
      var res = await fetch("/api/progress", {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" }
      });
      var data = await readJson(res);
      if (!res.ok) {
        if (data.restart) { goTo(data.restart); return false; }
        revealPage();
        showError(data.message || "无法读取进度。");
        return false;
      }
      if (!Number.isInteger(data.step)) {
        goTo(pageFor(1));
        return false;
      }
      if (step > data.step) {
        goTo(pageFor(data.step));
        return false;
      }
      revealPage();
      return true;
    } catch (err) {
      revealPage();
      showError("无法连接服务器：" + err.message);
      return false;
    }
  }

  async function submit(step, password) {
    if (!password) {
      showError("请输入密码");
      return false;
    }
    try {
      var res = await fetch("/api/answer", {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ step: step, password: password })
      });
      var data = await readJson(res);
      if (res.ok && data.ok && data.next) {
        goTo(data.next);
        return true;
      }
      if (res.ok && data.ok && data.finished) {
        showError(data.message || "全部关卡已通过。");
        return true;
      }
      if (data.restart) {
        showError(data.message || "请回到入口。");
        setTimeout(function () { goTo(data.restart); }, 700);
        return false;
      }
      if (Number.isInteger(data.currentStep)) {
        showError(data.message || "你不在这一层。");
        setTimeout(function () { goTo(pageFor(data.currentStep)); }, 700);
        return false;
      }
      showError(data.message || "密码不对。");
      return false;
    } catch (err) {
      showError("连接服务器失败：" + err.message);
      return false;
    }
  }

  window.CopyspaceGuard = Object.freeze({ enter: enter, submit: submit });
})();
