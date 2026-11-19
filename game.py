"""答案哈希本、全网共享的一轮进度、按 Cookie 的多玩家进度与按 IP 限流。"""
from __future__ import annotations

import atexit
import hashlib
import hmac
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 首次启动没有 data/answers.json 时用这 5 层起步，和 copyspace/step1-5.html 对应。
# 服务器只保存/校验 SHA-256：这里直接放哈希，答案原文只存在于题面里。
DEFAULT_ANSWER_HASHES = {
    "1": "fb05e8de46f53f6c5da45a52e4226d9e99709dce2196735ef1c733fe0cc0fc92",
    "2": "2f0958707bae688ea04d8868f7dbd859ade4025b032e200f2867b58bd38d8a45",
    "3": "1e2acda19a91478064de14b711eec4820e6c9d177bb1c4b00f96bb301cc73611",
    "4": "04efaf080f5a3e74e1c29d1ca6a48569382cbbcd324e8d59d2b83ef21c039f00",
    "5": "0edc1a85d0a04a2a77ed3a04881f0f59df1da0a632c26e2c330729f2b50f810b",
}

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PREFIXED_HASH_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class AnswerBook:
    """level -> SHA-256 哈希；直接读 data/answers.json，文件一改就热加载。

    文件格式 v2：{"version": 2, "hashes": {"1": "<64 hex>", ...}}。
    旧版明文文件首次读取时自动改写为哈希，服务器不再保留答案原文。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.path = ROOT / str(cfg["game"].get("answers_file", "data/answers.json"))
        self.max_length = int(cfg["security"].get("max_guess_length", 64))
        self.lock = threading.RLock()
        self._mtime: float | None = None
        self._hashes: dict[str, str] = {}
        if not self.path.exists():
            write_json(self.path, {"version": 2, "hashes": dict(DEFAULT_ANSWER_HASHES)})
        self._refresh()

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_hash(value: str) -> str | None:
        value = str(value or "").strip()
        match = PREFIXED_HASH_RE.fullmatch(value)
        if match:
            return match.group(1).lower()
        if HASH_RE.fullmatch(value.lower()):
            return value.lower()
        return None

    def _normalize(self, raw) -> tuple[dict[str, str], bool]:
        """返回 (hashes, 是否需要迁移旧格式)。"""
        if not isinstance(raw, dict):
            return {}, False
        if isinstance(raw.get("hashes"), dict):
            values = raw["hashes"]
            legacy = raw.get("version") != 2
        else:
            values = raw
            legacy = True
        hashes: dict[str, str] = {}
        for key, value in values.items():
            if str(key) in ("version", "hashes"):
                continue
            try:
                level = int(key)
            except (TypeError, ValueError):
                continue
            if level < 1:
                continue
            digest = self.normalize_hash(value)
            if digest is None:
                text = str(value).strip()
                if not text or not re.fullmatch(r"\S+", text) or len(text) > self.max_length:
                    continue
                digest = self.hash_text(text)
            hashes[str(level)] = digest
        return hashes, legacy

    def _refresh(self) -> dict[str, str]:
        with self.lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                mtime = None
            if mtime != self._mtime:
                raw = read_json(self.path)
                if raw is not None:
                    hashes, legacy = self._normalize(raw)
                    self._hashes = hashes
                    self._mtime = mtime
                    if legacy:
                        self._persist_locked(hashes)
                else:
                    self._mtime = mtime
            return self._hashes

    def _persist_locked(self, hashes: dict[str, str]) -> None:
        ordered = {
            str(level): hashes[str(level)]
            for level in sorted((int(k) for k in hashes))
        }
        write_json(self.path, {"version": 2, "hashes": ordered})
        self._hashes = dict(ordered)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = None

    def all(self) -> dict[str, str]:
        with self.lock:
            return dict(self._refresh())

    def hash_for(self, level: int) -> str | None:
        return self.all().get(str(int(level)))

    def total_levels(self) -> int:
        return max((int(k) for k in self.all()), default=0)

    def verify(self, level: int, submitted_hash: str) -> bool:
        expected = self.hash_for(level)
        digest = self.normalize_hash(submitted_hash)
        if expected is None or digest is None:
            return False
        return hmac.compare_digest(digest, expected)

    def set_answer(self, level: int, answer: str) -> str:
        """接受明文（转成哈希）或显式的 sha256:<hex>；返回保存的哈希。"""
        raw = str(answer or "").strip()
        level = int(level)
        if level < 1:
            raise ValueError("关卡从 1 开始")
        if not raw:
            raise ValueError("答案不能为空")
        digest = None
        if raw.startswith("sha256:"):
            digest = self.normalize_hash(raw)
            if digest is None:
                raise ValueError("sha256: 后面需要 64 位十六进制")
        else:
            if not re.fullmatch(r"\S+", raw):
                raise ValueError("答案不能包含空格或换行")
            if len(raw) > self.max_length:
                raise ValueError(f"答案最长 {self.max_length} 个字符")
            digest = self.hash_text(raw)
        with self.lock:
            hashes = dict(self._refresh())
            hashes[str(level)] = digest
            self._persist_locked(hashes)
            return digest


class GameState:
    """state.json：全网当前开到第几层、总猜测数、每层统计和最近尝试。"""

    def __init__(self, cfg: dict, book: AnswerBook):
        self.cfg = cfg
        self.book = book
        self.path = ROOT / str(cfg["game"].get("state_file", "data/state.json"))
        self.log_size = max(1, int(cfg["game"].get("log_size", 200)))
        self.flush_interval = max(0.2, float(cfg["game"].get("state_flush_seconds", 1.5)))
        self.lock = threading.Lock()
        self._dirty = False
        self._last_save = 0.0
        self._stop = threading.Event()
        self.state = self._load()
        self._writer = threading.Thread(target=self._persist_loop, name="bx-state", daemon=True)
        self._writer.start()
        atexit.register(self.close)

    def _blank(self) -> dict:
        return {
            "version": 2,
            "current_step": 1,
            "total_guesses": 0,
            "levels": {},
            "guess_log": [],
            "created_at": now_iso(),
        }

    def _load(self) -> dict:
        data = read_json(self.path)
        if isinstance(data, dict) and data.get("version") == 2:
            data.setdefault("current_step", 1)
            data.setdefault("total_guesses", 0)
            data.setdefault("levels", {})
            data.setdefault("guess_log", [])
            data.setdefault("created_at", now_iso())
            return data
        if self.path.exists():
            # 旧版聊天界面 / 管理面板的状态，留个备份后从第一层重新开始。
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".old"))
            except OSError:
                pass
        state = self._blank()
        self._save(state)
        return state

    def _save(self, state: dict) -> None:
        write_json(self.path, state)
        self._dirty = False
        self._last_save = time.monotonic()

    def _touch_locked(self) -> None:
        self._dirty = True
        now = time.monotonic()
        if now - self._last_save >= self.flush_interval:
            self._save(self.state)

    def _persist_loop(self) -> None:
        while not self._stop.wait(0.5):
            with self.lock:
                if self._dirty:
                    self._save(self.state)

    def close(self) -> None:
        self._stop.set()
        with self.lock:
            if self._dirty:
                self._save(self.state)

    def _global_step(self, total: int) -> int:
        return min(max(1, int(self.state.get("current_step", 1))), total + 1)

    def _own_step(self, cleared: int, total: int) -> int:
        return min(max(0, int(cleared)) + 1, self._global_step(total))

    def progress(self, cleared: int) -> dict:
        with self.lock:
            total = self.book.total_levels()
            global_step = self._global_step(total)
            own = self._own_step(cleared, total)
            finished = total == 0 or own > total
            return {
                "ok": True,
                "step": own,
                "cleared": max(0, int(cleared)),
                "global_step": global_step,
                "total_levels": total,
                "finished": finished,
                "total_guesses": int(self.state.get("total_guesses", 0)),
            }

    def submit(self, step: int, submitted_hash: str, cleared: int, player_id: str) -> dict:
        with self.lock:
            total = self.book.total_levels()
            own = self._own_step(cleared, total)
            if total == 0:
                return {"ok": False, "error": "no_levels",
                        "message": "还没有配置关卡。"}
            if own > total:
                return {"ok": False, "error": "finished",
                        "message": "全部关卡已通过。"}
            if step != own:
                return {"ok": False, "error": "wrong_step", "currentStep": own,
                        "message": f"你当前在第 {own} 关。"}

            if self.book.hash_for(own) is None:
                return {"ok": False, "error": "no_level", "currentStep": own,
                        "message": f"第 {own} 关还没有答案。"}

            digest = AnswerBook.normalize_hash(submitted_hash)
            correct = False
            if digest is not None:
                expected = self.book.hash_for(own)
                correct = hmac.compare_digest(digest, str(expected))
            rec = self.state["levels"].setdefault(str(own), {
                "guesses": 0, "solved": False, "solved_at": None, "solved_by": None,
            })
            rec["guesses"] = int(rec.get("guesses", 0)) + 1
            self.state["total_guesses"] = int(self.state.get("total_guesses", 0)) + 1
            self.state["guess_log"].append({
                "at": now_iso(),
                "level": own,
                "pid": player_id,
                "hash": (digest or "")[:64],
                "correct": correct,
            })
            self.state["guess_log"] = self.state["guess_log"][-self.log_size:]

            result = {
                "ok": True,
                "correct": correct,
                "level": own,
                "cleared": max(0, int(cleared)),
                "total_guesses": self.state["total_guesses"],
                "level_guesses": rec["guesses"],
            }
            if correct:
                rec["solved"] = True
                rec["solved_at"] = now_iso()
                rec["solved_by"] = player_id
                if own == int(self.state.get("current_step", 1)):
                    self.state["current_step"] = own + 1
                result["cleared"] = own
                next_step = min(own + 1, self._global_step(total))
                if next_step > total:
                    result["finished"] = True
                    result["message"] = "正确！全部关卡已通过。"
                else:
                    result["next"] = f"/game/{next_step}"
                    result["message"] = f"正确！进入第 {next_step} 关。"
            else:
                result["message"] = "密码不对。"

            self._touch_locked()
            return result

    # -- 命令行管理 -------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            total = self.book.total_levels()
            hashes = self.book.all()
            levels = []
            for level in range(1, total + 1):
                rec = self.state["levels"].get(str(level), {})
                levels.append({
                    "level": level,
                    "hash": hashes.get(str(level)),
                    "guesses": int(rec.get("guesses", 0)),
                    "solved": bool(rec.get("solved")),
                    "solved_at": rec.get("solved_at"),
                })
            return {
                "current_step": self._global_step(total),
                "total_levels": total,
                "total_guesses": int(self.state.get("total_guesses", 0)),
                "levels": levels,
                "guess_log": list(self.state.get("guess_log", [])),
                "state_file": str(self.path),
            }

    def reset(self) -> None:
        with self.lock:
            self.state = self._blank()
            self._save(self.state)

    def goto(self, level: int) -> int:
        level = max(1, int(level))
        with self.lock:
            self.state["current_step"] = level
            for key, rec in self.state["levels"].items():
                if int(key) >= level and rec.get("solved"):
                    rec["solved"] = False
                    rec["solved_at"] = None
                    rec["solved_by"] = None
            self._save(self.state)
            return level

    def reset_solved(self, level: int) -> None:
        with self.lock:
            rec = self.state["levels"].get(str(int(level)))
            if rec:
                rec["solved"] = False
                rec["solved_at"] = None
                rec["solved_by"] = None
                self._save(self.state)


class IPRateLimiter:
    """按 IP 的固定窗口限流（默认 5 秒 20 次），阈值可在后台实时修改。

    127.0.0.1 等回环地址由调用方直接放行，不经过这里。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.windows: dict[str, list[float]] = {}

    def check(self, ip: str, limit: int, window: float) -> tuple[bool, float]:
        limit = max(1, int(limit))
        window = max(0.5, float(window))
        key = str(ip or "unknown")[:45]
        now = time.monotonic()
        with self.lock:
            slot = self.windows.get(key)
            if slot is None or now - slot[0] >= window:
                self.windows[key] = [now, 1.0]
                if len(self.windows) > 8192:
                    self._prune_locked(now, window)
                return True, 0.0
            if slot[1] >= limit:
                return False, window - (now - slot[0])
            slot[1] += 1
            return True, 0.0

    def _prune_locked(self, now: float, window: float) -> None:
        stale = [key for key, slot in self.windows.items() if now - slot[0] >= window * 2]
        for key in stale:
            self.windows.pop(key, None)

