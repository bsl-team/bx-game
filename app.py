"""bx-game: a one-password-for-everyone guessing game (Flask)."""
from __future__ import annotations

import argparse
import json
import secrets as pysecrets
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session

from game import ROOT, GameStore, RateLimiter

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
    "rounds": {"max_levels": 0, "auto_advance": True, "state_file": "data/state.json"},
    "ui": {
        "title": "猜密码",
        "subtitle": "",
        "accent_color": "#10a37f",
        "theme": "chatgpt",
        "poll_interval_ms": 2500,
        "show_charset_hint": False,
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
    app.extensions["bx_store"] = store
    app.extensions["bx_limiter"] = limiter

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
        return resp

    @app.get("/")
    def index():
        session.permanent = True
        player_id()
        ui = dict(cfg["ui"])
        ui["rate_limit_per_minute"] = int(sec["guess_rate_limit_per_minute"])
        return render_template("index.html", ui=ui, cfg=cfg)

    @app.get("/api/status")
    def api_status():
        pid = player_id()
        status = store.status(cleared_levels())
        status["guesses_left_this_minute"] = limiter.remaining(pid)
        status["rate_limit_per_minute"] = int(sec["guess_rate_limit_per_minute"])
        status["poll_interval_ms"] = int(cfg["ui"]["poll_interval_ms"])
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
    parser.add_argument("command", nargs="?", default="run", choices=["run", "reset", "status"])
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

    host = args.host or cfg["server"]["host"]
    port = args.port or int(cfg["server"]["port"])
    debug = bool(args.debug or cfg["server"]["debug"])
    app = create_app(cfg)
    print(f"猜密码小游戏 → http://{host}:{port}/  (关卡: 全局第 {store_level(cfg)} 关)")
    app.run(host=host, port=port, debug=debug, threaded=True)


def store_level(cfg: dict) -> int:
    try:
        return GameStore(cfg).status(0)["global_level"]
    except Exception:
        return "?"


if __name__ == "__main__":
    main()
