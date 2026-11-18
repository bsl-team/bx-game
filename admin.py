"""管理后台：密码登录、改答案、生成关卡页、切关/重置、查看猜测记录。

公开站点保持纯 HTML；这里只挂 /admin 下的表单页。
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

from game import ROOT, now_iso

bp = Blueprint("admin", __name__, url_prefix="/admin")

STEP_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="../hash/hashp.js"></script>
<style>html {{ visibility: hidden; }}</style>
<script src="guard.js"></script>
</head>
<body>
<script>CopyspaceGuard.enter({level});</script>
<h2>请输入第 {level} 层密码：</h2>
<input type="password" id="pwd">
<button onclick="next()">进入下一步</button>
<p id="msg"></p>

<script>
async function next() {{
  const pwd = document.getElementById("pwd").value;
  await CopyspaceGuard.submit({level}, pwd);
}}
</script>

{body}
</body>
</html>
"""


class AdminAuth:
    """data/admin.json：PBKDF2 哈希；文件一变就重载，CLI 改密码不用重启。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.path = ROOT / str(cfg["admin"].get("credentials_file", "data/admin.json"))
        self.lock = threading.Lock()
        self._mtime: float | None = None
        self.data: dict = {}
        self._refresh()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) and data.get("version") == 1 else {}

    def _refresh(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self.data = self._read()
            self._mtime = mtime
        return self.data

    def _iterations(self) -> int:
        return max(1000, int(self.cfg["admin"].get("hash_iterations", 200000)))

    @staticmethod
    def _hash(password: str, salt: str, iterations: int) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
        ).hex()

    @property
    def configured(self) -> bool:
        data = self._refresh()
        return bool(data.get("hash") and data.get("salt"))

    @property
    def generation(self) -> int:
        return int(self._refresh().get("generation", 0))

    def verify(self, password: str) -> bool:
        data = self._refresh()
        if not (data.get("hash") and data.get("salt")):
            return False
        try:
            got = self._hash(str(password or ""), data["salt"], int(data.get("iterations", 0)))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(got, str(data["hash"]))

    def set_password(self, password: str) -> None:
        password = str(password or "")
        minimum = int(self.cfg["admin"].get("min_password_length", 8))
        if len(password) < minimum:
            raise ValueError(f"管理密码至少 {minimum} 位")
        if len(password) > 128:
            raise ValueError("管理密码最长 128 位")
        salt = secrets.token_hex(16)
        iterations = self._iterations()
        record = {
            "version": 1,
            "algorithm": "pbkdf2_sha256",
            "iterations": iterations,
            "salt": salt,
            "hash": self._hash(password, salt, iterations),
            "generation": self.generation + 1,
            "updated_at": now_iso(),
        }
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            if os.name == "posix":
                os.chmod(self.path, 0o600)
            self.data = record
            self._mtime = self.path.stat().st_mtime


# -- session / CSRF --------------------------------------------------------
def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf"] = token
    return token


def csrf_ok() -> bool:
    expected = session.get("csrf")
    sent = request.form.get("csrf", "")
    return bool(expected) and hmac.compare_digest(str(sent).encode(), str(expected).encode())


def _ext(name: str):
    return current_app.extensions[name]


def authed() -> bool:
    auth: AdminAuth = _ext("bx_admin")
    if not auth.configured or not session.get("admin"):
        return False
    if int(session.get("admin_gen", -1)) != auth.generation:
        session.pop("admin", None)
        session.pop("admin_at", None)
        return False
    idle = int(_ext("bx_cfg")["admin"].get("session_max_age_seconds", 1800))
    if idle > 0 and time.time() - float(session.get("admin_at", 0)) > idle:
        session.pop("admin", None)
        session.pop("admin_at", None)
        return False
    session["admin_at"] = time.time()
    return True


def require_csrf(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not csrf_ok():
            flash("页面已过期，请重试。", "error")
            return redirect(url_for("admin.panel"))
        return view(*args, **kwargs)

    return wrapper


def require_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not authed():
            flash("请先登录。", "error")
            return redirect(url_for("admin.panel"))
        return view(*args, **kwargs)

    return wrapper


# -- level pages -----------------------------------------------------------
def copyspace_dir() -> Path:
    return ROOT / str(_ext("bx_cfg")["game"].get("copyspace_dir", "copyspace"))


def level_page(level: int) -> Path:
    return copyspace_dir() / f"step{int(level)}.html"


def write_level_page(level: int, title: str, body: str) -> Path:
    path = level_page(level)
    path.parent.mkdir(parents=True, exist_ok=True)
    source = STEP_TEMPLATE.format(
        title=html.escape(title or f"Step {int(level)}", quote=True),
        level=int(level),
        body=body,
    )
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(source, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _parse_level(raw) -> int:
    level = int(raw)
    if not 1 <= level <= 999:
        raise ValueError("关卡号需在 1-999 之间")
    return level


def _level_exists_warning(level: int) -> str:
    if not level_page(level).is_file():
        return f" 注意：copyspace/step{level}.html 不存在，玩家会看到 404。"
    return ""


# -- routes ----------------------------------------------------------------
@bp.get("/")
def panel():
    auth: AdminAuth = _ext("bx_admin")
    logged_in = authed()
    data = {}
    recent: list[dict] = []
    if logged_in:
        store = _ext("bx_store")
        data = store.snapshot()
        for row in data["levels"]:
            row["page_exists"] = level_page(row["level"]).is_file()
            row["page_url"] = f"/copyspace/step{row['level']}.html"
        log = data.get("guess_log", [])
        now = datetime.now(timezone.utc).timestamp()
        recent_hour = 0
        for entry in log:
            try:
                if datetime.fromisoformat(entry["at"]).timestamp() > now - 3600:
                    recent_hour += 1
            except (KeyError, ValueError, TypeError):
                continue
        data["guesses_last_hour"] = recent_hour
        data["distinct_players"] = len({e.get("pid") for e in log if e.get("pid")})
        data["answers_file"] = str(_ext("bx_book").path)
        recent = list(reversed(log[-30:]))
    return render_template(
        "admin.html",
        authed=logged_in,
        configured=auth.configured,
        csrf=csrf_token(),
        data=data,
        recent=recent,
        next_level=(int(data.get("total_levels", 0)) + 1 if data else 1),
        min_password_length=int(_ext("bx_cfg")["admin"].get("min_password_length", 8)),
    )


@bp.post("/login")
@require_csrf
def login():
    auth: AdminAuth = _ext("bx_admin")
    limiter = _ext("bx_admin_limiter")
    allowed, retry = limiter.check(f"login:{request.remote_addr}")
    if not allowed:
        flash(f"尝试太频繁，请 {max(1, round(retry))} 秒后再试。", "error")
        return redirect(url_for("admin.panel"))
    if not auth.configured:
        flash("还没有设置管理密码，请先在服务器上运行 python app.py admin-password", "error")
        return redirect(url_for("admin.panel"))
    if not auth.verify(request.form.get("password", "")):
        flash("管理密码不正确。", "error")
        return redirect(url_for("admin.panel"))
    session["admin"] = True
    session["admin_gen"] = auth.generation
    session["admin_at"] = time.time()
    session["csrf"] = secrets.token_urlsafe(24)
    flash("登录成功。", "ok")
    return redirect(url_for("admin.panel"))


@bp.post("/logout")
@require_csrf
def logout():
    for key in ("admin", "admin_gen", "admin_at"):
        session.pop(key, None)
    session["csrf"] = secrets.token_urlsafe(24)
    flash("已退出。", "ok")
    return redirect(url_for("admin.panel"))


@bp.post("/answer")
@require_csrf
@require_login
def answer():
    try:
        level = _parse_level(request.form.get("level"))
        password = _ext("bx_book").set_answer(level, request.form.get("answer", ""))
        _ext("bx_store").reset_solved(level)
    except (TypeError, ValueError) as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("admin.panel"))
    message = f"第 {level} 关答案已保存（{len(password)} 个字符），这一关重新开放。"
    flash(message + _level_exists_warning(level), "error" if not level_page(level).is_file() else "ok")
    return redirect(url_for("admin.panel"))


@bp.post("/level")
@require_csrf
@require_login
def level():
    form = request.form
    try:
        level = _parse_level(form.get("level"))
    except (TypeError, ValueError) as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("admin.panel"))

    if form.get("answer"):
        try:
            _ext("bx_book").set_answer(level, form.get("answer", ""))
            _ext("bx_store").reset_solved(level)
        except (TypeError, ValueError) as exc:
            flash(f"答案保存失败：{exc}", "error")
            return redirect(url_for("admin.panel"))
    elif form.get("make_page") != "1":
        flash("答案不能为空。", "error")
        return redirect(url_for("admin.panel"))

    if form.get("make_page") == "1":
        existing = level_page(level).exists()
        if existing and form.get("overwrite") != "1":
            flash("页面已存在，这次没有覆盖，只保存了答案。要重写页面请勾选「覆盖」。", "error")
            return redirect(url_for("admin.panel"))
        try:
            write_level_page(level, form.get("title", ""), form.get("body", ""))
        except OSError as exc:
            flash(f"页面写入失败：{exc}", "error")
            return redirect(url_for("admin.panel"))
        flash(f"第 {level} 关已保存，页面 copyspace/step{level}.html 已生成。", "ok")
    else:
        flash(f"第 {level} 关答案已保存。" + _level_exists_warning(level),
              "ok" if level_page(level).is_file() else "error")
    return redirect(url_for("admin.panel"))


@bp.post("/goto")
@require_csrf
@require_login
def goto():
    try:
        level = _parse_level(request.form.get("level"))
    except (TypeError, ValueError) as exc:
        flash(f"切换失败：{exc}", "error")
        return redirect(url_for("admin.panel"))
    _ext("bx_store").goto(level)
    total = _ext("bx_book").total_levels()
    if level > total:
        flash(f"全网当前关卡已切到第 {level} 关，但这一关还没有答案，玩家会卡住。", "error")
    else:
        flash(f"全网当前关卡已切到第 {level} 关。", "ok")
    return redirect(url_for("admin.panel"))


@bp.post("/reset")
@require_csrf
@require_login
def reset():
    _ext("bx_store").reset()
    flash("已重置：回到第 1 关，计数清零，答案保留。", "ok")
    return redirect(url_for("admin.panel"))


@bp.post("/password")
@require_csrf
@require_login
def password():
    auth: AdminAuth = _ext("bx_admin")
    if not auth.verify(request.form.get("current", "")):
        flash("当前密码不正确。", "error")
        return redirect(url_for("admin.panel"))
    if request.form.get("new", "") != request.form.get("confirm", ""):
        flash("两次输入的新密码不一致。", "error")
        return redirect(url_for("admin.panel"))
    try:
        auth.set_password(request.form.get("new", ""))
    except ValueError as exc:
        flash(f"修改失败：{exc}", "error")
        return redirect(url_for("admin.panel"))
    for key in ("admin", "admin_gen", "admin_at"):
        session.pop(key, None)
    session["csrf"] = secrets.token_urlsafe(24)
    flash("管理密码已修改，请重新登录。", "ok")
    return redirect(url_for("admin.panel"))
