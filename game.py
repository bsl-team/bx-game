"""Game state: password generation, hashing, rounds and the shared guess counter."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import string
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ALPHABETS = {
    "digits": string.digits,
    "lower": string.ascii_lowercase,
    "upper": string.ascii_uppercase,
    "hex": string.hexdigits[:16],
    "alnum_lower": string.ascii_lowercase + string.digits,
    "alnum": string.ascii_letters + string.digits,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 题库第 i 题对应第 i+1 关；答案不含空格，玩家可以提交。
DEFAULT_QUESTIONS = [
    {"prompt": "本站梗题：计算算式结果 114514 × 1919810", "answer": "219845122340",
     "hint": "答案是一串纯数字，把两个数相乘。"},
    {"prompt": "把单词 maze 的每个字母都往后挪一位（a→b，y→z，z→a）", "answer": "nbaf",
     "hint": "四个小写字母。"},
    {"prompt": "字符串 bx 的 SHA-256 十六进制摘要的前 8 位（小写）", "answer": "3ff1726c",
     "hint": "八位十六进制字符，0-9 和 a-f。"},
    {"prompt": "本关的关卡序号用英文怎么说？（全小写单词）", "answer": "four",
     "hint": "想一想你现在在第几关。"},
    {"prompt": "上一题的答案（十进制）与 114514 的和：219845122340 + 114514", "answer": "219845122854",
     "hint": "只要做一次加法。"},
]


class QuestionBank:
    """Ordered puzzle list; item i is the question used by level i+1."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.path = ROOT / str(cfg["rounds"].get("questions_file", "data/questions.json"))
        self.lock = threading.Lock()
        self.items = self._read()

    @staticmethod
    def max_answer_length(cfg: dict) -> int:
        return int(cfg["security"].get("max_guess_length", 64))

    def normalize(self, item: dict) -> dict:
        prompt = str(item.get("prompt", "")).strip()
        answer = str(item.get("answer", "")).strip()
        hint = str(item.get("hint", "")).strip()
        if not prompt:
            raise ValueError("题面不能为空")
        if not answer:
            raise ValueError("答案不能为空")
        if not re.fullmatch(r"\S+", answer):
            raise ValueError("答案不能包含空格或换行")
        if len(answer) > self.max_answer_length(self.cfg):
            raise ValueError(f"答案最长 {self.max_answer_length(self.cfg)} 个字符")
        return {"prompt": prompt, "answer": answer, "hint": hint}

    def _read(self) -> list[dict]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [self.normalize(item) for item in data if isinstance(item, dict)]
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        return [dict(q) for q in DEFAULT_QUESTIONS]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def count(self) -> int:
        return len(self.items)

    def get(self, level: int) -> dict | None:
        index = int(level) - 1
        return self.items[index] if 0 <= index < len(self.items) else None

    def upsert(self, index: int, prompt: str, answer: str, hint: str = "") -> dict:
        """1-based index; appending past the end fills gaps with blanks rejected."""
        index = int(index)
        item = self.normalize({"prompt": prompt, "answer": answer, "hint": hint})
        with self.lock:
            if index < 1 or index > len(self.items) + 1:
                raise ValueError(f"题目序号只能是 1-{len(self.items) + 1}")
            if index == len(self.items) + 1:
                self.items.append(item)
            else:
                self.items[index - 1] = item
            self._save()
            return item

    def delete(self, index: int) -> dict:
        index = int(index)
        with self.lock:
            if not 1 <= index <= len(self.items):
                raise ValueError("题目序号不存在")
            removed = self.items.pop(index - 1)
            self._save()
            return removed


def make_password(cfg: dict, length: int, charset: str | None = None) -> str:
    pc = cfg["password"]
    name = charset or pc.get("charset", "lower")
    alphabet = ALPHABETS.get(name, string.ascii_lowercase)
    if len(alphabet) < 2:
        alphabet = string.ascii_lowercase
    avoid = bool(pc.get("avoid_repeats", True))
    out: list[str] = []
    for _ in range(max(1, int(length))):
        pool = alphabet
        if avoid and out:
            pool = alphabet.replace(out[-1], "") or alphabet
        out.append(secrets.choice(pool))
    return "".join(out)


def build_hash(secret: str, algorithm: str, iterations: int) -> dict:
    algo = str(algorithm or "pbkdf2_sha256").lower()
    salt = secrets.token_hex(16)
    if algo.startswith("pbkdf2_"):
        digest = algo.split("_", 1)[1] or "sha256"
        digest_hex = hashlib.pbkdf2_hmac(
            digest, secret.encode(), bytes.fromhex(salt), max(1000, int(iterations))
        ).hex()
    elif algo in getattr(hashlib, "algorithms_available", set()):
        digest_hex = hashlib.new(algo, (salt + secret).encode()).hexdigest()
    else:
        raise ValueError(f"unsupported hash_algorithm: {algo}")
    return {"algorithm": algo, "salt": salt, "hash": digest_hex, "iterations": max(1000, int(iterations))}


def hash_secret(secret: str, cfg: dict) -> dict:
    sec = cfg["security"]
    return build_hash(
        secret, sec.get("hash_algorithm", "pbkdf2_sha256"), int(sec.get("hash_iterations", 120000))
    )


def verify_secret(secret: str, record: dict) -> bool:
    salt = record.get("salt", "")
    algo = str(record.get("algorithm", "pbkdf2_sha256")).lower()
    expected = record.get("hash", "")
    try:
        if algo.startswith("pbkdf2_"):
            digest = algo.split("_", 1)[1] or "sha256"
            got = hashlib.pbkdf2_hmac(
                digest, secret.encode(), bytes.fromhex(salt), int(record.get("iterations", 0) or 120000)
            ).hex()
        else:
            got = hashlib.new(algo, (salt + secret).encode()).hexdigest()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def length_for_level(cfg: dict, level: int) -> int:
    pc = cfg["password"]
    base = max(1, int(pc.get("level1_length", 4)))
    step = max(0, int(pc.get("length_step", 1)))
    cap = max(base, int(pc.get("length_max", 8)))
    return min(base + step * (max(1, int(level)) - 1), cap)


class GameStore:
    """Global round state, persisted as JSON so every player shares one puzzle."""

    def __init__(self, cfg: dict, bank: QuestionBank | None = None):
        self.cfg = cfg
        self.path = ROOT / str(cfg["rounds"].get("state_file", "data/state.json"))
        self.bank = bank if bank is not None else QuestionBank(cfg)
        self.lock = threading.Lock()
        self.state = self._load()

    @property
    def mode(self) -> str:
        return str(self.cfg["rounds"].get("mode", "puzzle")).lower()

    # -- persistence ------------------------------------------------------
    def _blank(self) -> dict:
        return {
            "version": 1,
            "current_level": 1,
            "total_guesses": 0,
            "levels": {},
            "solved_log": [],
            "guess_log": [],
            "created_at": now_iso(),
        }

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = None
            if isinstance(data, dict) and data.get("levels"):
                data.setdefault("solved_log", [])
                data.setdefault("guess_log", [])
                data.setdefault("total_guesses", 0)
                data.setdefault("current_level", max(int(k) for k in data["levels"]))
                return data
        state = self._blank()
        self._create_level(state, 1)
        self._save(state)
        return state

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- levels -----------------------------------------------------------
    @property
    def reveal_passwords(self) -> bool:
        return bool(self.cfg.get("admin", {}).get("reveal_passwords", True))

    def _record(self, password: str, charset: str | None, base: dict | None = None,
                prompt: str | None = None, hint: str | None = None, source: str = "random") -> dict:
        rec = dict(base or {})
        rec.update(hash_secret(password, self.cfg))
        rec["length"] = len(password)
        rec["charset"] = charset or self.cfg["password"].get("charset", "lower")
        rec["plain"] = password if self.reveal_passwords else None
        rec["prompt"] = prompt
        rec["hint"] = hint
        rec["source"] = source
        rec.setdefault("custom", False)
        rec.setdefault("guesses", 0)
        rec.setdefault("created_at", now_iso())
        rec.setdefault("solved", False)
        rec.setdefault("solved_at", None)
        rec.setdefault("solved_by", None)
        return rec

    def _from_bank(self, level: int) -> tuple[str, str, str | None] | None:
        """Puzzle mode uses the question's answer; random mode (or a missing question) uses None."""
        if self.mode != "puzzle":
            return None
        question = self.bank.get(level)
        if not question:
            return None
        return question["answer"], question["prompt"], question.get("hint") or None

    def _create_level(self, state: dict, level: int, password: str | None = None,
                      length: int | None = None, charset: str | None = None) -> dict:
        banked = None if password is not None else self._from_bank(level)
        if banked:
            password, prompt, hint = banked
            state["levels"][str(level)] = self._record(password, charset, prompt=prompt, hint=hint,
                                                       source="question")
        else:
            if password is None:
                password = make_password(self.cfg, int(length or length_for_level(self.cfg, level)), charset)
            state["levels"][str(level)] = self._record(password, charset, source="random")
        return state["levels"][str(level)]

    @property
    def max_levels(self) -> int:
        return int(self.cfg["rounds"].get("max_levels", 0) or 0)

    def status(self, cleared: int) -> dict:
        with self.lock:
            self.ensure_current_level_locked()
            max_levels = self.max_levels
            finished = max_levels > 0 and cleared >= max_levels
            level = None if finished else min(int(cleared) + 1, self.state["current_level"])
            rec = self.state["levels"].get(str(level), {}) if level else {}
            last = self.state["solved_log"][-1] if self.state["solved_log"] else None
            return {
                "level": level,
                "cleared": cleared,
                "global_level": self.state["current_level"],
                "prompt": rec.get("prompt"),
                "hint": rec.get("hint"),
                "source": rec.get("source", "random"),
                "mode": self.mode,
                "password_length": rec.get("length"),
                "level_guesses": rec.get("guesses", 0),
                "total_guesses": self.state["total_guesses"],
                "level_solved": bool(rec.get("solved")),
                "max_levels": max_levels,
                "finished": finished,
                "last_solve": last,
                "solved_count": len(self.state["solved_log"]),
            }

    def ensure_current_level_locked(self) -> None:
        if str(self.state["current_level"]) not in self.state["levels"]:
            self._create_level(self.state, self.state["current_level"])
            self._save(self.state)

    def submit_guess(self, guess: str, cleared: int, player_id: str) -> dict:
        guess = guess.strip()
        max_len = int(self.cfg["security"].get("max_guess_length", 64))
        if not guess:
            return {"ok": False, "error": "empty", "message": "请输入密码"}
        if len(guess) > max_len:
            return {"ok": False, "error": "too_long", "message": f"最长 {max_len} 个字符"}
        if not re.fullmatch(r"\S+", guess):
            return {"ok": False, "error": "charset", "message": "不能包含空格或换行"}

        with self.lock:
            self.ensure_current_level_locked()
            max_levels = self.max_levels
            if max_levels > 0 and cleared >= max_levels:
                return {"ok": False, "error": "finished", "message": "全部关卡已通过"}
            level = min(int(cleared) + 1, self.state["current_level"])
            rec = self.state["levels"][str(level)]
            correct = verify_secret(guess, rec)

            self.state["total_guesses"] += 1
            rec["guesses"] += 1
            log_guess_text = bool(self.cfg.get("admin", {}).get("log_guess_text", True))
            entry = {
                "at": now_iso(),
                "level": level,
                "pid": player_id,
                "length": len(guess),
                "guess": guess if log_guess_text else None,
                "correct": correct,
            }
            self.state["guess_log"].append(entry)
            self.state["guess_log"] = self.state["guess_log"][-int(
                self.cfg.get("admin", {}).get("guess_log_size", 500)
            ):]
            result = {
                "ok": True,
                "correct": correct,
                "level": level,
                "length_matches": len(guess) == rec["length"],
                "total_guesses": self.state["total_guesses"],
                "level_guesses": rec["guesses"],
                "advanced": False,
                "message": "",
            }

            if correct:
                was_open = not rec["solved"]
                rec["solved"] = True
                rec["solved_at"] = now_iso()
                rec["solved_by"] = player_id
                if was_open:
                    self.state["solved_log"].append(
                        {"level": level, "at": rec["solved_at"], "by": player_id,
                         "total_guesses": self.state["total_guesses"]}
                    )
                    self.state["solved_log"] = self.state["solved_log"][-200:]
                if was_open and self.cfg["rounds"].get("auto_advance", True) and level >= self.state["current_level"]:
                    capped = max_levels > 0 and level >= max_levels
                    if not capped:
                        self.state["current_level"] = level + 1
                        self._create_level(self.state, self.state["current_level"])
                        result["advanced"] = True
                        result["next_level"] = self.state["current_level"]
                        result["next_length"] = self.state["levels"][str(self.state["current_level"])]["length"]
                    else:
                        result["finished"] = True
                result["message"] = "正确！" + ("自动进入下一关。" if result["advanced"] else "")
            else:
                result["message"] = "不对，再试一次。" if result["length_matches"] else "不对，长度也不对。"

            self._save(self.state)
            return result

    # -- admin ------------------------------------------------------------
    def _level_row(self, key: str, rec: dict) -> dict:
        return {
            "level": int(key),
            "password": rec.get("plain") if self.reveal_passwords else None,
            "password_stored": bool(rec.get("plain")),
            "prompt": rec.get("prompt"),
            "hint": rec.get("hint"),
            "source": rec.get("source", "random"),
            "length": rec.get("length"),
            "charset": rec.get("charset"),
            "custom": bool(rec.get("custom")),
            "guesses": rec.get("guesses", 0),
            "solved": bool(rec.get("solved")),
            "solved_at": rec.get("solved_at"),
            "solved_by": rec.get("solved_by"),
            "created_at": rec.get("created_at"),
            "password_set_at": rec.get("password_set_at"),
            "algorithm": rec.get("algorithm"),
            "iterations": rec.get("iterations"),
            "is_current": int(key) == self.state["current_level"],
        }

    def _validate_password(self, password: str) -> str:
        password = (password or "").strip()
        max_len = int(self.cfg["security"].get("max_guess_length", 64))
        if not password:
            raise ValueError("密码不能为空")
        if not re.fullmatch(r"\S+", password):
            raise ValueError("密码不能包含空格或换行")
        if len(password) > max_len:
            raise ValueError(f"密码最长 {max_len} 个字符")
        return password

    def _validate_charset(self, charset: str | None) -> str | None:
        if charset in (None, ""):
            return None
        if charset not in ALPHABETS:
            raise ValueError(f"未知字符集 {charset}，可选：{'、'.join(ALPHABETS)}")
        return charset

    def admin_overview(self) -> dict:
        with self.lock:
            rows = [self._level_row(k, self.state["levels"][k])
                    for k in sorted(self.state["levels"], key=int)]
            log = list(reversed(self.state["guess_log"][-100:]))
            hour_ago = datetime.now(timezone.utc).timestamp() - 3600
            recent_hour = sum(
                1 for e in self.state["guess_log"]
                if datetime.fromisoformat(e["at"]).timestamp() > hour_ago
            )
            players = {e.get("pid") for e in self.state["guess_log"] if e.get("pid")}
            solved_levels = sum(1 for k in self.state["levels"] if self.state["levels"][k].get("solved"))
            return {
                "levels": rows,
                "current_level": self.state["current_level"],
                "total_guesses": self.state["total_guesses"],
                "level_guess_sum": sum(int(r["guesses"]) for r in rows),
                "solved_levels": solved_levels,
                "solved_count": len(self.state["solved_log"]),
                "guesses_last_hour": recent_hour,
                "distinct_players": len(players),
                "max_levels": self.max_levels,
                "reveal_passwords": self.reveal_passwords,
                "mode": self.mode,
                "questions_count": self.bank.count(),
                "questions_file": str(self.bank.path),
                "question_source": str(self.bank.path.relative_to(ROOT)) if self.bank.path.is_relative_to(ROOT) else str(self.bank.path),
                "guess_log": log,
                "solved_log": list(reversed(self.state["solved_log"][-30:])),
                "created_at": self.state.get("created_at"),
                "state_file": str(self.path),
                "state_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            }

    def admin_set_password(self, level: int, password: str) -> dict:
        password = self._validate_password(password)
        level = int(level)
        with self.lock:
            rec = self.state["levels"].get(str(level))
            if rec is None:
                raise KeyError(level)
            charset = rec.get("charset") if rec.get("charset") in ALPHABETS else None
            self.state["levels"][str(level)] = self._record(
                password, charset,
                base={**rec, "custom": True, "solved": False, "solved_at": None,
                      "solved_by": None, "password_set_at": now_iso()},
            )
            self._save(self.state)
            return {"level": level, "password": password if self.reveal_passwords else None,
                    "length": len(password), "custom": True}

    def admin_regenerate(self, level: int, length: int | None = None, charset: str | None = None) -> dict:
        name = self._validate_charset(charset)
        level = int(level)
        with self.lock:
            rec = self.state["levels"].get(str(level))
            if rec is None:
                raise KeyError(level)
            size = int(length) if length else int(rec.get("length") or length_for_level(self.cfg, level))
            if not 1 <= size <= 64:
                raise ValueError("长度需在 1-64 之间")
            password = make_password(self.cfg, size, name)
            self.state["levels"][str(level)] = self._record(
                password, name,
                base={**rec, "custom": False, "solved": False, "solved_at": None,
                      "solved_by": None, "password_set_at": now_iso()},
            )
            self._save(self.state)
            return {"level": level, "password": password if self.reveal_passwords else None,
                    "length": size, "charset": name or rec.get("charset")}

    def admin_add_levels(self, count: int, length: int | None = None,
                        charset: str | None = None, password: str | None = None) -> dict:
        name = self._validate_charset(charset)
        count = max(1, min(50, int(count)))
        fixed = self._validate_password(password) if password else None
        with self.lock:
            top = max(int(k) for k in self.state["levels"])
            made = []
            for offset in range(1, count + 1):
                level = top + offset
                size = int(length) if length else length_for_level(self.cfg, level)
                if not 1 <= size <= 64:
                    raise ValueError("长度需在 1-64 之间")
                secret = fixed if fixed else make_password(self.cfg, size, name)
                self._create_level(self.state, level, password=secret, charset=name)
                made.append({"level": level, "length": size,
                             "password": secret if self.reveal_passwords else None})
            self._save(self.state)
            return {"added": made, "current_level": self.state["current_level"]}

    def admin_set_current_level(self, level: int) -> dict:
        level = max(1, int(level))
        with self.lock:
            if str(level) not in self.state["levels"]:
                self._create_level(self.state, level)
            self.state["current_level"] = level
            self._save(self.state)
            return {"current_level": level,
                    "password_length": self.state["levels"][str(level)]["length"]}

    def admin_reset_counters(self, level: int | None = None) -> dict:
        """Zero guess counters; the attempt log is kept so history stays auditable."""
        with self.lock:
            if level is None:
                self.state["total_guesses"] = 0
                for rec in self.state["levels"].values():
                    rec["guesses"] = 0
            else:
                rec = self.state["levels"].get(str(int(level)))
                if rec is None:
                    raise KeyError(level)
                rec["guesses"] = 0
            self._save(self.state)
            return {"total_guesses": self.state["total_guesses"],
                   "log_entries_kept": len(self.state["guess_log"])}

    def admin_purge_plaintext(self) -> int:
        """Drop stored plaintext, e.g. when admin.reveal_passwords is turned off."""
        cleared = 0
        with self.lock:
            for rec in self.state["levels"].values():
                if rec.get("plain") is not None:
                    rec["plain"] = None
                    cleared += 1
            if cleared:
                self._save(self.state)
        return cleared

    # -- questions / per-level view ---------------------------------------
    def level_view(self, level: int, cleared: int) -> dict:
        """Read one level's question card; locked levels stay locked server-side."""
        level = int(level)
        with self.lock:
            self.ensure_current_level_locked()
            own = min(int(cleared) + 1, self.state["current_level"])
            if level > own:
                raise PermissionError(level)
            rec = self.state["levels"].get(str(level))
            if rec is None:
                raise KeyError(level)
            return {
                "level": level,
                "prompt": rec.get("prompt"),
                "hint": rec.get("hint"),
                "has_hint": bool(rec.get("hint")),
                "password_length": rec.get("length"),
                "source": rec.get("source", "random"),
                "solved": bool(rec.get("solved")),
                "solved_at": rec.get("solved_at"),
                "guesses": rec.get("guesses", 0),
                "is_current": level == self.state["current_level"],
                "is_playable": level == own,
                "own_level": own,
                "global_level": self.state["current_level"],
                "total_guesses": self.state["total_guesses"],
                "mode": self.mode,
            }

    def progress(self, cleared: int) -> dict:
        with self.lock:
            self.ensure_current_level_locked()
            own = min(int(cleared) + 1, self.state["current_level"])
            levels = [
                {"level": int(k), "solved": bool(v.get("solved")), "locked": int(k) > own}
                for k, v in sorted(self.state["levels"].items(), key=lambda kv: int(kv[0]))
            ]
            return {
                "cleared": cleared,
                "own_level": own,
                "global_level": self.state["current_level"],
                "levels": levels,
                "total_guesses": self.state["total_guesses"],
                "mode": self.mode,
                "questions_count": self.bank.count(),
                "max_levels": self.max_levels,
            }

    def admin_questions(self) -> list[dict]:
        reveal = self.reveal_passwords
        return [
            {"index": i + 1, "prompt": q["prompt"], "hint": q.get("hint", ""),
             "answer": q["answer"] if reveal else None, "length": len(q["answer"])}
            for i, q in enumerate(self.bank.items)
        ]

    def admin_question_save(self, index: int, prompt: str, answer: str, hint: str = "") -> dict:
        item = self.bank.upsert(index, prompt, answer, hint)
        rebound = self._rebind_level(int(index), item)
        return {"index": int(index), **item, "rebound_level": rebound}

    def admin_question_delete(self, index: int) -> dict:
        index = int(index)
        removed = self.bank.delete(index)
        shifted = self.bank.get(index)
        if shifted is not None:
            self._rebind_level(index, shifted)  # 后面的题往前挪一位，关卡跟着换题面
        else:
            self._randomize_level(index)        # 题库变短了：这关改成随机密码，计数保留
        return {"index": index, "shifted": shifted is not None, **removed}

    def _randomize_level(self, level: int) -> None:
        with self.lock:
            rec = self.state["levels"].get(str(level))
            if rec is None:
                return
            length = int(rec.get("length") or length_for_level(self.cfg, level))
            self.state["levels"][str(level)] = self._record(
                make_password(self.cfg, length), None,
                base={**rec, "custom": False, "solved": False, "solved_at": None,
                      "solved_by": None, "password_set_at": now_iso()},
            )
            self._save(self.state)

    def admin_rebind_level(self, level: int) -> dict:
        item = self.bank.get(level)
        if not item:
            raise ValueError(f"题库里没有第 {level} 题")
        rebound = self._rebind_level(int(level), item)
        if rebound is None:
            raise KeyError(level)
        return {"level": int(level), "password": item["answer"] if self.reveal_passwords else None,
                "length": len(item["answer"]), "prompt": item["prompt"]}

    def _rebind_level(self, level: int, item: dict) -> int | None:
        """Point an existing level at a bank question so its answer matches the prompt."""
        with self.lock:
            if str(level) not in self.state["levels"]:
                return None
            self.state["levels"][str(level)] = self._record(
                item["answer"], None,
                base={**self.state["levels"][str(level)], "custom": False, "solved": False,
                      "solved_at": None, "solved_by": None, "password_set_at": now_iso()},
                prompt=item["prompt"], hint=item.get("hint") or None, source="question",
            )
            self._save(self.state)
            return level

    def reset(self) -> None:
        with self.lock:
            state = self._blank()
            self._create_level(state, 1)
            self._save(state)
            self.state = state


# -- per-player rate limiting (in memory, keyed by player id) --------------
class RateLimiter:
    def __init__(self, cfg: dict, limit: int | None = None, window: float | None = None,
                 min_interval_ms: int | None = None):
        sec = cfg["security"]
        self.window = float(window if window is not None else 60.0)
        self.limit = max(1, int(limit if limit is not None else sec.get("guess_rate_limit_per_minute", 30)))
        interval = min_interval_ms if min_interval_ms is not None else sec.get("guess_min_interval_ms", 0)
        self.min_interval = max(0.0, int(interval) / 1000.0)
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def remaining(self, player_id: str) -> int:
        now = time.monotonic()
        with self.lock:
            stamps = [t for t in self.hits.get(player_id, []) if now - t < self.window]
            return max(0, self.limit - len(stamps))

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
