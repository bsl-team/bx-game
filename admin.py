"""Admin panel: token bootstrap, password login, and round management APIs."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from functools import wraps

from flask import Blueprint, current_app, jsonify, render_template, request, session

from game import ALPHABETS, ROOT, build_hash, verify_secret

bp = Blueprint("admin", __name__, url_prefix="/admin")


class AdminAuth:
    """Admin credentials file: one-shot bootstrap token, then a PBKDF2 password hash."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        admin = cfg["admin"]
        self.path = ROOT / str(admin.get("credentials_file", "data/admin.json"))
        self.token_path = ROOT / str(admin.get("token_file", "data/admin_token.txt"))
        self.lock = threading.Lock()
        self.data = self._read()
        if not self.data.get("password") and not self.data.get("token"):
            self.data["token"] = secrets.token_urlsafe(16)
        self._save()

    def _iterations(self) -> int:
        return max(1000, int(self.cfg["admin"].get("hash_iterations", 200000)))

    def _read(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"token": None, "password": None, "generation": 1, "created_at": time.time()}

    def _save(self) -> None:
        """Persist credentials and keep the operator-facing token file in sync."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        token = self.data.get("token")
        if token and not self.password_set:
            self.token_path.write_text(token + "\n", encoding="utf-8")
            if os.name == "posix":
                os.chmod(self.token_path, 0o600)
        elif self.token_path.exists():
            try:
                self.token_path.unlink()
            except OSError:
                pass

    # -- state ------------------------------------------------------------
    def bootstrap_token(self) -> str | None:
        """Token to print at startup; None once the admin password is set."""
        with self.lock:
            if self.password_set:
                return None
            if not self.data.get("token"):
                self.data["token"] = secrets.token_urlsafe(16)
            self._save()
            return self.data["token"]

    @property
    def password_set(self) -> bool:
        return bool(self.data.get("password"))

    @property
    def generation(self) -> int:
        """Bumped whenever credentials change, so old admin cookies stop working."""
        return int(self.data.get("generation") or 1)

    @property
    def token(self) -> str | None:
        return self.data.get("token")

    def _drop_token(self) -> None:
        self.data["token"] = None

    # -- operations -------------------------------------------------------
    def check_token(self, candidate: str) -> bool:
        stored = self.data.get("token")
        if not stored or self.password_set:
            return False
        return secrets.compare_digest(str(stored), str(candidate or "").strip())

    def set_password(self, password: str) -> None:
        admin = self.cfg["admin"]
        minimum = int(admin.get("min_password_length", 8))
        if len(password) < minimum:
            raise ValueError(f"管理密码至少 {minimum} 位")
        if len(password) > 128:
            raise ValueError("管理密码最长 128 位")
        with self.lock:
            self.data["password"] = build_hash(password, "pbkdf2_sha256", self._iterations())
            self.data["generation"] = self.generation + 1
            self._drop_token()
            self._save()

    def check_password(self, candidate: str) -> bool:
        record = self.data.get("password")
        if not record:
            return False
        return verify_secret(str(candidate or ""), record)

    def rotate_token(self) -> str:
        """Forget the password and hand out a fresh bootstrap token."""
        with self.lock:
            self.data["password"] = None
            self.data["token"] = secrets.token_urlsafe(16)
            self.data["generation"] = self.generation + 1
            self._save()
            return self.data["token"]


def _ext(name):
    return current_app.extensions[name]


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf"] = token
    return token


def rotate_csrf() -> str:
    """Mint a fresh token whenever privilege changes, so a pre-set cookie can't be reused."""
    session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def csrf_ok() -> bool:
    if request.is_json:
        sent = request.headers.get("X-CSRF-Token") or str(body().get("csrf", ""))
    else:
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
    expected = session.get("csrf")
    return bool(expected) and secrets.compare_digest(str(sent), str(expected))


def require_csrf(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not csrf_ok():
            return jsonify({"ok": False, "error": "csrf", "message": "CSRF 校验失败，请刷新页面重试"}), 403
        return view(*args, **kwargs)

    return wrapper


def authed() -> bool:
    if not session.get("admin"):
        return False
    auth: AdminAuth = _ext("bx_admin")
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


def require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not csrf_ok():
            return jsonify({"ok": False, "error": "csrf", "message": "CSRF 校验失败，请刷新页面重试"}), 403
        if not authed():
            return jsonify({"ok": False, "error": "unauthorized", "message": "请先登录管理面板"}), 401
        return view(*args, **kwargs)

    return wrapper


def body() -> dict:
    return request.get_json(silent=True) or {}


@bp.get("/")
def panel():
    cfg = _ext("bx_cfg")
    auth: AdminAuth = _ext("bx_admin")
    session["csrf"] = csrf_token()
    return render_template(
        "admin.html",
        ui=cfg["ui"],
        authed=authed(),
        setup_required=not auth.password_set,
        csrf=session["csrf"],
        charsets=sorted(ALPHABETS),
        min_password_length=int(cfg["admin"].get("min_password_length", 8)),
    )


@bp.get("/api/session")
def api_session():
    auth: AdminAuth = _ext("bx_admin")
    return jsonify(
        {
            "ok": True,
            "authed": authed(),
            "setup_required": not auth.password_set,
            "csrf": csrf_token(),
        }
    )


@bp.post("/api/setup")
@require_csrf
def api_setup():
    auth: AdminAuth = _ext("bx_admin")
    limiter = _ext("bx_admin_limiter")
    key = f"setup:{request.remote_addr}"
    allowed, retry = limiter.check(key)
    if not allowed:
        return jsonify({"ok": False, "error": "rate_limited",
                        "message": f"尝试过多，请 {max(1, round(retry))} 秒后再试"}), 429
    payload = body()
    if not auth.check_token(payload.get("token", "")):
        return jsonify({"ok": False, "error": "bad_token", "message": "admin token 不正确或已失效"}), 401
    try:
        auth.set_password(str(payload.get("password", "")))
    except ValueError as exc:
        return jsonify({"ok": False, "error": "weak_password", "message": str(exc)}), 400
    session["admin"] = True
    session["admin_gen"] = auth.generation
    session["admin_at"] = time.time()
    return jsonify({"ok": True, "message": "管理密码已设置，token 已作废", "authed": True,
                    "csrf": rotate_csrf()})


@bp.post("/api/login")
@require_csrf
def api_login():
    auth: AdminAuth = _ext("bx_admin")
    limiter = _ext("bx_admin_limiter")
    key = f"login:{request.remote_addr}"
    allowed, retry = limiter.check(key)
    if not allowed:
        return jsonify({"ok": False, "error": "rate_limited",
                        "message": f"登录尝试过多，请 {max(1, round(retry))} 秒后再试"}), 429
    if not auth.password_set:
        return jsonify({"ok": False, "error": "setup_required", "message": "请先用 admin token 设置密码"}), 400
    if not auth.check_password(str(body().get("password", ""))):
        return jsonify({"ok": False, "error": "bad_password", "message": "管理密码不正确"}), 401
    session["admin"] = True
    session["admin_gen"] = auth.generation
    session["admin_at"] = time.time()
    return jsonify({"ok": True, "authed": True, "csrf": rotate_csrf(), "message": "登录成功"})


@bp.post("/api/logout")
@require_csrf
def api_logout():
    session.pop("admin", None)
    session.pop("admin_at", None)
    return jsonify({"ok": True, "authed": False, "csrf": rotate_csrf()})


@bp.post("/api/change_password")
@require_admin
def api_change_password():
    auth: AdminAuth = _ext("bx_admin")
    payload = body()
    if not auth.check_password(str(payload.get("old_password", ""))):
        return jsonify({"ok": False, "error": "bad_password", "message": "原密码不正确"}), 401
    try:
        auth.set_password(str(payload.get("new_password", "")))
    except ValueError as exc:
        return jsonify({"ok": False, "error": "weak_password", "message": str(exc)}), 400
    session.pop("admin", None)
    return jsonify({"ok": True, "message": "密码已修改，请重新登录", "csrf": rotate_csrf()})


@bp.get("/api/overview")
def api_overview():
    if not authed():
        return jsonify({"ok": False, "error": "unauthorized", "message": "请先登录"}), 401
    store = _ext("bx_store")
    return jsonify({"ok": True, "csrf": session.get("csrf"), "questions": store.admin_questions(),
                    **store.admin_overview()})


@bp.post("/api/questions/save")
@require_admin
def api_questions_save():
    payload = body()
    try:
        result = _ext("bx_store").admin_question_save(
            int(payload.get("index", 0)),
            str(payload.get("prompt", "")),
            str(payload.get("answer", "")),
            str(payload.get("hint", "")),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": "invalid", "message": str(exc)}), 400
    where = f"，第 {result['rebound_level']} 关已同步重绑" if result["rebound_level"] else "，该关尚未生成"
    return jsonify({"ok": True, "message": f"第 {result['index']} 题已保存{where}", **result})


@bp.post("/api/questions/delete")
@require_admin
def api_questions_delete():
    try:
        result = _ext("bx_store").admin_question_delete(int(body().get("index", 0)))
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": "invalid", "message": str(exc)}), 400
    return jsonify({"ok": True, "message": f"第 {result['index']} 题已删除，后面的题自动前移", **result})


@bp.post("/api/level/rebind")
@require_admin
def api_level_rebind():
    try:
        result = _ext("bx_store").admin_rebind_level(int(body().get("level", 0)))
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": "invalid", "message": str(exc)}), 400
    except KeyError:
        return jsonify({"ok": False, "error": "no_level", "message": "这一关还不存在"}), 404
    return jsonify({"ok": True, "message": f"第 {result['level']} 关已按题库重绑", **result})


@bp.post("/api/level/password")
@require_admin
def api_level_password():
    payload = body()
    try:
        result = _ext("bx_store").admin_set_password(int(payload.get("level", 0)), str(payload.get("password", "")))
    except (KeyError, TypeError, ValueError) as exc:
        return _store_error(exc)
    return jsonify({"ok": True, "message": f"第 {result['level']} 关密码已修改，该关重新开放", **result})


@bp.post("/api/level/regenerate")
@require_admin
def api_level_regenerate():
    payload = body()
    try:
        result = _ext("bx_store").admin_regenerate(
            int(payload.get("level", 0)),
            int(payload["length"]) if payload.get("length") else None,
            payload.get("charset") or None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _store_error(exc)
    return jsonify({"ok": True, "message": f"第 {result['level']} 关已随机重生成", **result})


@bp.post("/api/levels/add")
@require_admin
def api_levels_add():
    payload = body()
    try:
        result = _ext("bx_store").admin_add_levels(
            int(payload.get("count", 1)),
            int(payload["length"]) if payload.get("length") else None,
            payload.get("charset") or None,
            payload.get("password") or None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _store_error(exc)
    return jsonify({"ok": True, "message": f"新增 {len(result['added'])} 关", **result})


@bp.post("/api/round/current")
@require_admin
def api_round_current():
    try:
        result = _ext("bx_store").admin_set_current_level(int(body().get("level", 0)))
    except (KeyError, TypeError, ValueError) as exc:
        return _store_error(exc)
    return jsonify({"ok": True, "message": f"当前关卡已设为第 {result['current_level']} 关", **result})


@bp.post("/api/counters/reset")
@require_admin
def api_counters_reset():
    level = body().get("level")
    result = _ext("bx_store").admin_reset_counters(int(level) if level not in (None, "") else None)
    return jsonify({"ok": True, "message": "计数已清零", **result})


def _store_error(exc: Exception):
    if isinstance(exc, KeyError):
        return jsonify({"ok": False, "error": "no_level", "message": f"第 {exc.args[0]} 关不存在"}), 404
    return jsonify({"ok": False, "error": "invalid", "message": str(exc)}), 400
