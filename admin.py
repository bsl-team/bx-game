"""super_admin 后台：密码登录、改答案、生成/编辑关卡页、切关/重置、查看猜测记录。

公开站点保持纯 HTML；这里只挂 /super_admin 下的表单页。
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
from ipstats import is_loopback

bp = Blueprint("super_admin", __name__, url_prefix="/super_admin")

STEP_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
</head>
<body>
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


@bp.before_request
def _record_admin_access():
    ip = str(request.remote_addr or "")
    loop = is_loopback(ip)
    _ext("bx_ipstats").record(ip, loopback=loop, admin=True)


def _loop_messages() -> list[str]:
    info = _ext("bx_ipstats").loopback_summary()
    messages = []
    if info["loop_6h"] > 0:
        messages.append(
            f"近 6 小时有 {info['loop_6h']} 次来自本机（127.0.0.1）的访问，"
            f"其中管理后台 {info['loop_admin_6h']} 次。"
        )
    if info["all_loopback_6h"]:
        messages.append("近 6 小时所有请求都来自本机，请确认反向代理和端口暴露是否正常。")
    return messages


def authed() -> bool:
    if not session.get("admin"):
        return False
    auth: AdminAuth = _ext("bx_admin")
    if not auth.configured:
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
            return redirect(url_for("super_admin.panel"))
        return view(*args, **kwargs)

    return wrapper


def require_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not authed():
            flash("请先登录。", "error")
            return redirect(url_for("super_admin.panel"))
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
        body=body,
    )
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(source, encoding="utf-8")
    os.replace(tmp, path)
    return path


def default_page_source(level: int) -> str:
    return (
        "<!doctype html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>Step {level}</title>\n</head>\n<body>\n"
        "<h3>这里写题面</h3>\n<p>这里写提示。</p>\n"
        "<!--bx-controls-->\n</body>\n</html>\n"
    )


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
    settings = _ext("bx_settings")
    themes = _ext("bx_themes")
    pages = _ext("bx_pages")
    if logged_in:
        store = _ext("bx_store")
        data = store.snapshot()
        theme_list = themes.all()
        for row in data["levels"]:
            row["page_exists"] = level_page(row["level"]).is_file()
            row["page_url"] = f"/game/{row['level']}"
            style = pages.get(row["level"])
            row["theme"] = style.get("theme") or ""
            row["theme_label"] = theme_list.get(row["theme"], {}).get("label", "默认")
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
        data["ipstats"] = _ext("bx_ipstats").summary()
        data["gate"] = _ext("bx_gate").snapshot()
        data["loopback"] = _ext("bx_ipstats").loopback_summary()
        data["notice"] = _loop_messages()
        recent = list(reversed(log[-30:]))
    return render_template(
        "super_admin.html",
        authed=logged_in,
        configured=auth.configured,
        csrf=csrf_token(),
        data=data,
        recent=recent,
        next_level=(int(data.get("total_levels", 0)) + 1 if data else 1),
        min_password_length=int(_ext("bx_cfg")["admin"].get("min_password_length", 8)),
        settings={
            "rate_count": settings.get_int("rate_limit_count", 1),
            "rate_window": settings.get_float("rate_limit_window", 0.5),
            "gate_wait_ms": settings.get_int("gate_wait_timeout_ms", 50),
            "default_theme": settings.get("default_theme", "chatgpt"),
        },
        themes=themes.all() if logged_in else {},
    )


@bp.post("/login")
@require_csrf
def login():
    auth: AdminAuth = _ext("bx_admin")
    limiter = _ext("bx_admin_limiter")
    settings = _ext("bx_settings")
    ip = str(request.remote_addr or "")
    loop = is_loopback(ip)
    if not loop:
        allowed, retry = limiter.check(
            ip,
            settings.get_int("rate_limit_admin_count", 1),
            settings.get_float("rate_limit_admin_window", 1.0),
        )
        if not allowed:
            flash(f"尝试太频繁，请 {max(1, round(retry))} 秒后再试。", "error")
            return redirect(url_for("super_admin.panel"))
    if not auth.configured:
        flash("还没有设置管理密码，请先在服务器上运行 python app.py admin-password", "error")
        return redirect(url_for("super_admin.panel"))
    if not auth.verify(request.form.get("password", "")):
        if not loop:
            delay = settings.get_float("admin_login_delay_ms", 0, 60_000) / 1000.0
            if delay > 0:
                time.sleep(delay)
        flash("管理密码不正确。", "error")
        return redirect(url_for("super_admin.panel"))
    session["admin"] = True
    session["admin_gen"] = auth.generation
    session["admin_at"] = time.time()
    session["csrf"] = secrets.token_urlsafe(24)
    flash("登录成功。", "ok")
    for message in _loop_messages():
        flash("提示：" + message, "error")
    return redirect(url_for("super_admin.panel"))


@bp.post("/logout")
@require_csrf
def logout():
    for key in ("admin", "admin_gen", "admin_at"):
        session.pop(key, None)
    session["csrf"] = secrets.token_urlsafe(24)
    flash("已退出。", "ok")
    return redirect(url_for("super_admin.panel"))


@bp.post("/answer")
@require_csrf
@require_login
def answer():
    try:
        level = _parse_level(request.form.get("level"))
        digest = _ext("bx_book").set_answer(level, request.form.get("answer", ""))
        _ext("bx_store").reset_solved(level)
    except (TypeError, ValueError) as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("super_admin.panel"))
    message = f"第 {level} 关答案已保存（{digest[:12]}…，服务器只存 SHA-256），这一关重新开放。"
    flash(message + _level_exists_warning(level), "error" if not level_page(level).is_file() else "ok")
    return redirect(url_for("super_admin.panel"))


@bp.post("/level")
@require_csrf
@require_login
def level():
    form = request.form
    try:
        level = _parse_level(form.get("level"))
    except (TypeError, ValueError) as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("super_admin.panel"))

    if form.get("answer"):
        try:
            _ext("bx_book").set_answer(level, form.get("answer", ""))
            _ext("bx_store").reset_solved(level)
        except (TypeError, ValueError) as exc:
            flash(f"答案保存失败：{exc}", "error")
            return redirect(url_for("super_admin.panel"))
    elif form.get("make_page") != "1":
        flash("答案不能为空。", "error")
        return redirect(url_for("super_admin.panel"))

    if form.get("make_page") == "1":
        existing = level_page(level).exists()
        if existing and form.get("overwrite") != "1":
            flash("页面已存在，这次没有覆盖，只保存了答案。要重写页面请勾选「覆盖」。", "error")
            return redirect(url_for("super_admin.panel"))
        try:
            write_level_page(level, form.get("title", ""), form.get("body", ""))
        except OSError as exc:
            flash(f"页面写入失败：{exc}", "error")
            return redirect(url_for("super_admin.panel"))
        flash(f"第 {level} 关已保存，页面 copyspace/step{level}.html 已生成。", "ok")
    else:
        flash(f"第 {level} 关答案已保存。" + _level_exists_warning(level),
              "ok" if level_page(level).is_file() else "error")
    return redirect(url_for("super_admin.panel"))


@bp.get("/edit/<int:level>")
def edit_page(level: int):
    if not authed():
        flash("请先登录。", "error")
        return redirect(url_for("super_admin.panel"))
    try:
        level = _parse_level(level)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("super_admin.panel"))
    page = level_page(level)
    if page.is_file():
        source = page.read_text(encoding="utf-8", errors="replace")
        page_exists = True
    else:
        source = default_page_source(level)
        page_exists = False
    return render_template(
        "super_admin_edit.html",
        authed=True,
        configured=True,
        csrf=csrf_token(),
        level=level,
        source=source,
        page_exists=page_exists,
        page_url=f"/game/{level}",
        answer_hash=_ext("bx_book").hash_for(level),
        level_style=_ext("bx_pages").get(level),
        themes=_ext("bx_themes").all(),
        default_theme=_ext("bx_settings").get("default_theme", "chatgpt"),
    )


@bp.post("/page")
@require_csrf
@require_login
def save_page():
    try:
        level = _parse_level(request.form.get("level"))
    except (TypeError, ValueError) as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("super_admin.panel"))
    source = request.form.get("source", "")
    if not source.strip():
        flash("源码不能为空。", "error")
        return redirect(url_for("super_admin.edit_page", level=level))
    if len(source) > 1_000_000:
        flash("源码太长（上限 1 MB）。", "error")
        return redirect(url_for("super_admin.edit_page", level=level))
    path = level_page(level)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".html.tmp")
        tmp.write_text(source, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        flash(f"写入失败：{exc}", "error")
        return redirect(url_for("super_admin.edit_page", level=level))
    flash(f"第 {level} 关源码已保存（{len(source)} 字符）。", "ok")
    if request.form.get("then") == "open":
        return redirect(f"/game/{level}")
    return redirect(url_for("super_admin.edit_page", level=level))


@bp.post("/goto")
@require_csrf
@require_login
def goto():
    try:
        level = _parse_level(request.form.get("level"))
    except (TypeError, ValueError) as exc:
        flash(f"切换失败：{exc}", "error")
        return redirect(url_for("super_admin.panel"))
    _ext("bx_store").goto(level)
    total = _ext("bx_book").total_levels()
    if level > total:
        flash(f"全网当前关卡已切到第 {level} 关，但这一关还没有答案，玩家会卡住。", "error")
    else:
        flash(f"全网当前关卡已切到第 {level} 关。", "ok")
    return redirect(url_for("super_admin.panel"))


@bp.post("/reset")
@require_csrf
@require_login
def reset():
    _ext("bx_store").reset()
    flash("已重置：回到第 1 关，计数清零，答案保留。", "ok")
    return redirect(url_for("super_admin.panel"))


@bp.post("/password")
@require_csrf
@require_login
def password():
    auth: AdminAuth = _ext("bx_admin")
    if not auth.verify(request.form.get("current", "")):
        flash("当前密码不正确。", "error")
        return redirect(url_for("super_admin.panel"))
    if request.form.get("new", "") != request.form.get("confirm", ""):
        flash("两次输入的新密码不一致。", "error")
        return redirect(url_for("super_admin.panel"))
    try:
        auth.set_password(request.form.get("new", ""))
    except ValueError as exc:
        flash(f"修改失败：{exc}", "error")
        return redirect(url_for("super_admin.panel"))
    for key in ("admin", "admin_gen", "admin_at"):
        session.pop(key, None)
    session["csrf"] = secrets.token_urlsafe(24)
    flash("管理密码已修改，请重新登录。", "ok")
    return redirect(url_for("super_admin.panel"))


@bp.post("/settings")
@require_csrf
@require_login
def save_settings():
    settings = _ext("bx_settings")
    try:
        count = int(request.form.get("rate_count", ""))
        window = float(request.form.get("rate_window", ""))
        inflight = int(request.form.get("gate_inflight", ""))
        wait_ms = int(request.form.get("gate_wait_ms", ""))
        if not 1 <= count <= 100_000:
            raise ValueError("次数需在 1-100000 之间")
        if not 0.5 <= window <= 3600:
            raise ValueError("窗口需在 0.5-3600 秒之间")
        if not 1 <= inflight <= 10_000:
            raise ValueError("并发上限需在 1-10000 之间")
        if not 50 <= wait_ms <= 60_000:
            raise ValueError("排队等待需在 50-60000 毫秒之间")
    except (TypeError, ValueError) as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("super_admin.panel"))
    settings.update({
        "rate_limit_count": count,
        "rate_limit_window": window,
        "gate_max_inflight": inflight,
        "gate_wait_timeout_ms": wait_ms,
    })
    _ext("bx_gate").configure(max_inflight=inflight)
    flash(f"已保存：每 IP {window:g} 秒最多 {count} 次，回环地址不限。", "ok")
    return redirect(url_for("super_admin.panel"))


@bp.get("/themes")
def theme_panel():
    if not authed():
        flash("请先登录。", "error")
        return redirect(url_for("super_admin.panel"))
    themes = _ext("bx_themes")
    edit_name = request.args.get("edit") or str(_ext("bx_settings").get("default_theme") or "")
    theme_list = themes.all()
    if edit_name not in theme_list:
        edit_name = next(iter(theme_list))
    return render_template(
        "super_admin_themes.html",
        authed=True,
        configured=True,
        csrf=csrf_token(),
        themes=theme_list,
        edit_name=edit_name,
        edit_theme=theme_list[edit_name],
        default_theme=_ext("bx_settings").get("default_theme", "chatgpt"),
        page_styles=_ext("bx_pages").all(),
        total_levels=_ext("bx_book").total_levels(),
    )


@bp.post("/themes/save")
@require_csrf
@require_login
def save_theme():
    themes = _ext("bx_themes")
    name = str(request.form.get("name", "")).strip()
    label = str(request.form.get("label", "")).strip()
    css = request.form.get("css", "")
    try:
        if request.form.get("create") == "1":
            new_name = str(request.form.get("new_name", "")).strip()
            themes.create(new_name, label, css)
            flash(f"主题 {new_name} 已创建。", "ok")
            name = new_name
        else:
            themes.save(name, label, css)
            flash(f"主题 {name} 源码已保存。", "ok")
    except ValueError as exc:
        flash(f"保存失败：{exc}", "error")
    return redirect(url_for("super_admin.theme_panel", edit=name or None))


@bp.post("/themes/default")
@require_csrf
@require_login
def set_default_theme():
    name = str(request.form.get("name", "")).strip()
    if name not in _ext("bx_themes").all():
        flash("主题不存在。", "error")
        return redirect(url_for("super_admin.theme_panel"))
    _ext("bx_settings").update({"default_theme": name})
    flash(f"默认主题已设为 {name}。", "ok")
    return redirect(url_for("super_admin.theme_panel", edit=name))


@bp.post("/themes/reset")
@require_csrf
@require_login
def reset_theme():
    name = str(request.form.get("name", "")).strip()
    try:
        _ext("bx_themes").reset(name)
        flash(f"已恢复内置主题 {name} 的默认源码。", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("super_admin.theme_panel", edit=name or None))


@bp.post("/themes/delete")
@require_csrf
@require_login
def delete_theme():
    name = str(request.form.get("name", "")).strip()
    try:
        _ext("bx_themes").delete(name)
        settings = _ext("bx_settings")
        if settings.get("default_theme") == name:
            settings.update({"default_theme": "chatgpt"})
        flash(f"主题 {name} 已删除。", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("super_admin.theme_panel"))


@bp.post("/background")
@require_csrf
@require_login
def background():
    try:
        level = _parse_level(request.form.get("level"))
    except (TypeError, ValueError) as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("super_admin.panel"))
    theme = str(request.form.get("theme", "")).strip()
    if theme and theme not in _ext("bx_themes").all():
        flash("这个主题不存在。", "error")
        return redirect(url_for("super_admin.edit_page", level=level))
    try:
        _ext("bx_pages").set(level, theme, request.form.get("css", ""))
    except ValueError as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("super_admin.edit_page", level=level))
    flash(f"第 {level} 关背景已保存" + (f"（主题 {theme}）" if theme else "（跟随默认主题）") + "。", "ok")
    return redirect(url_for("super_admin.edit_page", level=level))
