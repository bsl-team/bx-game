"""bx-game: a one-password-for-everyone guessing game (Flask)."""
from __future__ import annotations

import argparse
import json
import secrets as pysecrets
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from admin import AdminAuth, bp
from game import ROOT, GameStore, RateLimiter, inject_password_widget

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 5000, "debug": False},
    "security": {
        "secret_key_file": "data/secret.key",
        "session_cookie_name": "bxgame",
        "session_max_age_seconds": 2592000,
        "session_httponly": True,
        "session_samesite": "Lax",
        "session_secure": False,
        "hash_algorithm": "pbkdf2_sha256",
        "hash_iterations": 120000,
        "guess_rate_limit_per_minute": 30,
        "guess_min_interval_ms": 150,
        "max_guess_length": 64,
    },
    "password": {
        "charset": "lower",
        "charsets": {},
        "level1_length": 4,
        "length_step": 1,
        "length_max": 8,
        "avoid_repeats": True,
    },
    "rounds": {
        "mode": "puzzle",
        "max_levels": 0,
        "auto_advance": True,
        "auto_redirect": True,
        "state_file": "data/state.json",
        "questions_file": "data/questions.json",
    },
    "admin": {
        "credentials_file": "data/admin.json",
        "token_file": "data/admin_token.txt",
        "session_max_age_seconds": 1800,
        "hash_iterations": 200000,
        "min_password_length": 8,
        "login_rate_limit_per_5min": 10,
        "login_min_interval_ms": 800,
        "reveal_passwords": True,
        "log_guess_text": True,
        "guess_log_size": 500,
    },
    "ui": {
        "title": "猜密码",
        "subtitle": "",
        "accent_color": "#10a37f",
        "theme": "chatgpt",
        "poll_interval_ms": 2500,
        "show_charset_hint": False,
        "show_hint": True,
        "max_chat_messages": 60,
        "footer_text": "",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None) -> dict:
    cfg_path = path or ROOT / "config.json"
    user_cfg = {}
    if cfg_path.exists():
        try:
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                user_cfg = loaded
        except (json.JSONDecodeError, OSError) as exc:
            raise SystemExit(f"config.json 解析失败: {exc}")
    cfg = deep_merge(DEFAULTS, user_cfg)
    cfg["config_path"] = str(cfg_path)
    return cfg


def load_secret_key(cfg: dict) -> bytes:
    path = ROOT / str(cfg["security"]["secret_key_file"])
    if path.exists():
        key = path.read_bytes().strip()
        if key:
            return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = pysecrets.token_hex(32).encode()
    path.write_bytes(key)
    return key


def create_app(cfg: dict) -> Flask:
    app = Flask(__name__)
    app.secret_key = load_secret_key(cfg)
    sec = cfg["security"]
    app.config.update(
        SESSION_COOKIE_NAME=str(sec["session_cookie_name"]),
        SESSION_COOKIE_HTTPONLY=bool(sec["session_httponly"]),
        SESSION_COOKIE_SAMESITE=str(sec["session_samesite"]),
        SESSION_COOKIE_SECURE=bool(sec["session_secure"]),
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=int(sec["session_max_age_seconds"])),
        JSON_AS_ASCII=False,
    )

    store = GameStore(cfg)
    limiter = RateLimiter(cfg)
    admin_cfg = cfg["admin"]
    admin_limiter = RateLimiter(
        cfg,
        limit=int(admin_cfg.get("login_rate_limit_per_5min", 10)),
        window=300.0,
        min_interval_ms=int(admin_cfg.get("login_min_interval_ms", 800)),
    )
    app.extensions["bx_cfg"] = cfg
    app.extensions["bx_store"] = store
    app.extensions["bx_limiter"] = limiter
    app.extensions["bx_admin"] = AdminAuth(cfg)
    app.extensions["bx_admin_limiter"] = admin_limiter
    app.register_blueprint(bp)

    if not admin_cfg.get("reveal_passwords", True):
        store.admin_purge_plaintext()

    def player_id() -> str:
        pid = session.get("pid")
        if not pid:
            pid = pysecrets.token_hex(8)
            session["pid"] = pid
        return pid

    def cleared_levels() -> int:
        return max(0, int(session.get("cleared", 0)))

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        if request.path.startswith("/admin"):
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        return resp

    def widget_html(view: dict) -> str:
        return render_template(
            "widget.html",
            view=view,
            ui=cfg["ui"],
            auto_redirect=bool(cfg["rounds"].get("auto_redirect", True)),
            next_level=view["level"] + 1,
        )

    def render_level_page(page_level: int | None):
        session.permanent = True
        player_id()
        cleared = cleared_levels()
        ui = dict(cfg["ui"])
        ui["rate_limit_per_minute"] = int(sec["guess_rate_limit_per_minute"])
        ui["auto_redirect"] = bool(cfg["rounds"].get("auto_redirect", True))
        target = page_level if page_level is not None else min(cleared + 1, store.status(cleared)["global_level"])
        try:
            view = store.level_view(target, cleared)
        except (PermissionError, KeyError):
            view = None
        # 站长自己写好的网页源码：我们只往里面插一个密码框
        if view and view.get("html"):
            return Response(inject_password_widget(view["html"], widget_html(view)), mimetype="text/html")
        return render_template("index.html", ui=ui, cfg=cfg, page_level=page_level, view=view)

    @app.get("/")
    def index():
        return render_level_page(None)

    @app.get("/level/<int:page_level>")
    def level_page(page_level: int):
        """Locked levels bounce back to the player's own level, like the reference site."""
        if page_level < 1:
            return render_level_page(None)
        own = min(cleared_levels() + 1, store.status(cleared_levels())["global_level"])
        if page_level > own:
            return redirect(url_for("level_page", page_level=own) if own > 1 else url_for("index"))
        return render_level_page(page_level)

    @app.get("/api/progress")
    def api_progress():
        return jsonify(store.progress(cleared_levels()))

    @app.get("/api/level/<int:level>")
    def api_level(level: int):
        try:
            return jsonify(store.level_view(level, cleared_levels()))
        except PermissionError:
            return jsonify({"ok": False, "error": "locked",
                            "message": "这一关还没解锁，先过前面的关。",
                            "own_level": min(cleared_levels() + 1, store.status(cleared_levels())["global_level"])}), 403
        except KeyError:
            return jsonify({"ok": False, "error": "no_level", "message": "这一关还不存在"}), 404

    @app.get("/api/status")
    def api_status():
        pid = player_id()
        status = store.status(cleared_levels())
        status["guesses_left_this_minute"] = limiter.remaining(pid)
        status["rate_limit_per_minute"] = int(sec["guess_rate_limit_per_minute"])
        status["poll_interval_ms"] = int(cfg["ui"]["poll_interval_ms"])
        status["auto_redirect"] = bool(cfg["rounds"].get("auto_redirect", True))
        status["server_time"] = time.time()
        return jsonify(status)

    @app.post("/api/guess")
    def api_guess():
        pid = player_id()
        allowed, retry = limiter.check(pid)
        if not allowed:
            return jsonify(
                {
                    "ok": False,
                    "error": "rate_limited",
                    "message": f"猜得太快了，请 {max(1, round(retry))} 秒后再试。",
                    "retry_after": round(retry, 1),
                }
            ), 429
        payload = request.get_json(silent=True) or {}
        guess = str(payload.get("guess", request.form.get("guess", "")))
        cleared = cleared_levels()
        result = store.submit_guess(guess, cleared, pid)
        if result.get("correct"):
            result["cleared"] = max(cleared, int(result.get("level", 1)))
            session["cleared"] = result["cleared"]
        result["status"] = store.status(result.get("cleared", cleared))
        result["rate_limit_per_minute"] = int(sec["guess_rate_limit_per_minute"])
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="bx-game 猜密码小游戏")
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "reset", "status", "admin-token", "admin-reset"],
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.command == "reset":
        GameStore(cfg).reset()
        print("已重置：回到第一关，全局计数清零。")
        return
    if args.command == "status":
        print(json.dumps(GameStore(cfg).status(0), ensure_ascii=False, indent=2))
        return
    if args.command in ("admin-token", "admin-reset"):
        auth = AdminAuth(cfg)
        if args.command == "admin-token":
            token = auth.bootstrap_token()
            print(token or "管理密码已设置，token 不可用；需要重新发放请用 `python app.py admin-reset`")
        else:
            print(f"已作废旧密码，新 admin token: {auth.rotate_token()}")
        return

    host = args.host or cfg["server"]["host"]
    port = args.port or int(cfg["server"]["port"])
    debug = bool(args.debug or cfg["server"]["debug"])
    app = create_app(cfg)
    print(f"猜密码小游戏 → http://{host}:{port}/  管理面板 /admin  "
          f"(当前第 {app.extensions['bx_store'].status(0)['global_level']} 关)")
    token = app.extensions["bx_admin"].bootstrap_token()
    if token:
        print(f"首次启动 admin token: {token}")
        print(f"（也写在 {ROOT / cfg['admin']['token_file']}）在 /admin 输入它来设置管理密码，设置后 token 立即作废。")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
