"""按 IP 的 24 小时请求统计（SQLite 持久化）与优先级准入队列。

统计每 IP 每个整点小时的请求数，内存里保留最近 24 个窗口，
后台线程每两分钟写一次 data/ipstats.db；优先级队列按 24 小时请求总数
排序（请求少的 IP 先放行），避免少数 IP 把服务堵死。
"""
from __future__ import annotations

import atexit
import heapq
import ipaddress
import sqlite3
import threading
import time
from pathlib import Path


def is_loopback(ip: str | None) -> bool:
    try:
        return ipaddress.ip_address(str(ip or "")).is_loopback
    except ValueError:
        return False


class IPStats:
    HOUR = 3600
    WINDOWS = 24

    def __init__(self, path: Path, max_ips: int = 20_000, flush_interval: float = 120.0):
        self.path = path
        self.max_ips = max(100, int(max_ips))
        self.flush_interval = max(5.0, float(flush_interval))
        self.lock = threading.RLock()
        self.counts: dict[tuple[str, int], int] = {}
        self.known: set[str] = set()
        self.loop_hours: dict[int, int] = {}
        self.loop_admin_hours: dict[int, int] = {}
        self.last_loopback = 0.0
        self.last_external = time.time()
        self._stop = threading.Event()
        self._conn: sqlite3.Connection | None = None

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS ip_hourly ("
                "ip TEXT NOT NULL, hour INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (ip, hour))"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ip_hourly_hour ON ip_hourly(hour)")
            self._load_locked()
        except sqlite3.Error:
            self._conn = None

        self._thread = threading.Thread(target=self._loop, name="bx-ipstats", daemon=True)
        self._thread.start()
        atexit.register(self.close)

    # -- 持久化 -----------------------------------------------------------
    def _load_locked(self) -> None:
        if self._conn is None:
            return
        oldest = int(time.time() // self.HOUR) - self.WINDOWS + 1
        try:
            rows = self._conn.execute(
                "SELECT ip, hour, hits FROM ip_hourly WHERE hour >= ?", (oldest,)
            ).fetchall()
        except sqlite3.Error:
            return
        for ip, hour, hits in rows:
            if not ip:
                continue
            self.counts[(str(ip), int(hour))] = int(hits)
            self.known.add(str(ip))

    def _prune_locked(self) -> None:
        oldest = int(time.time() // self.HOUR) - self.WINDOWS + 1
        old_keys = [key for key in self.counts if key[1] < oldest]
        for key in old_keys:
            self.counts.pop(key, None)
        if old_keys:
            self.known = {ip for ip, _ in self.counts}
        loop_oldest = int(time.time() // self.HOUR) - 5
        for table in (self.loop_hours, self.loop_admin_hours):
            for hour in [h for h in table if h < loop_oldest]:
                table.pop(hour, None)

    def _flush_snapshot_locked(self) -> tuple[int, list[tuple[str, int, int]]]:
        self._prune_locked()
        oldest = int(time.time() // self.HOUR) - self.WINDOWS + 1
        rows = [(ip, hour, hits) for (ip, hour), hits in self.counts.items() if hour >= oldest]
        return oldest, rows

    def _write_rows(self, oldest: int, rows: list[tuple[str, int, int]]) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM ip_hourly WHERE hour < ?", (oldest,))
            if rows:
                self._conn.executemany(
                    "INSERT INTO ip_hourly (ip, hour, hits) VALUES (?, ?, ?) "
                    "ON CONFLICT(ip, hour) DO UPDATE SET hits = excluded.hits",
                    rows,
                )
            self._conn.commit()
        except sqlite3.Error:
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass

    def _loop(self) -> None:
        while not self._stop.wait(self.flush_interval):
            with self.lock:
                oldest, rows = self._flush_snapshot_locked()
            self._write_rows(oldest, rows)

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)
        with self.lock:
            oldest, rows = self._flush_snapshot_locked()
        self._write_rows(oldest, rows)
        with self.lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    # -- 记录 / 查询 ------------------------------------------------------
    def record(self, ip: str, loopback: bool = False, admin: bool = False) -> None:
        ip = str(ip or "unknown")[:45]
        hour = int(time.time() // self.HOUR)
        now = time.time()
        with self.lock:
            if ip in self.known or len(self.known) < self.max_ips:
                self.known.add(ip)
                key = (ip, hour)
                self.counts[key] = self.counts.get(key, 0) + 1
            if loopback:
                self.loop_hours[hour] = self.loop_hours.get(hour, 0) + 1
                if admin:
                    self.loop_admin_hours[hour] = self.loop_admin_hours.get(hour, 0) + 1
                self.last_loopback = now
            else:
                self.last_external = now

    def priority(self, ip: str) -> int:
        """24 小时窗口内的请求总数，越小越优先。"""
        ip = str(ip or "unknown")[:45]
        hour = int(time.time() // self.HOUR)
        with self.lock:
            return sum(self.counts.get((ip, h), 0) for h in range(hour - self.WINDOWS + 1, hour + 1))

    def loopback_summary(self) -> dict:
        hour = int(time.time() // self.HOUR)
        with self.lock:
            loop_6h = sum(self.loop_hours.get(h, 0) for h in range(hour - 5, hour + 1))
            loop_admin = sum(self.loop_admin_hours.get(h, 0) for h in range(hour - 5, hour + 1))
            last_external = self.last_external
            last_loopback = self.last_loopback
        now = time.time()
        return {
            "loop_6h": loop_6h,
            "loop_admin_6h": loop_admin,
            "last_loopback": last_loopback,
            "last_external_age": now - last_external,
            "all_loopback_6h": now - last_external >= 6 * self.HOUR and loop_6h > 0,
        }

    def summary(self, top: int = 8) -> dict:
        hour = int(time.time() // self.HOUR)
        with self.lock:
            hours = list(range(hour - self.WINDOWS + 1, hour + 1))
            totals: dict[str, int] = {}
            hour_total = 0
            for (ip, h), hits in self.counts.items():
                if h in hours:
                    totals[ip] = totals.get(ip, 0) + hits
                    if h == hour:
                        hour_total += hits
            ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top]
        return {
            "requests_this_hour": hour_total,
            "tracked_ips": len(totals),
            "top_ips": ranked,
            "windows": self.WINDOWS,
            "db": str(self.path),
        }


class PriorityGate:
    """并发准入闸门：放行顺序按优先级（请求少的 IP 在前），限制排队人数。"""

    def __init__(self, max_inflight: int = 48, max_waiters: int = 4096):
        self.cond = threading.Condition()
        self.max_inflight = max(1, int(max_inflight))
        self.max_waiters = max(1, int(max_waiters))
        self.inflight = 0
        self.waiters: list[list] = []
        self.seq = 0

    def configure(self, max_inflight: int | None = None, max_waiters: int | None = None) -> None:
        with self.cond:
            if max_inflight is not None:
                self.max_inflight = max(1, int(max_inflight))
            if max_waiters is not None:
                self.max_waiters = max(1, int(max_waiters))

    def acquire(self, priority: int, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.01, float(timeout))
        with self.cond:
            if self.inflight < self.max_inflight and not self.waiters:
                self.inflight += 1
                return True
            if len(self.waiters) >= self.max_waiters:
                return False
            self.seq += 1
            ticket = [int(priority), self.seq, threading.Event(), False]
            heapq.heappush(self.waiters, ticket)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if ticket[2].wait(remaining):
                return True
        with self.cond:
            if ticket[2].is_set():
                return True
            ticket[3] = True
            return False

    def release(self) -> None:
        with self.cond:
            if self.inflight > 0:
                self.inflight -= 1
            while self.waiters:
                priority, seq, event, cancelled = heapq.heappop(self.waiters)
                if cancelled:
                    continue
                self.inflight += 1
                event.set()
                return

    def snapshot(self) -> dict:
        with self.cond:
            return {
                "inflight": self.inflight,
                "waiting": len(self.waiters),
                "max_inflight": self.max_inflight,
                "max_waiters": self.max_waiters,
            }
