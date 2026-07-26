#!/usr/bin/env python3
"""Generate the sample incidents in samples/.

The output is deterministic (fixed seeds), so regenerating produces
byte-identical files and the tests can assert on them.

    python3 tools/gen_samples.py [output_dir]

Two incidents are written, each as a syslog file plus an application log:

  db-pool/  a release exhausts the database connection pool
  oom/      a memory leak ends with the kernel OOM killer
"""

import os
import random
import sys
from calendar import timegm
from datetime import datetime, timezone

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
CLIENTS = ["203.0.113.45", "198.51.100.23", "192.0.2.77", "203.0.113.9",
           "198.51.100.140", "192.0.2.201"]


def at(day, hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return timegm((2026, 7, day, h, m, s))


class Log:
    """Collects events, then writes them out time-ordered."""

    def __init__(self):
        self.syslog = []   # (ts, order, line)
        self.app = []
        self._n = 0

    def _order(self):
        self._n += 1
        return self._n

    def sys(self, ts, host, proc, pid, msg):
        t = datetime.fromtimestamp(ts, timezone.utc)
        line = (f"{MONTHS[t.month - 1]} {t.day:2d} {t:%H:%M:%S} {host} "
                f"{proc}[{pid}]: {msg}")
        self.syslog.append((ts, self._order(), line))

    def app_line(self, ts, level, thread, logger, msg, ms=None):
        t = datetime.fromtimestamp(ts, timezone.utc)
        ms = random.randint(0, 999) if ms is None else ms
        line = (f"{t:%Y-%m-%d %H:%M:%S},{ms:03d} {level:<5} [{thread}] "
                f"{logger} - {msg}")
        self.app.append((ts, self._order(), line))

    def write(self, directory):
        os.makedirs(directory, exist_ok=True)
        for name, rows in (("syslog.log", self.syslog), ("app.log", self.app)):
            with open(os.path.join(directory, name), "w") as fh:
                for _, _, line in sorted(rows):
                    fh.write(line + "\n")


def events(rng, start, end, per_minute_at):
    """Yield event timestamps, with a rate (per minute) that varies by time."""
    for t in range(start, end):
        lam = per_minute_at(t) / 60.0
        n = int(lam) + (1 if rng.random() < lam - int(lam) else 0)
        for _ in range(n):
            yield t


def ramp(t, t0, t1, lo, hi):
    """Linear rate from lo at t0 to hi at t1."""
    if t <= t0:
        return lo
    if t >= t1:
        return hi
    return lo + (hi - lo) * (t - t0) / (t1 - t0)


def background(log, rng, start, end):
    """Everyday traffic and noise common to both incidents."""
    for t in range(start, end, 300):
        log.sys(t, "app01", "CRON", rng.randint(2000, 9000),
                "(root) CMD (/usr/lib64/sa/sa1 1 1)")
    for t in events(rng, start, end, lambda t: 0.4):
        log.sys(t, "web01", "sshd", rng.randint(1000, 30000),
                f"Failed password for invalid user {rng.choice(['admin', 'test', 'oracle', 'ubuntu'])}"
                f" from {rng.choice(CLIENTS)} port {rng.randint(30000, 60000)} ssh2")
    for t in events(rng, start, end, lambda t: 0.15):
        log.sys(t, "web01", "sshd", rng.randint(1000, 30000),
                f"Accepted publickey for deploy from 10.0.0.{rng.randint(2, 30)} "
                f"port {rng.randint(30000, 60000)} ssh2")
    for t in events(rng, start, end, lambda t: 0.3):
        log.sys(t, "db01", "postgres", rng.randint(500, 900),
                f"checkpoint complete: wrote {rng.randint(100, 900)} buffers")
    for t in events(rng, start, end, lambda t: 30):
        log.app_line(t, "INFO", f"http-nio-8080-exec-{rng.randint(1, 20)}",
                     "c.a.orders.OrderController",
                     f"GET /api/v1/orders/{rng.randint(1000, 99999)} -> 200 "
                     f"in {rng.randint(8, 90)}ms")
    for t in events(rng, start, end, lambda t: 0.8):
        log.app_line(t, "WARN", f"http-nio-8080-exec-{rng.randint(1, 20)}",
                     "c.a.orders.CacheService",
                     f"Cache miss for key orders:{rng.randint(1000, 99999)}")
    # a rare, steady error that is always there and is not the incident
    for t in events(rng, start, end, lambda t: 0.25):
        log.app_line(t, "ERROR", "scheduler-1", "c.a.orders.EmailJob",
                     f"Failed to send receipt email to user {rng.randint(100, 9999)}: "
                     "SMTP 421 try again later")


def db_pool(out):
    rng = random.Random(2107)
    random.seed(2107)
    log = Log()
    day = 21
    start, end = at(day, "02:00:00"), at(day, "02:45:00")
    background(log, rng, start, end)

    # 02:14 deploy of v2.14.0
    d = at(day, "02:14:03")
    log.sys(d, "app01", "deploy", 3412, "Deploying release v2.14.0 to app01")
    log.sys(d + 4, "app01", "systemd", 1, "Stopping orders-api.service - Orders API...")
    log.sys(d + 16, "app01", "systemd", 1, "Stopped orders-api.service - Orders API.")
    log.sys(d + 18, "app01", "systemd", 1, "Started orders-api.service - Orders API.")
    log.app_line(d + 22, "INFO", "main", "c.a.orders.OrdersApplication",
                 "Starting OrdersApplication v2.14.0 with 20 pool connections")

    # slow queries start ~02:16 and the pool saturates
    s0 = at(day, "02:16:10")
    for t in events(rng, s0, at(day, "02:26:30"),
                    lambda t: ramp(t, s0, at(day, "02:19:00"), 1, 14)):
        log.app_line(t, "WARN", f"http-nio-8080-exec-{rng.randint(1, 20)}",
                     "c.a.orders.OrderRepository",
                     f"Slow query took {rng.randint(2200, 9800)}ms: "
                     "SELECT * FROM order_items WHERE order_id = ?")
    for t in range(s0, at(day, "02:27:00"), 30):
        active = min(20, 8 + (t - s0) // 12)
        log.app_line(t, "WARN", "HikariPool-1 housekeeper", "com.zaxxer.hikari.pool.HikariPool",
                     f"HikariPool-1 - Pool stats (total=20, active={active}, "
                     f"idle={20 - active}, waiting={max(0, (t - s0) // 15 - 6)})")

    # errors: pool timeouts, postgres refusing connections, nginx 504s
    e0, e1 = at(day, "02:18:10"), at(day, "02:26:40")
    for t in events(rng, e0, e1, lambda t: ramp(t, e0, at(day, "02:20:00"), 4, 52)
                    if t < at(day, "02:25:00") else ramp(t, at(day, "02:25:00"), e1, 52, 3)):
        log.app_line(t, "ERROR", f"http-nio-8080-exec-{rng.randint(1, 20)}",
                     "com.zaxxer.hikari.pool.HikariPool",
                     "HikariPool-1 - Connection is not available, request timed out "
                     "after 30000ms.")
    for t in events(rng, e0 + 30, e1, lambda t: ramp(t, e0, at(day, "02:20:30"), 3, 30)
                    if t < at(day, "02:25:00") else ramp(t, at(day, "02:25:00"), e1, 30, 2)):
        log.sys(t, "db01", "postgres", rng.randint(900, 4000),
                "FATAL:  remaining connection slots are reserved for "
                "non-replication superuser connections")
    for t in events(rng, e0 + 60, e1 + 20, lambda t: ramp(t, e0, at(day, "02:21:00"), 3, 60)
                    if t < at(day, "02:25:00") else ramp(t, at(day, "02:25:00"), e1, 60, 3)):
        oid = rng.randint(1000, 99999)
        log.sys(t, "web01", "nginx", 812,
                "upstream timed out (110: Connection timed out) while reading "
                "response header from upstream, client: "
                f"{rng.choice(CLIENTS)}, server: shop.example.com, request: "
                f"\"GET /api/v1/orders/{oid} HTTP/1.1\", upstream: "
                f"\"http://10.0.1.12:8080/api/v1/orders/{oid}\", host: \"shop.example.com\"")

    # rollback
    r = at(day, "02:26:41")
    log.sys(r, "app01", "deploy", 3590, "Rolling back to release v2.13.4")
    log.sys(r + 3, "app01", "systemd", 1, "Stopping orders-api.service - Orders API...")
    log.sys(r + 14, "app01", "systemd", 1, "Started orders-api.service - Orders API.")
    log.app_line(r + 19, "INFO", "main", "c.a.orders.OrdersApplication",
                 "Starting OrdersApplication v2.13.4 with 20 pool connections")
    log.write(out)


def oom(out):
    rng = random.Random(1409)
    random.seed(1409)
    log = Log()
    day = 14
    start, end = at(day, "13:00:00"), at(day, "13:50:00")
    background(log, rng, start, end)
    # background() also logs an api that is healthy; the leak shows as
    # climbing memory warnings from 13:21 and a kill at 13:31:42
    m0, kill = at(day, "13:21:00"), at(day, "13:31:42")
    for t in events(rng, m0, kill, lambda t: ramp(t, m0, kill, 1, 18)):
        log.app_line(t, "WARN", "gc-monitor", "c.a.orders.MemoryWatch",
                     f"Heap usage {rng.randint(88, 99)}% after full GC, "
                     f"{rng.randint(1, 9)} GC pauses over {rng.randint(900, 4200)}ms in the last minute")
    log.sys(kill, "app01", "kernel", 0,
            "Out of memory: Killed process 4411 (java) total-vm:9124412kB, "
            "anon-rss:7340212kB, file-rss:0kB, shmem-rss:0kB")
    log.sys(kill + 1, "app01", "systemd", 1,
            "orders-api.service: Main process exited, code=killed, status=9/KILL")
    log.sys(kill + 1, "app01", "systemd", 1,
            "orders-api.service: Failed with result 'oom-kill'.")
    # restart, then it leaks again and is killed a second time
    log.sys(kill + 12, "app01", "systemd", 1, "Started orders-api.service - Orders API.")
    k2 = at(day, "13:41:20")
    log.sys(k2, "app01", "kernel", 0,
            "Out of memory: Killed process 5180 (java) total-vm:9201124kB, "
            "anon-rss:7422920kB, file-rss:0kB, shmem-rss:0kB")
    log.sys(k2 + 1, "app01", "systemd", 1,
            "orders-api.service: Main process exited, code=killed, status=9/KILL")
    log.sys(k2 + 12, "app01", "systemd", 1, "Started orders-api.service - Orders API.")

    # while the service is down, nginx can't reach it
    for lo, hi in ((kill, kill + 38), (k2, k2 + 34)):
        for t in events(rng, lo + 1, hi, lambda t: 55):
            oid = rng.randint(1000, 99999)
            log.sys(t, "web01", "nginx", 812,
                    "connect() failed (111: Connection refused) while connecting to "
                    f"upstream, client: {rng.choice(CLIENTS)}, server: shop.example.com, "
                    f"request: \"GET /api/v1/orders/{oid} HTTP/1.1\", "
                    f"upstream: \"http://10.0.1.12:8080/api/v1/orders/{oid}\"")
    log.write(out)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "samples")
    db_pool(os.path.join(out, "db-pool"))
    oom(os.path.join(out, "oom"))


if __name__ == "__main__":
    main()
