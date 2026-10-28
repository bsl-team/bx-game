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


def make_password(cfg: dict, length: int) -> str:
    pc = cfg["password"]
    alphabet = ALPHABETS.get(pc.get("charset", "lower"), string.ascii_lowercase)
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


def hash_secret(secret: str, cfg: dict) -> dict:
    sec = cfg["security"]
    algo = str(sec.get("hash_algorithm", "pbkdf2_sha256")).lower()
    salt = secrets.token_hex(16)
    if algo.startswith("pbkdf2_"):
        digest = algo.split("_", 1)[1] or "sha256"
        iterations = max(1000, int(sec.get("hash_iterations", 120000)))
        digest_hex = hashlib.pbkdf2_hmac(
            digest, secret.encode(), bytes.fromhex(salt), iterations
        ).hex()
    elif algo in getattr(hashlib, "algorithms_available", set()):
        digest_hex = hashlib.new(algo, (salt + secret).encode()).hexdigest()
    else:
        raise ValueError(f"unsupported hash_algorithm: {algo}")
    return {"algorithm": algo, "salt": salt, "hash": digest_hex}


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

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.path = ROOT / str(cfg["rounds"].get("state_file", "data/state.json"))
        self.lock = threading.Lock()
        self.state = self._load()

    # -- persistence ------------------------------------------------------
    def _blank(self) -> dict:
        return {
            "version": 1,
            "current_level": 1,
            "total_guesses": 0,
            "levels": {},
            "solved_log": [],
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
    def _create_level(self, state: dict, level: int) -> str:
        length = length_for_level(self.cfg, level)
        password = make_password(self.cfg, length)
        rec = hash_secret(password, self.cfg)
        rec.update(
            {
                "length": length,
                "iterations": max(1000, int(self.cfg["security"].get("hash_iterations", 120000))),
                "guesses": 0,
                "solved": False,
                "solved_at": None,
                "solved_by": None,
                "created_at": now_iso(),
            }
        )
        state["levels"][str(level)] = rec
        return password

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

    def reset(self) -> None:
        with self.lock:
            state = self._blank()
            self._create_level(state, 1)
            self._save(state)
            self.state = state


# -- per-player rate limiting (in memory, keyed by player id) --------------
class RateLimiter:
    def __init__(self, cfg: dict):
        self.window = 60.0
        self.limit = max(1, int(cfg["security"].get("guess_rate_limit_per_minute", 30)))
        self.min_interval = max(0.0, int(cfg["security"].get("guess_min_interval_ms", 0)) / 1000.0)
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
