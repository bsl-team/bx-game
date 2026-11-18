(function () {
  "use strict";

  function revealPage() {
    document.documentElement.style.visibility = "visible";
  }

  function showError(text) {
    var el = document.getElementById("msg") || document.getElementById("result");
    if (el) { el.textContent = text; }
  }

  function goTo(path) {
    if (typeof path === "string" && path.charAt(0) === "/") {
      location.replace(path);
    } else {
      location.replace("/");
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
        revealPage();
        showError(data.message || "无法读取进度。");
        return false;
      }
      if (!Number.isInteger(data.step)) {
        return goTo("/");
      }
      if (step > data.step) {
        goTo("/step/" + data.step);
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
      showError("请输入密码。");
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
        setTimeout(function () { goTo("/step/" + data.currentStep); }, 700);
        return false;
      }
      showError(data.message || "密码不对。");
      return false;
    } catch (err) {
      showError("提交失败：" + err.message);
      return false;
    }
  }

  window.MazeGuard = Object.freeze({ enter: enter, submit: submit });
})();
