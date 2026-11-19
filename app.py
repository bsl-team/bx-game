"""bx-game：没有主页的密码迷宫。

访问 / 会跳到玩家当前关卡 /game/N。关卡页完全是站长自己的 HTML，
服务端只往里插：主题背景、一个密码框和「上一关 / 下一关」。
浏览器本地算 SHA-256，服务器只保存/校验哈希，管理页面在 /super_admin。
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import secrets as pysecrets
import threading
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs

from flask import (Flask, Response, g, redirect, request,
                   send_from_directory, session)

from admin import AdminAuth, authed as admin_authed, bp as admin_bp
from game import ROOT, AnswerBook, GameState, IPRateLimiter
from ipstats import IPStats, PriorityGate, is_loopback
from themes import PageStyles, SiteSettings, ThemeStore, inject_background

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 5000, "debug": False},
    "security": {
        "secret_key_file": "data/secret.key",
        "session_cookie_name": "bxmaze",
        "session_max_age_seconds": 2592000,
        "session_httponly": True,
        "session_samesite": "Lax",
        "session_secure": False,
        "max_guess_length": 64,
        "answer_max_body_bytes": 1024,
    },
    "game": {
        "answers_file": "data/answers.json",
        "state_file": "data/state.json",
        "state_flush_seconds": 1.5,
        "settings_file": "data/settings.json",
        "themes_file": "data/themes.json",
        "pages_file": "data/pages.json",
        "ipstats_file": "data/ipstats.db",
        "copyspace_dir": "copyspace",
        "log_size": 200,
        "page_cache_size": 512,
    },
    "admin": {
        "credentials_file": "data/admin.json",
        "session_max_age_seconds": 1800,
        "hash_iterations": 200000,
        "min_password_length": 8,
        "login_rate_limit_per_5min": 10,
        "wrong_password_delay_ms": 1000,
    },
}

CONTROL_ANCHOR = "<!--bx-controls-->"
CONTROL_ERRORS = {
    "wrong": "密码不对。",
    "slow": "猜得太快了，请稍后再试。",
    "empty": "请输入密码。",
    "toolong": "密码太长了。",
    "busy": "服务器繁忙，请稍后再试。",
}

# 浏览器端 SHA-256：http 下没有 crypto.subtle，所以内联一份纯 JS 实现。
SHA256_JS = r"""
(function () {
  function rr(v, n) { return (v >>> n) | (v << (32 - n)); }
  function utf8(s) {
    if (window.TextEncoder) { return new TextEncoder().encode(s); }
    var bin = unescape(encodeURIComponent(s)), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) { out[i] = bin.charCodeAt(i); }
    return out;
  }
  function sha256(str) {
    var K = [1116352408,1899447441,3049323471,3921009573,961987163,1508970993,2453635748,2870763221,
      3624381080,310598401,607225278,1426881987,1925078388,2162078206,2614888103,3248222580,3835390401,
      4022224774,264347078,604807628,770255983,1249150122,1555081692,1996064986,2554220882,2821834349,
      2952996808,3210313671,3336571891,3584528711,113926993,338241895,666307205,773529912,1294757372,
      1396182291,1695183700,1986661051,2177026350,2456956037,2730485921,2820302411,3259730800,3345764771,
      3516065817,3600352804,4094571909,275423344,430227734,506948616,659060556,883997877,958139571,
      1322822218,1537002063,1747873779,1955562222,2024104815,2227730452,2361852424,2428436474,2756734187,
      3204031479,3329325298];
    var H = [1779033703,3144134277,1013904242,2773480762,1359893119,2600822924,528734635,1541459225];
    var msg = utf8(String(str)), len = msg.length;
    var padded = new Uint8Array((((len + 9 + 63) >> 6) << 6));
    padded.set(msg); padded[len] = 0x80;
    var view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(len / 0x20000000));
    view.setUint32(padded.length - 4, (len << 3) >>> 0);
    var w = new Array(64);
    for (var off = 0; off < padded.length; off += 64) {
      for (var t = 0; t < 16; t++) { w[t] = view.getUint32(off + t * 4); }
      for (t = 16; t < 64; t++) {
        var x = w[t - 15], y = w[t - 2];
        var s0 = rr(x, 7) ^ rr(x, 18) ^ (x >>> 3);
        var s1 = rr(y, 17) ^ rr(y, 19) ^ (y >>> 10);
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g2 = H[6], h = H[7];
      for (t = 0; t < 64; t++) {
        var S1 = rr(e, 6) ^ rr(e, 11) ^ rr(e, 25);
        var ch = (e & f) ^ (~e & g2);
        var t1 = (h + S1 + ch + K[t] + w[t]) | 0;
        var S0 = rr(a, 2) ^ rr(a, 13) ^ rr(a, 22);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + maj) | 0;
        h = g2; g2 = f; f = e; e = (d + t1) | 0;
        d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g2) | 0; H[7] = (H[7] + h) | 0;
    }
    var out = "";
    for (var i = 0; i < 8; i++) { out += ("00000000" + ((H[i] >>> 0).toString(16))).slice(-8); }
    return out;
  }
  var form = document.getElementById("bx-form-__LEVEL__");
  if (!form) { return; }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var input = form.querySelector('input[type="password"]');
    var hidden = form.querySelector('input[name="hash"]');
    var button = form.querySelector('button[type="submit"]');
    var text = (input.value || "").trim();
    if (!text) { input.focus(); return; }
    var digest = sha256(text);
    if (!/^[0-9a-f]{64}$/.test(digest)) { return; }
    hidden.value = digest;
    input.value = "";
    input.readOnly = true;
    if (button) { button.disabled = true; button.textContent = "校验中…"; }
    form.submit();
  });
})();
"""


def controls_html(level: int, error: str | None = None) -> str:
    """插进关卡页的固定控件：密码框 + 上一关 / 下一关。"""
    message = CONTROL_ERRORS.get(error or "")
    if level > 1:
        prev = (f'<button type="button" '
                f'onclick="location.href=\'/game/{level - 1}\'">上一关</button>')
    else:
        prev = '<button type="button" disabled>上一关</button>'
    error_html = f'<p class="bx-error">{message}</p>' if message else ""
    script = SHA256_JS.replace("__LEVEL__", str(int(level)))
    return (
        f'<form class="bx-controls" id="bx-form-{int(level)}" method="post" action="/api/answer">'
        f'<input type="hidden" name="step" value="{int(level)}">'
        f'<input type="hidden" name="hash" value="">'
        f"{error_html}"
        '<input type="password" placeholder="密码" autocomplete="off" required>'
        f"{prev}"
        '<button type="submit">下一关</button>'
        "</form>"
        f"<script>{script}</script>"
    )


def inject_controls(source: str, widget: str) -> str:
    if CONTROL_ANCHOR in source:
        return source.replace(CONTROL_ANCHOR, widget, 1)
    lower = source.lower()
    if "</body>" in lower:
        index = lower.rindex("</body>")
        return source[:index] + widget + source[index:]
    return source + widget


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


class PageCache:
    """按 mtime 缓存关卡源码；按条目数和总字节数限制内存占用。"""

    def __init__(self, directory: Path, max_entries: int = 512,
                 max_bytes: int = 32 * 1024 * 1024, max_item_bytes: int = 512 * 1024):
        self.directory = directory
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(0, int(max_bytes))
        self.max_item_bytes = max(0, int(max_item_bytes))
        self.lock = threading.Lock()
        self.entries: "OrderedDict[int, tuple[float, str]]" = OrderedDict()
        self.cached_bytes = 0

    def read(self, level: int) -> str | None:
        path = self.directory / f"step{int(level)}.html"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        with self.lock:
            cached = self.entries.get(level)
            if cached and cached[0] == mtime:
                self.entries.move_to_end(level)
                return cached[1]
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if len(source) > self.max_item_bytes:
            return source
        with self.lock:
            old = self.entries.pop(level, None)
            if old:
                self.cached_bytes -= len(old[1])
            self.entries[level] = (mtime, source)
            self.cached_bytes += len(source)
            while self.entries and (
                len(self.entries) > self.max_entries or self.cached_bytes > self.max_bytes
            ):
                _key, value = self.entries.popitem(last=False)
                self.cached_bytes -= len(value[1])
        return source


class RenderCache:
    """缓存最终渲染好的关卡 HTML（按内容哈希区分），热门关卡直接回字节。"""

    def __init__(self, max_entries: int = 256, max_bytes: int = 16 * 1024 * 1024):
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(0, int(max_bytes))
        self.lock = threading.Lock()
        self.entries: "OrderedDict[tuple, bytes]" = OrderedDict()
        self.cached_bytes = 0

    def get(self, key: tuple) -> bytes | None:
        with self.lock:
            value = self.entries.get(key)
            if value is not None:
                self.entries.move_to_end(key)
            return value

    def put(self, key: tuple, value: bytes) -> None:
        if len(value) > self.max_bytes:
            return
        with self.lock:
            old = self.entries.pop(key, None)
            if old is not None:
                self.cached_bytes -= len(old)
            self.entries[key] = value
            self.cached_bytes += len(value)
            while self.entries and (
                len(self.entries) > self.max_entries or self.cached_bytes > self.max_bytes
            ):
                _key, old_value = self.entries.popitem(last=False)
                self.cached_bytes -= len(old_value)


def create_app(cfg: dict) -> Flask:
    app = Flask(__name__)
    app.secret_key = load_secret_key(cfg)
    sec = cfg["security"]
    game_cfg = cfg["game"]
    app.config.update(
        SESSION_COOKIE_NAME=str(sec["session_cookie_name"]),
        SESSION_COOKIE_HTTPONLY=bool(sec["session_httponly"]),
        SESSION_COOKIE_SAMESITE=str(sec["session_samesite"]),
        SESSION_COOKIE_SECURE=bool(sec["session_secure"]),
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=int(sec["session_max_age_seconds"])),
        SESSION_REFRESH_EACH_REQUEST=False,
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=2_500_000,
        MAX_FORM_MEMORY_SIZE=2_000_000,
    )

    book = AnswerBook(cfg)
    store = GameState(cfg, book)
    settings = SiteSettings(ROOT / str(game_cfg.get("settings_file", "data/settings.json")))
    themes = ThemeStore(ROOT / str(game_cfg.get("themes_file", "data/themes.json")))
    pages = PageStyles(ROOT / str(game_cfg.get("pages_file", "data/pages.json")))
    stats = IPStats(
        ROOT / str(game_cfg.get("ipstats_file", "data/ipstats.db")),
        max_ips=int(game_cfg.get("max_tracked_ips", 20_000)),
        flush_interval=float(game_cfg.get("ipstats_flush_seconds", 120)),
    )
    gate = PriorityGate(
        max_inflight=settings.get_int("gate_max_inflight", 1, 100_000),
        max_waiters=settings.get_int("gate_max_waiters", 1, 1_000_000),
    )
    game_limiter = IPRateLimiter()
    admin_limiter = IPRateLimiter()
    levels_dir = ROOT / str(game_cfg.get("copyspace_dir", "copyspace"))
    page_cache = PageCache(levels_dir, int(game_cfg.get("page_cache_size", 512)))
    render_cache = RenderCache()
    answer_body_bytes = int(sec.get("answer_max_body_bytes", 1024))

    app.extensions["bx_cfg"] = cfg
    app.extensions["bx_store"] = store
    app.extensions["bx_book"] = book
    app.extensions["bx_settings"] = settings
    app.extensions["bx_themes"] = themes
    app.extensions["bx_pages"] = pages
    app.extensions["bx_ipstats"] = stats
    app.extensions["bx_gate"] = gate
    app.extensions["bx_game_limiter"] = game_limiter
    app.extensions["bx_admin_limiter"] = admin_limiter
    app.extensions["bx_page_cache"] = page_cache
    app.extensions["bx_levels_dir"] = levels_dir
    app.extensions["bx_admin"] = AdminAuth(cfg)
    app.register_blueprint(admin_bp)

    def player_id() -> str:
        pid = session.get("pid")
        if not pid:
            pid = pysecrets.token_hex(8)
            session["pid"] = pid
        return pid

    def cleared_levels() -> int:
        return max(0, int(session.get("cleared", 0)))

    def client_ip() -> str:
        return str(request.remote_addr or "")

    def gameplay_guard() -> Response | None:
        ip = client_ip()
        loop = is_loopback(ip)
        stats.record(ip, loopback=loop)
        if loop or admin_authed():
            return None
        allowed, retry = game_limiter.check(
            ip,
            settings.get_int("rate_limit_count", 1),
            settings.get_float("rate_limit_window", 1.0),
        )
        if not allowed:
            return Response(
                f"请求太频繁，请 {max(1, round(retry))} 秒后再试。",
                status=429, content_type="text/plain; charset=utf-8",
            )
        timeout = settings.get_float("gate_wait_timeout_ms", 100, 60_000) / 1000.0
        if not gate.acquire(stats.priority(ip), timeout):
            return Response(
                "服务器繁忙，请稍后再试。",
                status=503, content_type="text/plain; charset=utf-8",
            )
        g.bx_gate_held = True
        return None

    @app.before_request
    def permanent_session():
        if not session.permanent:
            session.permanent = True

    @app.teardown_request
    def release_gate(_exc):
        if g.pop("bx_gate_held", False):
            gate.release()

    @app.after_request
    def headers(resp: Response):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/super_admin"):
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    def own_step() -> dict:
        return store.progress(cleared_levels())

    def back_to_level(code: str) -> Response:
        progress = own_step()
        own = progress["step"]
        if progress["total_levels"] == 0 or progress["finished"] or own < 1:
            return redirect("/", code=303)
        return redirect(f"/game/{own}?bx={code}", code=303)

    @app.get("/")
    def index():
        player_id()
        progress = own_step()
        if progress["total_levels"] == 0:
            return Response("还没有配置关卡。", mimetype="text/plain")
        if progress["finished"]:
            return Response("全部关卡已通过。", mimetype="text/plain")
        return redirect(f"/game/{progress['step']}", code=303)

    @app.get("/game/<int:level>")
    def game_level(level: int):
        blocked = gameplay_guard()
        if blocked is not None:
            return blocked
        player_id()
        progress = own_step()
        total = progress["total_levels"]
        own = progress["step"]
        if total == 0:
            return Response("还没有配置关卡。", mimetype="text/plain")
        if progress["finished"]:
            return Response("全部关卡已通过。", mimetype="text/plain")
        if level != own:
            if not (admin_authed() and 1 <= level <= total):
                return redirect(f"/game/{own}", code=303)
        source = page_cache.read(level)
        if source is None:
            return Response("这一关还没有页面。", status=404,
                            content_type="text/plain; charset=utf-8")

        level_style = pages.get(level)
        custom_css = level_style.get("css", "")
        theme_name = level_style.get("theme") or str(settings.get("default_theme") or "chatgpt")
        _name, theme = themes.get(theme_name, str(settings.get("default_theme") or "chatgpt"))
        error = request.args.get("bx")
        key = (level, hash(source), hash(theme["css"]), hash(custom_css), error)
        cached = render_cache.get(key)
        if cached is not None:
            return Response(cached, content_type="text/html; charset=utf-8")
        html_out = inject_background(source, theme["css"], custom_css)
        html_out = inject_controls(html_out, controls_html(level, error))
        payload = html_out.encode("utf-8")
        render_cache.put(key, payload)
        return Response(payload, content_type="text/html; charset=utf-8")

    @app.get("/copyspace/<path:name>")
    def copyspace_file(name: str):
        match = re.fullmatch(r"step([1-9]\d*)\.html", name)
        if match:
            return redirect(f"/game/{int(match.group(1))}", code=302)
        return send_from_directory(levels_dir, name)

    def read_answer_form() -> tuple[dict | None, Response | None]:
        ctype = (request.content_type or "").split(";")[0].strip().lower()
        if ctype != "application/x-www-form-urlencoded":
            return None, Response("请用表单提交。", status=415,
                                  content_type="text/plain; charset=utf-8")
        length = request.content_length
        if length is not None and length > answer_body_bytes:
            return None, Response(
                f"表单内容不能超过 {answer_body_bytes} 字节。",
                status=413, content_type="text/plain; charset=utf-8",
            )
        try:
            # 流式请求（没有 Content-Length）最多读 1 KB，多出的部分直接截断。
            chunks = []
            remaining = answer_body_bytes
            while remaining > 0:
                chunk = request.stream.read(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            body = b"".join(chunks)
        except OSError:
            return None, Response("读取请求失败。", status=400,
                                  content_type="text/plain; charset=utf-8")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return None, Response("编码错误。", status=400,
                                  content_type="text/plain; charset=utf-8")
        try:
            values = parse_qs(text, keep_blank_values=True, max_num_fields=32)
        except ValueError:
            return None, Response("表单字段太多。", status=400,
                                  content_type="text/plain; charset=utf-8")
        return {key: items[0] if items else "" for key, items in values.items()}, None

    @app.post("/api/answer")
    def api_answer():
        blocked = gameplay_guard()
        if blocked is not None:
            return blocked
        pid = player_id()
        form, failure = read_answer_form()
        if failure is not None:
            return failure
        try:
            step = int(form.get("step", ""))
        except (TypeError, ValueError):
            return redirect("/", code=303)
        digest = AnswerBook.normalize_hash(form.get("hash", ""))
        if digest is None:
            return back_to_level("empty")

        result = store.submit(step, digest, cleared_levels(), pid)
        if result.get("correct"):
            session["cleared"] = result["cleared"]
            if result.get("finished"):
                return redirect("/", code=303)
            next_path = result.get("next") or "/"
            return redirect(next_path, code=303)
        if result.get("error") in ("finished", "no_levels", "wrong_step"):
            return redirect("/", code=303)
        return back_to_level("wrong")

    return app


def print_status(cfg: dict) -> None:
    book = AnswerBook(cfg)
    store = GameState(cfg, book)
    snap = store.snapshot()
    levels_dir = ROOT / str(cfg["game"].get("copyspace_dir", "copyspace"))
    auth = AdminAuth(cfg)
    print(f"当前关卡: {snap['current_step']} / 共 {snap['total_levels']} 关")
    print(f"全网总猜测: {snap['total_guesses']}")
    print("管理密码: " + ("已设置（/super_admin）" if auth.configured
                        else "未设置（运行 python app.py admin-password）"))
    for row in snap["levels"]:
        page = "有" if (levels_dir / f"step{row['level']}.html").is_file() else "缺"
        digest = (row.get("hash") or "")[:12]
        print(f"{row['level']}  哈希={digest}…  本关猜测={row['guesses']}  "
              f"已破解={'是' if row['solved'] else '否'}  页面={page}")
    if snap["total_levels"] == 0:
        print("（还没有关卡：在 /super_admin 里添加，或写 data/answers.json 和 copyspace/stepN.html）")
    store.close()


def print_log(cfg: dict, count: int = 30) -> None:
    book = AnswerBook(cfg)
    store = GameState(cfg, book)
    entries = store.snapshot()["guess_log"][-max(1, count):]
    if not entries:
        print("还没有猜测记录。")
        store.close()
        return
    for entry in entries:
        mark = "对" if entry.get("correct") else "错"
        pid = str(entry.get("pid") or "")[:8]
        digest = str(entry.get("hash") or "")[:16]
        print(f"[{entry.get('at')}] 第 {entry.get('level')} 关  {pid}  {digest}…  {mark}")
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="bx-game 密码迷宫")
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "status", "reset", "goto", "set-answer", "log", "hash",
                 "admin-password"],
    )
    parser.add_argument("args", nargs="*", help="goto <关卡> / set-answer <关卡> <答案> / log [条数] / hash <文本> / admin-password [密码]")
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
    if args.command == "admin-password":
        auth = AdminAuth(cfg)
        password = args.args[0] if args.args else ""
        if not password:
            password = getpass.getpass("新管理密码: ")
            confirm = getpass.getpass("再输一次: ")
            if password != confirm:
                raise SystemExit("两次输入不一致。")
        try:
            auth.set_password(password)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"管理密码已保存到 {auth.path}。打开 /super_admin 登录。")
        return
    if args.command == "reset":
        book = AnswerBook(cfg)
        store = GameState(cfg, book)
        store.reset()
        store.close()
        print("已重置：回到第 1 关，计数清零，答案保留。")
        return
    if args.command == "goto":
        if not args.args:
            raise SystemExit("用法: python app.py goto <关卡>")
        book = AnswerBook(cfg)
        store = GameState(cfg, book)
        level = store.goto(int(args.args[0]))
        total = book.total_levels()
        store.close()
        print(f"全网当前关卡已跳到第 {level} 关（共 {total} 关）。")
        if level > total:
            print("提醒：这一关还没有答案，玩家会看到「还没有配置关卡」。")
        return
    if args.command == "set-answer":
        if len(args.args) < 2:
            raise SystemExit("用法: python app.py set-answer <关卡> <答案>")
        level = int(args.args[0])
        book = AnswerBook(cfg)
        digest = book.set_answer(level, args.args[1])
        store = GameState(cfg, book)
        store.reset_solved(level)
        store.close()
        levels_dir = ROOT / str(cfg["game"].get("copyspace_dir", "copyspace"))
        print(f"第 {level} 关哈希已写入（{digest[:12]}…，只保存哈希）。")
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
    print("公开入口: / → /game/<当前关>；管理入口: /super_admin")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
