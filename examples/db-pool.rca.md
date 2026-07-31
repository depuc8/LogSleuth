# Incident analysis

## Summary

- **Input**: `samples/db-pool/syslog.log`, `samples/db-pool/app.log`
- **Window**: 2026-07-21 02:00:00 to 2026-07-21 02:44:58 (44m 58s), 2,537 records
- **Errors**: 948 across 6 signatures (201 further warnings)
- **Detection**: 30s buckets, spike = median + 3.5 sigma (min 5 events)
- **Incidents**: 1
  - [Incident 1](#incident-1): 02:18:30-02:26:30, 916 errors, likely slow-queries-locks (medium confidence)

## Incident 1

**2026-07-21 02:18:30 to 2026-07-21 02:26:30** (8m), 916 errors, peak 75 in one 30s bucket at 02:21:00.

### Likely cause

**Database**: Database queries are slow or blocked on locks, holding connections and backing work up behind them.

Confidence: **medium**.

Evidence:
- Leading signal `Slow query took <N>ms: SELECT * FROM order_items WHERE order_id = ?` from `app/c.a.orders.OrderRepository` went abnormal at 2026-07-21 02:16:13 and occurred 141 times in the incident.
- Example: `Slow query took 4881ms: SELECT * FROM order_items WHERE order_id = ?`
- Matches known cause pattern 'slow-queries-locks'.
- Among the first to go abnormal.
- First seen just before the incident.
- Spiked above its own baseline.
- A change was logged 2m 10s before onset at 2026-07-21 02:14:03: `Deploying release v2.14.0 to app01` (app01/deploy).
- Followed by: `HikariPool-<N> - Connection is not available, request timed…` (app/com.zaxxer.hikari.pool.HikariPool), `Order lookup failed for order <N> [java.sql.SQLTransientCon…` (app/c.a.orders.OrderService), `FATAL: remaining connection slots are reserved for non-repl…` (db01/postgres).

### Timeline

| Time | Type | Event |
|---|---|---|
| 02:14:03 | Change | `Deploying release v2.14.0 to app01` (app01/deploy) |
| 02:14:19 | Change | `Stopped orders-api.service - Orders API.` (app01/systemd) |
| 02:14:21 | Change | `Started orders-api.service - Orders API.` (app01/systemd) |
| 02:16:10 | Warning | `HikariPool-<N> - Pool stats (total=<N>, active=<N>, idle=<N>, waiting=<N>)` first abnormal (app/com.zaxxer.hikari.pool.HikariPool, 22x) |
| 02:16:13 | Warning | `Slow query took <N>ms: SELECT * FROM order_items WHERE order_id = ?` first abnormal (app/c.a.orders.OrderRepository, 141x) **<- likely root cause** |
| 02:18:19 | Error | `HikariPool-<N> - Connection is not available, request timed out after <N>ms.` first abnormal (app/com.zaxxer.hikari.pool.HikariPool, 339x) |
| 02:18:30 | Spike | Error spike begins (baseline 0 per 30s) |
| 02:18:43 | Error | `Order lookup failed for order <N> [java.sql.SQLTransientConnectionException: HikariPool-<N> - Connection is n…` first abnormal (app/c.a.orders.OrderService, 36x) |
| 02:18:48 | Error | `FATAL: remaining connection slots are reserved for non-replication superuser connections` first abnormal (db01/postgres, 185x) |
| 02:19:16 | Error | `upstream timed out (<N>: Connection timed out) while reading response header from upstream, client: <IP>, ser…` first abnormal (web01/nginx, 355x) |
| 02:21:00 | Peak | Busiest bucket: 75 events in 30s |
| 02:26:30 | Recovery | Spike ends; rate back near baseline |

### Recommended fix

1. Find the offending statements with the slow query log or pg_stat_statements and EXPLAIN them.
2. Look for a missing index, a lock held by a long transaction, or a migration running on a hot table.
3. Set statement and lock timeouts so one bad query can't stall the pool.

Later failures in the same incident have causes of their own; once the above is handled, check:

- **Database** (`db-connections-exhausted`): Look for connection leaks or long-held transactions in the code that changed just before onset; roll it back if a deploy lines up.

### Errors during the incident

| Count | Level | First | Signature | Source |
|---|---|---|---|---|
| 353 | ERROR | 02:19:16 | `upstream timed out (<N>: Connection timed out) while reading response header fr…` | web01/nginx |
| 337 | ERROR | 02:18:37 | `HikariPool-<N> - Connection is not available, request timed out after <N>ms.` | app/com.zaxxer.hikari.pool.Hi… |
| 185 | CRIT | 02:18:48 | `FATAL: remaining connection slots are reserved for non-replication superuser co…` | db01/postgres |
| 36 | ERROR | 02:18:43 | `Order lookup failed for order <N> [java.sql.SQLTransientConnectionException: Hi…` | app/c.a.orders.OrderService |
| 3 | ERROR | 02:25:12 | `Failed password for <USER> from <IP> port <N> ssh<N>` | web01/sshd |
| 2 | ERROR | 02:19:17 | `Failed to send receipt email to user <N>: SMTP <N> try again later` | app/c.a.orders.EmailJob |

### Ranked candidates

| # | Score | Onset | Signature | Source | Count | Rule |
|---|---|---|---|---|---|---|
| 1 | 9.2 | 02:16:13 | `Slow query took <N>ms: SELECT * FROM order_items WHERE order_id = ?` | app/c.a.orders.OrderRepository | 141 | slow-queries-locks |
| 2 | 8.8 | 02:18:48 | `FATAL: remaining connection slots are reserved for non-replication su…` | db01/postgres | 185 | db-connections-exhausted |
| 3 | 8.4 | 02:18:19 | `HikariPool-<N> - Connection is not available, request timed out after…` | app/com.zaxxer.hikari.pool.Hi… | 339 | db-connections-exhausted |
| 4 | 7.5 | 02:18:43 | `Order lookup failed for order <N> [java.sql.SQLTransientConnectionExc…` | app/c.a.orders.OrderService | 36 | db-connections-exhausted |
| 5 | 5.8 | 02:16:10 | `HikariPool-<N> - Pool stats (total=<N>, active=<N>, idle=<N>, waiting…` | app/com.zaxxer.hikari.pool.Hi… | 22 | - |
| 6 | 5.0 | 02:19:16 | `upstream timed out (<N>: Connection timed out) while reading response…` | web01/nginx | 355 | upstream-timeouts |

## Top error signatures

| Count | Level | First seen | Last seen | Signature | Busiest source |
|---|---|---|---|---|---|
| 355 | ERROR | 2026-07-21 02:19:16 | 2026-07-21 02:26:53 | `upstream timed out (<N>: Connection timed out) while reading response header fr…` | web01/nginx |
| 339 | ERROR | 2026-07-21 02:18:19 | 2026-07-21 02:26:25 | `HikariPool-<N> - Connection is not available, request timed out after <N>ms.` | app/com.zaxxer.hikari.pool.Hi… |
| 185 | CRIT | 2026-07-21 02:18:48 | 2026-07-21 02:26:17 | `FATAL: remaining connection slots are reserved for non-replication superuser co…` | db01/postgres |
| 36 | ERROR | 2026-07-21 02:18:43 | 2026-07-21 02:26:05 | `Order lookup failed for order <N> [java.sql.SQLTransientConnectionException: Hi…` | app/c.a.orders.OrderService |
| 18 | ERROR | 2026-07-21 02:01:19 | 2026-07-21 02:42:03 | `Failed password for <USER> from <IP> port <N> ssh<N>` | web01/sshd |
| 15 | ERROR | 2026-07-21 02:02:24 | 2026-07-21 02:42:12 | `Failed to send receipt email to user <N>: SMTP <N> try again later` | app/c.a.orders.EmailJob |

---
Generated by LogSleuth 0.1.0.
