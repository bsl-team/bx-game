"""bx-game：和参考站一样没有管理界面的极简密码迷宫。

首页 index.html 用 hashStrong 做快捷跳转；copyspace/stepN.html 是每一层（站长自己写），
copyspace/guard.js 负责藏页面和校验进度，答案只在服务端的 data/answers.json 里。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets as pysecrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, session

from game import ROOT, AnswerBook, GameState, RateLimiter

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 5000, "debug": False},
    "security": {
        "secret_key_file": "data/secret.key",
        "session_cookie_name": "bxmaze",
        "session_max_age_seconds": 2592000,
        "session_httponly": True,
        "session_samesite": "Lax",
        "session_secure": False,
        "guess_rate_limit_per_minute": 30,
        "guess_min_interval_ms": 150,
        "max_guess_length": 64,
    },
    "game": {
        "answers_file": "data/answers.json",
        "state_file": "data/state.json",
        "copyspace_dir": "copyspace",
        "log_size": 200,
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

    book = AnswerBook(cfg)
    store = GameState(cfg, book)
    limiter = RateLimiter(cfg)
    levels_dir = ROOT / str(cfg["game"].get("copyspace_dir", "copyspace"))
    max_guess_length = int(sec.get("max_guess_length", 64))
    app.extensions["bx_cfg"] = cfg
    app.extensions["bx_store"] = store
    app.extensions["bx_book"] = book
    app.extensions["bx_limiter"] = limiter

    def player_id() -> str:
        pid = session.get("pid")
        if not pid:
            pid = pysecrets.token_hex(8)
            session["pid"] = pid
        return pid

    def cleared_levels() -> int:
        return max(0, int(session.get("cleared", 0)))

    @app.before_request
    def permanent_session():
        session.permanent = True

    @app.after_request
    def headers(resp: Response):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.get("/")
    def index():
        return send_from_directory(ROOT, "index.html")

    @app.get("/hash/<path:name>")
    def hash_file(name: str):
        return send_from_directory(ROOT / "hash", name)

    @app.get("/copyspace/<path:name>")
    def copyspace_file(name: str):
        return send_from_directory(levels_dir, name)

    @app.get("/api/progress")
    def api_progress():
        player_id()
        return jsonify(store.progress(cleared_levels()))

    @app.post("/api/answer")
    def api_answer():
        pid = player_id()
        allowed, retry = limiter.check(pid)
        if not allowed:
            return jsonify({
                "ok": False,
                "error": "rate_limited",
                "message": f"猜得太快了，请 {max(1, round(retry))} 秒后再试。",
            }), 429

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = request.form
        try:
            step = int(payload.get("step"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad_step", "message": "缺少关卡编号。"}), 400

        password = str(payload.get("password", "")).strip()
        if not password:
            return jsonify({"ok": False, "error": "empty", "message": "请输入密码。"}), 400
        if len(password) > max_guess_length:
            return jsonify({
                "ok": False,
                "error": "too_long",
                "message": f"最长 {max_guess_length} 个字符。",
            }), 400

        result = store.submit(step, password, cleared_levels(), pid)
        if result.get("correct"):
            session["cleared"] = result["cleared"]
        return jsonify(result)

    return app


def print_status(cfg: dict) -> None:
    book = AnswerBook(cfg)
    store = GameState(cfg, book)
    snap = store.snapshot()
    levels_dir = ROOT / str(cfg["game"].get("copyspace_dir", "copyspace"))
    print(f"当前关卡: {snap['current_step']} / 共 {snap['total_levels']} 关")
    print(f"全网总猜测: {snap['total_guesses']}")
    for row in snap["levels"]:
        page = "有" if (levels_dir / f"step{row['level']}.html").is_file() else "缺"
        print(f"{row['level']}  答案={row['answer']}  本关猜测={row['guesses']}  "
              f"已破解={'是' if row['solved'] else '否'}  页面={page}")
    if snap["total_levels"] == 0:
        print("（还没有关卡：往 data/answers.json 写答案，再建 copyspace/stepN.html）")


def print_log(cfg: dict, count: int = 30) -> None:
    book = AnswerBook(cfg)
    store = GameState(cfg, book)
    entries = store.snapshot()["guess_log"][-max(1, count):]
    if not entries:
        print("还没有猜测记录。")
        return
    for entry in entries:
        mark = "对" if entry.get("correct") else "错"
        pid = str(entry.get("pid") or "")[:8]
        print(f"[{entry.get('at')}] 第 {entry.get('level')} 关  {pid}  "
              f"{entry.get('guess')}  {mark}")


def main() -> None:
    parser = argparse.ArgumentParser(description="bx-game 极简密码迷宫（无管理界面）")
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "status", "reset", "goto", "set-answer", "log", "hash"],
    )
    parser.add_argument("args", nargs="*", help="goto <关卡> / set-answer <关卡> <答案> / log [条数] / hash <文本>")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.command == "status":
        print_status(cfg)
        return
    if args.command == "log":
        print_log(cfg, int(args.args[0]) if args.args else 30)
        return
    if args.command == "hash":
        if not args.args:
            raise SystemExit("用法: python app.py hash <文本>")
        print(hashlib.sha256(args.args[0].encode("utf-8")).hexdigest())
        return
    if args.command == "reset":
        book = AnswerBook(cfg)
        GameState(cfg, book).reset()
        print("已重置：回到第 1 关，计数清零，答案保留。")
        return
    if args.command == "goto":
        if not args.args:
            raise SystemExit("用法: python app.py goto <关卡>")
        book = AnswerBook(cfg)
        store = GameState(cfg, book)
        level = store.goto(int(args.args[0]))
        total = book.total_levels()
        print(f"全网当前关卡已跳到第 {level} 关（共 {total} 关）。")
        if level > total:
            print("提醒：这一关还没有答案，玩家会看到「还没有配置关卡」。")
        return
    if args.command == "set-answer":
        if len(args.args) < 2:
            raise SystemExit("用法: python app.py set-answer <关卡> <答案>")
        level = int(args.args[0])
        book = AnswerBook(cfg)
        answer = book.set_answer(level, args.args[1])
        GameState(cfg, book).reset_solved(level)
        levels_dir = ROOT / str(cfg["game"].get("copyspace_dir", "copyspace"))
        print(f"第 {level} 关答案已写入（{len(answer)} 个字符）。")
        if not (levels_dir / f"step{level}.html").is_file():
            print(f"提醒：{levels_dir.name}/step{level}.html 不存在，这一关会 404。")
        print(f"若要让全网回到第 {level} 关，运行: python app.py goto {level}")
        return

    host = args.host or cfg["server"]["host"]
    port = args.port or int(cfg["server"]["port"])
    debug = bool(args.debug or cfg["server"]["debug"])
    app = create_app(cfg)
    total = app.extensions["bx_book"].total_levels()
    current = app.extensions["bx_store"].snapshot()["current_step"]
    print(f"bx-game → http://{host}:{port}/  第 {current} 关 / 共 {total} 关")
    print("没有管理界面：改 data/answers.json、copyspace/stepN.html，或用 python app.py status")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
