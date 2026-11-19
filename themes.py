"""站点设置、主题库、每关背景配置，以及把背景注入站长自己写的页面。"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from game import read_json, write_json

BASE_CSS = """
:root {
  --bx-text: #e9eef5;
  --bx-muted: #9aa8b8;
  --bx-accent: #10a37f;
  --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,255,255,.07);
  --bx-border: rgba(255,255,255,.16);
  --bx-danger: #ff7a7a;
  --bx-focus: rgba(16,163,127,.28);
  --bx-shadow: 0 16px 48px rgba(0,0,0,.22);
  --bx-radius: 14px;
  --bx-font: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  --bx-bg: #0b0f14;
  --bx-fallback: #0b0f14;
}
html { min-height: 100%; background: var(--bx-fallback); }
body { min-height: 100vh; margin: 0; color: var(--bx-text); font-family: var(--bx-font); background: transparent !important; }
a { color: var(--bx-accent); }
::selection { background: var(--bx-accent); color: var(--bx-accent-fg); }
.bx-bg { position: fixed; inset: 0; z-index: -1; pointer-events: none; background: var(--bx-bg); background-attachment: fixed; }
.bx-bg::after { content: ""; position: absolute; inset: 0; background: var(--bx-overlay, none); }
.bx-controls { position: relative; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; max-width: 560px; margin: 28px 0 14px; padding: 14px; border: 1px solid var(--bx-border); border-radius: var(--bx-radius); background: var(--bx-field); box-shadow: var(--bx-shadow); font-family: var(--bx-font); }
.bx-controls .bx-error { flex-basis: 100%; margin: 0 0 2px; color: var(--bx-danger); font-size: 14px; }
.bx-controls input[type="password"] { flex: 1 1 180px; min-width: 0; padding: 10px 12px; border: 1px solid var(--bx-border); border-radius: calc(var(--bx-radius) - 4px); background: var(--bx-field); color: var(--bx-text); font: inherit; outline: none; transition: border-color .15s, box-shadow .15s; }
.bx-controls input[type="password"]:focus { border-color: var(--bx-accent); box-shadow: 0 0 0 3px var(--bx-focus); }
.bx-controls button { padding: 10px 16px; border: 1px solid var(--bx-border); border-radius: calc(var(--bx-radius) - 4px); background: var(--bx-field); color: var(--bx-text); font: inherit; cursor: pointer; transition: transform .12s, border-color .15s, background .15s; }
.bx-controls button:hover { border-color: var(--bx-accent); }
.bx-controls button:active { transform: translateY(1px); }
.bx-controls button[type="submit"] { border-color: transparent; background: var(--bx-accent); color: var(--bx-accent-fg); font-weight: 600; }
.bx-controls button:disabled { opacity: .45; cursor: not-allowed; }
@media (max-width: 520px) {
  .bx-controls { padding: 12px; }
  .bx-controls button { flex: 1 1 auto; }
}
"""

DEFAULT_SETTINGS = {
    "rate_limit_count": 20,
    "rate_limit_window": 5,
    "rate_limit_admin_count": 10,
    "rate_limit_admin_window": 300,
    "admin_login_delay_ms": 1000,
    "gate_max_inflight": 16,
    "gate_max_waiters": 4096,
    "gate_wait_timeout_ms": 1500,
    "default_theme": "chatgpt",
}


DEFAULT_THEMES: dict[str, dict] = {
    "chatgpt": {
        "label": "ChatGPT 绿",
        "css": """:root {
  --bx-text: #20302a; --bx-muted: #5f746a; --bx-accent: #10a37f; --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,255,255,.78); --bx-border: rgba(16,163,127,.25); --bx-danger: #c0392b;
  --bx-focus: rgba(16,163,127,.18); --bx-shadow: 0 18px 50px rgba(31,63,52,.12); --bx-radius: 14px;
  --bx-bg: radial-gradient(900px 620px at 10% -12%, #d6f3e7 0, rgba(214,243,231,0) 62%),
    radial-gradient(720px 520px at 92% 6%, #e6f6ec 0, rgba(230,246,236,0) 60%),
    linear-gradient(180deg, #fbfdfb 0%, #eef5ef 100%);
  --bx-fallback: #f4f8f4;
}""",
    },
    "darktech": {
        "label": "暗科技",
        "css": """:root {
  --bx-text: #dff8ff; --bx-muted: #7ea9b8; --bx-accent: #00e5ff; --bx-accent-fg: #00141a;
  --bx-field: rgba(0,229,255,.07); --bx-border: rgba(0,229,255,.24); --bx-danger: #ff5d73;
  --bx-focus: rgba(0,229,255,.22); --bx-shadow: 0 18px 60px rgba(0,229,255,.14); --bx-radius: 10px;
  --bx-font: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, "Microsoft YaHei", monospace;
  --bx-bg: linear-gradient(rgba(0,229,255,.055) 1px, transparent 1px) 0 0/44px 44px,
    linear-gradient(90deg, rgba(0,229,255,.055) 1px, transparent 1px) 0 0/44px 44px,
    radial-gradient(1000px 700px at 82% -10%, rgba(0,119,255,.28), transparent 62%),
    radial-gradient(760px 560px at 4% 108%, rgba(0,229,255,.16), transparent 62%),
    #060a12;
  --bx-fallback: #060a12;
}
.bx-bg::after { background: linear-gradient(180deg, rgba(6,10,18,0) 45%, rgba(2,5,9,.72)); }""",
    },
    "warm": {
        "label": "温馨暖色",
        "css": """:root {
  --bx-text: #3a2a22; --bx-muted: #7d6257; --bx-accent: #d97706; --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,255,255,.66); --bx-border: rgba(180,120,70,.28); --bx-danger: #b3402e;
  --bx-focus: rgba(217,119,6,.2); --bx-shadow: 0 18px 50px rgba(122,71,38,.14); --bx-radius: 16px;
  --bx-bg: radial-gradient(760px 560px at 12% 0%, #ffe3c2 0, rgba(255,227,194,0) 62%),
    radial-gradient(680px 520px at 90% 12%, #ffd8d0 0, rgba(255,216,208,0) 58%),
    linear-gradient(175deg, #fff8ef 0%, #f7e6d2 100%);
  --bx-fallback: #fbf0e2;
}""",
    },
    "glass": {
        "label": "玻璃拟态",
        "css": """:root {
  --bx-text: #243244; --bx-muted: #667a90; --bx-accent: #4f7cff; --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,255,255,.42); --bx-border: rgba(255,255,255,.65); --bx-danger: #c0392b;
  --bx-focus: rgba(79,124,255,.22); --bx-shadow: 0 20px 60px rgba(64,90,140,.18); --bx-radius: 18px;
  --bx-bg: radial-gradient(560px 420px at 18% 16%, #b9d4ff 0, rgba(185,212,255,0) 65%),
    radial-gradient(620px 480px at 82% 12%, #ffd7ec 0, rgba(255,215,236,0) 62%),
    radial-gradient(720px 560px at 52% 96%, #c6f4e6 0, rgba(198,244,230,0) 66%),
    linear-gradient(160deg, #eef3ff, #f7f1ff 55%, #eefbf7);
  --bx-fallback: #eef3ff;
}
.bx-controls { backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); }""",
    },
    "paper": {
        "label": "纸墨",
        "css": """:root {
  --bx-text: #2f2a24; --bx-muted: #7a7060; --bx-accent: #8a5a2b; --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,252,246,.85); --bx-border: rgba(120,100,70,.28); --bx-danger: #a33a2c;
  --bx-focus: rgba(138,90,43,.18); --bx-shadow: 0 16px 40px rgba(90,70,40,.12); --bx-radius: 4px;
  --bx-font: "Songti SC", "SimSun", Georgia, serif;
  --bx-bg: repeating-linear-gradient(0deg, rgba(120,100,70,.045) 0 1px, transparent 1px 32px),
    radial-gradient(900px 700px at 50% -10%, #fffdf8, #f4ecdd 70%), #f6efe3;
  --bx-fallback: #f6efe3;
}""",
    },
    "neon": {
        "label": "霓虹赛博",
        "css": """:root {
  --bx-text: #f4e9ff; --bx-muted: #a58ec7; --bx-accent: #ff2fd0; --bx-accent-fg: #21001a;
  --bx-field: rgba(255,47,208,.08); --bx-border: rgba(255,47,208,.34); --bx-danger: #ff5470;
  --bx-focus: rgba(255,47,208,.28); --bx-shadow: 0 0 42px rgba(255,47,208,.2); --bx-radius: 12px;
  --bx-bg: radial-gradient(900px 640px at 78% -8%, rgba(255,47,208,.3), transparent 62%),
    radial-gradient(760px 560px at 4% 108%, rgba(0,200,255,.24), transparent 60%),
    linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px) 0 0/36px 36px,
    linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px) 0 0/36px 36px, #08030f;
  --bx-fallback: #08030f;
}""",
    },
    "forest": {
        "label": "深林",
        "css": """:root {
  --bx-text: #e6f4e8; --bx-muted: #9bb8a0; --bx-accent: #54d17a; --bx-accent-fg: #05270f;
  --bx-field: rgba(84,209,122,.08); --bx-border: rgba(84,209,122,.26); --bx-danger: #ff7a7a;
  --bx-focus: rgba(84,209,122,.22); --bx-shadow: 0 18px 60px rgba(0,40,15,.4); --bx-radius: 16px;
  --bx-bg: radial-gradient(900px 640px at 18% -10%, rgba(84,209,122,.22), transparent 62%),
    radial-gradient(760px 560px at 88% 16%, rgba(20,120,70,.3), transparent 64%),
    linear-gradient(180deg, #06170f, #0a2418 60%, #071a11);
  --bx-fallback: #071a11;
}""",
    },
    "ocean": {
        "label": "深海",
        "css": """:root {
  --bx-text: #e2f6ff; --bx-muted: #8fb6c9; --bx-accent: #23b5ff; --bx-accent-fg: #00131d;
  --bx-field: rgba(35,181,255,.08); --bx-border: rgba(35,181,255,.26); --bx-danger: #ff6b6b;
  --bx-focus: rgba(35,181,255,.22); --bx-shadow: 0 18px 60px rgba(0,30,60,.45); --bx-radius: 14px;
  --bx-bg: radial-gradient(1000px 700px at 20% -12%, rgba(35,181,255,.24), transparent 62%),
    radial-gradient(800px 600px at 92% 112%, rgba(0,90,160,.36), transparent 62%),
    repeating-linear-gradient(115deg, rgba(255,255,255,.03) 0 2px, transparent 2px 22px),
    linear-gradient(180deg, #031422, #062238 55%, #03121f);
  --bx-fallback: #031422;
}""",
    },
    "sunset": {
        "label": "晚霞",
        "css": """:root {
  --bx-text: #3b1f2b; --bx-muted: #7c5666; --bx-accent: #e0517a; --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,255,255,.5); --bx-border: rgba(160,80,110,.25); --bx-danger: #b3253f;
  --bx-focus: rgba(224,81,122,.2); --bx-shadow: 0 18px 50px rgba(120,50,80,.16); --bx-radius: 16px;
  --bx-bg: radial-gradient(900px 640px at 82% -10%, #ffb37a 0, rgba(255,179,122,0) 60%),
    radial-gradient(760px 560px at 8% 4%, #ff9ec4 0, rgba(255,158,196,0) 58%),
    linear-gradient(180deg, #ffe7f1 0%, #ffd4d8 45%, #ffe9d2 100%);
  --bx-fallback: #ffe7f1;
}""",
    },
    "ink": {
        "label": "水墨",
        "css": """:root {
  --bx-text: #1f2326; --bx-muted: #5d6569; --bx-accent: #2b3136; --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,255,255,.62); --bx-border: rgba(43,49,54,.22); --bx-danger: #8f2d2d;
  --bx-focus: rgba(43,49,54,.16); --bx-shadow: 0 16px 40px rgba(30,35,40,.12); --bx-radius: 2px;
  --bx-font: "Kaiti SC", "STKaiti", "Songti SC", serif;
  --bx-bg: radial-gradient(720px 560px at 78% 8%, rgba(43,49,54,.16), transparent 62%),
    radial-gradient(560px 420px at 18% 70%, rgba(43,49,54,.1), transparent 60%),
    radial-gradient(900px 700px at 50% -20%, #fdfdfb, #eceff0 75%), #f4f5f4;
  --bx-fallback: #f4f5f4;
}""",
    },
    "terminal": {
        "label": "终端绿",
        "css": """:root {
  --bx-text: #b6ffca; --bx-muted: #5f9e72; --bx-accent: #35ff7c; --bx-accent-fg: #00220c;
  --bx-field: rgba(53,255,124,.06); --bx-border: rgba(53,255,124,.24); --bx-danger: #ff5f5f;
  --bx-focus: rgba(53,255,124,.2); --bx-shadow: 0 0 42px rgba(53,255,124,.12); --bx-radius: 4px;
  --bx-font: "Courier New", ui-monospace, Consolas, monospace;
  --bx-bg: repeating-linear-gradient(0deg, rgba(0,0,0,.28) 0 1px, transparent 1px 3px),
    radial-gradient(900px 700px at 50% -10%, rgba(53,255,124,.14), transparent 62%), #020703;
  --bx-fallback: #020703;
}""",
    },
    "candy": {
        "label": "糖果",
        "css": """:root {
  --bx-text: #3a2b45; --bx-muted: #7f6a8c; --bx-accent: #7c5cff; --bx-accent-fg: #ffffff;
  --bx-field: rgba(255,255,255,.55); --bx-border: rgba(150,110,220,.25); --bx-danger: #c03a74;
  --bx-focus: rgba(124,92,255,.2); --bx-shadow: 0 18px 50px rgba(120,90,180,.18); --bx-radius: 20px;
  --bx-bg: radial-gradient(560px 440px at 14% 10%, #ffd6ee 0, rgba(255,214,238,0) 64%),
    radial-gradient(620px 480px at 88% 6%, #cfe6ff 0, rgba(207,230,255,0) 62%),
    radial-gradient(720px 560px at 52% 100%, #d9ffe8 0, rgba(217,255,232,0) 66%),
    linear-gradient(160deg, #fffafd, #f4efff 60%, #effcf9);
  --bx-fallback: #f8f2ff;
}""",
    },
}

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_THEME_CSS = 200_000


class SiteSettings:
    """data/settings.json：限流等可在后台修改、无需重启的设置。"""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self._mtime: float | None = None
        self._checked = 0.0
        self.data = dict(DEFAULT_SETTINGS)
        if not path.exists():
            write_json(path, self.data)
        self._refresh(force=True)

    def _refresh(self, force: bool = False) -> dict:
        now = time.monotonic()
        if not force and now - self._checked < 0.5:
            return self.data
        with self.lock:
            self._checked = now
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                return self.data
            if force or mtime != self._mtime:
                raw = read_json(self.path)
                if isinstance(raw, dict):
                    data = dict(DEFAULT_SETTINGS)
                    data.update(raw)
                    self.data = data
                self._mtime = mtime
        return self.data

    def get(self, key: str, default=None):
        return self._refresh().get(key, DEFAULT_SETTINGS.get(key, default))

    def get_int(self, key: str, minimum: int = 0, maximum: int = 1_000_000) -> int:
        try:
            value = int(self.get(key))
        except (TypeError, ValueError):
            value = int(DEFAULT_SETTINGS.get(key, minimum))
        return min(max(value, minimum), maximum)

    def get_float(self, key: str, minimum: float = 0.0, maximum: float = 3600.0) -> float:
        try:
            value = float(self.get(key))
        except (TypeError, ValueError):
            value = float(DEFAULT_SETTINGS.get(key, minimum))
        return min(max(value, minimum), maximum)

    def update(self, patch: dict) -> dict:
        self._refresh(force=True)
        with self.lock:
            data = dict(self.data)
            data.update(patch)
            write_json(self.path, data)
            self.data = data
            self._mtime = self.path.stat().st_mtime
            return dict(data)


class ThemeStore:
    """data/themes.json：内置 12 种风格 + 后台可改源码/新建主题。"""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self._mtime: float | None = None
        self.themes: dict[str, dict] = {}
        if not path.exists():
            write_json(path, self._file_data())
        self._refresh(force=True)

    def _file_data(self) -> dict:
        return {
            "version": 1,
            "themes": {
                name: {
                    "label": meta["label"],
                    "css": meta["css"],
                    "builtin": True,
                }
                for name, meta in DEFAULT_THEMES.items()
            },
        }

    def _load(self) -> None:
        raw = read_json(self.path)
        themes: dict[str, dict] = {}
        if isinstance(raw, dict) and isinstance(raw.get("themes"), dict):
            for name, meta in raw["themes"].items():
                if not NAME_RE.fullmatch(str(name)) or not isinstance(meta, dict):
                    continue
                css = str(meta.get("css") or "")
                if len(css) > MAX_THEME_CSS:
                    css = css[:MAX_THEME_CSS]
                themes[name] = {
                    "label": str(meta.get("label") or name)[:60],
                    "css": css,
                    "builtin": bool(meta.get("builtin")),
                }
        changed = False
        for name, meta in DEFAULT_THEMES.items():
            if name not in themes:
                themes[name] = {"label": meta["label"], "css": meta["css"], "builtin": True}
                changed = True
            else:
                themes[name]["builtin"] = True
        self.themes = themes
        if changed:
            self._save_locked()

    def _save_locked(self) -> None:
        write_json(self.path, {"version": 1, "themes": self.themes})

    def _refresh(self, force: bool = False) -> dict[str, dict]:
        with self.lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                mtime = None
            if force or mtime != self._mtime:
                self._load()
                self._mtime = mtime
            return self.themes

    def all(self) -> dict[str, dict]:
        with self.lock:
            return {name: dict(meta) for name, meta in self._refresh().items()}

    def names(self) -> list[str]:
        return sorted(self.all())

    def get(self, name: str | None, default: str = "chatgpt") -> tuple[str, dict]:
        with self.lock:
            themes = self._refresh()
            if name and name in themes:
                return name, dict(themes[name])
            if default in themes:
                return default, dict(themes[default])
            first = next(iter(themes))
            return first, dict(themes[first])

    def save(self, name: str, label: str, css: str) -> None:
        name = str(name or "").strip()
        if not NAME_RE.fullmatch(name):
            raise ValueError("主题名只能用小写字母、数字、- 和 _，且以字母开头")
        css = str(css or "")
        if len(css) > MAX_THEME_CSS:
            raise ValueError("主题 CSS 太长（上限 200 KB）")
        with self.lock:
            themes = self._refresh()
            themes[name] = {
                "label": str(label or name).strip()[:60] or name,
                "css": css,
                "builtin": name in DEFAULT_THEMES,
            }
            self._save_locked()
            self._mtime = self.path.stat().st_mtime

    def create(self, name: str, label: str, css: str) -> None:
        name = str(name or "").strip()
        with self.lock:
            if name in self._refresh():
                raise ValueError("同名主题已存在")
        self.save(name, label, css)

    def reset(self, name: str) -> None:
        if name not in DEFAULT_THEMES:
            raise ValueError("只有内置主题可以恢复默认")
        with self.lock:
            self._refresh()
            self.themes[name] = {
                "label": DEFAULT_THEMES[name]["label"],
                "css": DEFAULT_THEMES[name]["css"],
                "builtin": True,
            }
            self._save_locked()
            self._mtime = self.path.stat().st_mtime

    def delete(self, name: str) -> None:
        if name in DEFAULT_THEMES:
            raise ValueError("内置主题不能删除，可以恢复默认源码")
        with self.lock:
            themes = self._refresh()
            if name not in themes:
                raise ValueError("主题不存在")
            themes.pop(name)
            self._save_locked()
            self._mtime = self.path.stat().st_mtime


class PageStyles:
    """data/pages.json：每一关单独的背景主题与自定义 CSS。"""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self._mtime: float | None = None
        self.styles: dict[str, dict] = {}
        if not path.exists():
            write_json(path, {})
        self._refresh()

    def _refresh(self) -> dict[str, dict]:
        with self.lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                mtime = None
            if mtime != self._mtime:
                raw = read_json(self.path)
                styles = {}
                if isinstance(raw, dict):
                    for key, meta in raw.items():
                        try:
                            level = int(key)
                        except (TypeError, ValueError):
                            continue
                        if level < 1 or not isinstance(meta, dict):
                            continue
                        css = str(meta.get("css") or "")[:MAX_THEME_CSS]
                        theme = str(meta.get("theme") or "").strip()[:32]
                        if theme or css:
                            styles[str(level)] = {"theme": theme, "css": css}
                self.styles = styles
                self._mtime = mtime
            return self.styles

    def get(self, level: int) -> dict:
        return dict(self._refresh().get(str(int(level)), {"theme": "", "css": ""}))

    def all(self) -> dict[str, dict]:
        with self.lock:
            return {k: dict(v) for k, v in self._refresh().items()}

    def set(self, level: int, theme: str = "", css: str = "") -> None:
        level = int(level)
        theme = str(theme or "").strip()
        if theme and not NAME_RE.fullmatch(theme):
            raise ValueError("主题名不合法")
        css = str(css or "")
        if len(css) > MAX_THEME_CSS:
            raise ValueError("页面 CSS 太长（上限 200 KB）")
        with self.lock:
            styles = dict(self._refresh())
            if theme or css:
                styles[str(level)] = {"theme": theme, "css": css}
            else:
                styles.pop(str(level), None)
            write_json(self.path, styles)
            self.styles = styles
            self._mtime = self.path.stat().st_mtime


def style_block(theme_css: str, custom_css: str = "") -> str:
    css = BASE_CSS
    if theme_css:
        css += "\n" + theme_css
    if custom_css:
        css += "\n/* 本页自定义背景 */\n" + custom_css
    return f'<style id="bx-style">\n{css}\n</style>'


def inject_background(source: str, theme_css: str, custom_css: str = "") -> str:
    """把主题样式和背景层插进任何页面；站长手写的页面同样生效。"""
    style = style_block(theme_css, custom_css)
    layer = '<div class="bx-bg" aria-hidden="true"></div>'
    lower = source.lower()
    if "</head>" in lower:
        index = lower.rindex("</head>")
        source = source[:index] + style + "\n" + source[index:]
    else:
        source = style + "\n" + source
    match = re.search(r"<body\b[^>]*>", source, re.IGNORECASE)
    if match:
        return source[:match.end()] + layer + source[match.end():]
    return layer + source
