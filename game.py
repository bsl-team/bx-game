"""答案本、全网共享的一轮进度、按 Cookie 的多玩家进度与限流。"""
from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 首次启动没有 data/answers.json 时用这 5 层起步，和 levels/step1-5.html 对应。
DEFAULT_ANSWERS = {
    "1": "219845122340",
    "2": "nbaf",
    "3": "3ff1726c",
    "4": "four",
    "5": "219845122854",
}


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
    """level -> answer，直接读 data/answers.json；文件一改就热加载。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.path = ROOT / str(cfg["game"].get("answers_file", "data/answers.json"))
        self.max_length = int(cfg["security"].get("max_guess_length", 64))
        self.lock = threading.Lock()
        self._mtime: float | None = None
        self._answers: dict[str, str] = {}
        if not self.path.exists():
            write_json(self.path, DEFAULT_ANSWERS)
        self._refresh()

    def _normalize(self, raw) -> dict[str, str]:
        answers: dict[str, str] = {}
        if not isinstance(raw, dict):
            return answers
        for key, value in raw.items():
            try:
                level = int(key)
            except (TypeError, ValueError):
                continue
            if level < 1:
                continue
            answer = str(value).strip()
            if answer and re.fullmatch(r"\S+", answer) and len(answer) <= self.max_length:
                answers[str(level)] = answer
        return answers

    def _refresh(self) -> dict[str, str]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            raw = read_json(self.path)
            if raw is not None:
                self._answers = self._normalize(raw)
            self._mtime = mtime
        return self._answers

    def all(self) -> dict[str, str]:
        with self.lock:
            return dict(self._refresh())

    def answer_for(self, level: int) -> str | None:
        return self.all().get(str(int(level)))

    def total_levels(self) -> int:
        return max((int(k) for k in self.all()), default=0)

    def set_answer(self, level: int, answer: str) -> str:
        answer = str(answer or "").strip()
        level = int(level)
        if level < 1:
            raise ValueError("关卡从 1 开始")
        if not answer:
            raise ValueError("答案不能为空")
        if not re.fullmatch(r"\S+", answer):
            raise ValueError("答案不能包含空格或换行")
        if len(answer) > self.max_length:
            raise ValueError(f"答案最长 {self.max_length} 个字符")
        with self.lock:
            answers = self._refresh()
            answers[str(level)] = answer
            write_json(self.path, dict(sorted(answers.items(), key=lambda kv: int(kv[0]))))
            self._mtime = self.path.stat().st_mtime
            return answer


class GameState:
    """state.json：全网当前开到第几层、总猜测数、每层统计和最近尝试。"""

    def __init__(self, cfg: dict, book: AnswerBook):
        self.cfg = cfg
        self.book = book
        self.path = ROOT / str(cfg["game"].get("state_file", "data/state.json"))
        self.log_size = max(1, int(cfg["game"].get("log_size", 200)))
        self.lock = threading.Lock()
        self.state = self._load()

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
                "step": None if finished else own,
                "cleared": max(0, int(cleared)),
                "global_step": global_step,
                "total_levels": total,
                "finished": finished,
                "total_guesses": int(self.state.get("total_guesses", 0)),
            }

    def submit(self, step: int, password: str, cleared: int, player_id: str) -> dict:
        with self.lock:
            total = self.book.total_levels()
            own = self._own_step(cleared, total)
            if total == 0:
                return {"ok": False, "error": "no_levels", "restart": "/",
                        "message": "还没有配置关卡。"}
            if own > total:
                return {"ok": False, "error": "finished", "restart": "/",
                        "message": "全部关卡已通过。"}
            if step != own:
                return {"ok": False, "error": "wrong_step", "currentStep": own,
                        "message": f"你当前在第 {own} 关。"}

            answer = self.book.answer_for(own)
            if answer is None:
                return {"ok": False, "error": "no_level", "currentStep": own,
                        "message": f"第 {own} 关还没有答案。"}

            correct = hmac.compare_digest(password.encode("utf-8"), answer.encode("utf-8"))
            rec = self.state["levels"].setdefault(str(own), {
                "guesses": 0, "solved": False, "solved_at": None, "solved_by": None,
            })
            rec["guesses"] = int(rec.get("guesses", 0)) + 1
            self.state["total_guesses"] = int(self.state.get("total_guesses", 0)) + 1
            self.state["guess_log"].append({
                "at": now_iso(),
                "level": own,
                "pid": player_id,
                "guess": password,
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
                    result["next"] = f"/step/{next_step}"
                    result["message"] = f"正确！进入第 {next_step} 层。"
            else:
                result["message"] = "密码不对。"

            self._save(self.state)
            return result

    # -- 命令行管理 -------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            total = self.book.total_levels()
            answers = self.book.all()
            levels = []
            for level in range(1, total + 1):
                rec = self.state["levels"].get(str(level), {})
                levels.append({
                    "level": level,
                    "answer": answers.get(str(level)),
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


class RateLimiter:
    """按玩家 Cookie 限流；单进程内存实现，所以只能跑一个 worker。"""

    def __init__(self, cfg: dict):
        sec = cfg["security"]
        self.window = 60.0
        self.limit = max(1, int(sec.get("guess_rate_limit_per_minute", 30)))
        self.min_interval = max(0.0, int(sec.get("guess_min_interval_ms", 0)) / 1000.0)
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def check(self, player_id: str) -> tuple[bool, float]:
        now = time.monotonic()
        with self.lock:
            stamps = [t for t in self.hits.get(player_id, []) if now - t < self.window]
            if stamps and now - stamps[-1] < self.min_interval:
                self.hits[player_id] = stamps
                return False, self.min_interval - (now - stamps[-1])
            if len(stamps) >= self.limit:
                self.hits[player_id] = stamps
                return False, self.window - (now - stamps[0])
            stamps.append(now)
            self.hits[player_id] = stamps
            return True, 0.0
