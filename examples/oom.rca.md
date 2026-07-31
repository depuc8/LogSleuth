# Incident analysis

## Summary

- **Input**: `samples/oom/syslog.log`, `samples/oom/app.log`
- **Window**: 2026-07-14 13:00:00 to 2026-07-14 13:49:58 (49m 58s), 1,737 records
- **Errors**: 107 across 5 signatures (132 further warnings)
- **Detection**: 30s buckets, spike = median + 3.5 sigma (min 5 events)
- **Incidents**: 2
  - [Incident 1](#incident-1): 13:26:00-13:32:30, 42 errors, likely memory-pressure (high confidence)
  - [Incident 2](#incident-2): 13:41:00-13:42:00, 32 errors, likely out-of-memory (high confidence)

## Incident 1

**2026-07-14 13:26:00 to 2026-07-14 13:32:30** (6m 30s), 42 errors, peak 20 in one 30s bucket at 13:31:30.

### Likely cause

**Memory**: Memory is nearly exhausted and garbage collection or swapping is struggling to keep up. This usually precedes an out-of-memory kill.

Confidence: **high**.

Evidence:
- Leading signal `Heap usage <N>% after full GC, <N> GC pauses over <N>ms in the last minute` from `app/c.a.orders.MemoryWatch` went abnormal at 2026-07-14 13:21:18 and occurred 95 times in the incident.
- Example: `Heap usage 89% after full GC, 9 GC pauses over 3770ms in the last minute`
- Matches known cause pattern 'memory-pressure'.
- Among the first to go abnormal.
- First seen just before the incident.
- Spiked above its own baseline.
- Followed by: `Out of memory: Killed process <N> (java) total-vm:<N>kB, an…` (app01/kernel), `orders-api.service: Failed with result 'oom-kill'.` (app01/systemd), `connect() failed (<N>: Connection refused) while connecting…` (web01/nginx).

### Timeline

| Time | Type | Event |
|---|---|---|
| 13:21:18 | Warning | `Heap usage <N>% after full GC, <N> GC pauses over <N>ms in the last minute` first abnormal (app/c.a.orders.MemoryWatch, 95x) **<- likely root cause** |
| 13:26:00 | Spike | Error spike begins (baseline 0 per 30s) |
| 13:31:30 | Peak | Busiest bucket: 20 events in 30s |
| 13:31:42 | Error | `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>kB, file-rss:<N>kB, shmem-rss:<N>kB` first abnormal (app01/kernel, 1x) |
| 13:31:43 | Error | `orders-api.service: Failed with result 'oom-kill'.` first abnormal (app01/systemd, 1x) |
| 13:31:43 | Error | `connect() failed (<N>: Connection refused) while connecting to upstream, client: <IP>, server: shop.example.c…` first abnormal (web01/nginx, 35x) |
| 13:31:54 | Change | `Started orders-api.service - Orders API.` (app01/systemd) |
| 13:32:30 | Recovery | Spike ends; rate back near baseline |

### Recommended fix

1. Compare heap/RSS growth against the last deploy; a steady climb that never plateaus is a leak.
2. Take a heap dump (jcmd <pid> GC.heap_dump) or profile allocations while usage is high.
3. Cap caches and queues, and size the memory limit with headroom; alert on sustained high heap after GC.

Later failures in the same incident have causes of their own; once the above is handled, check:

- **Memory** (`out-of-memory`): Identify the victim and its peak RSS in the kernel log (dmesg | grep -i 'killed process').
- **Network** (`connection-refused`): Check that the target service is running and listening (systemctl status, ss -ltnp).

### Errors during the incident

| Count | Level | First | Signature | Source |
|---|---|---|---|---|
| 35 | ERROR | 13:31:43 | `connect() failed (<N>: Connection refused) while connecting to upstream, client…` | web01/nginx |
| 4 | ERROR | 13:26:29 | `Failed password for <USER> from <IP> port <N> ssh<N>` | web01/sshd |
| 1 | ERROR | 13:27:31 | `Failed to send receipt email to user <N>: SMTP <N> try again later` | app/c.a.orders.EmailJob |
| 1 | CRIT | 13:31:42 | `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>kB, file-r…` | app01/kernel |
| 1 | CRIT | 13:31:43 | `orders-api.service: Failed with result 'oom-kill'.` | app01/systemd |

### Ranked candidates

| # | Score | Onset | Signature | Source | Count | Rule |
|---|---|---|---|---|---|---|
| 1 | 9.5 | 13:21:18 | `Heap usage <N>% after full GC, <N> GC pauses over <N>ms in the last m…` | app/c.a.orders.MemoryWatch | 95 | memory-pressure |
| 2 | 6.2 | 13:31:42 | `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>…` | app01/kernel | 1 | out-of-memory |
| 3 | 6.2 | 13:31:43 | `orders-api.service: Failed with result 'oom-kill'.` | app01/systemd | 1 | out-of-memory |
| 4 | 6.2 | 13:31:43 | `connect() failed (<N>: Connection refused) while connecting to upstre…` | web01/nginx | 35 | connection-refused |

## Incident 2

**2026-07-14 13:41:00 to 2026-07-14 13:42:00** (1m), 32 errors, peak 22 in one 30s bucket at 13:41:30.

### Likely cause

**Memory**: A process ran out of memory and was killed (or failed to allocate), which restarts or drops whatever it was serving.

Confidence: **high**.

Evidence:
- Leading signal `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>kB, file-rss:<N>kB, shmem-rss:<N>kB` from `app01/kernel` went abnormal at 2026-07-14 13:31:42 and occurred 2 times in the incident.
- Example: `Out of memory: Killed process 4411 (java) total-vm:9124412kB, anon-rss:7340212kB, file-rss:0kB, shmem-rss:0kB`
- Matches known cause pattern 'out-of-memory'.
- Among the first to go abnormal.
- First seen just before the incident.
- Logged at critical severity.
- A change was logged just before onset at 2026-07-14 13:31:54: `Started orders-api.service - Orders API.` (app01/systemd).
- Corroborated by `orders-api.service: Failed with result 'oom-kill'.` (app01/systemd, 1x).
- Followed by: `connect() failed (<N>: Connection refused) while connecting…` (web01/nginx).

### Timeline

| Time | Type | Event |
|---|---|---|
| 13:31:42 | Error | `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>kB, file-rss:<N>kB, shmem-rss:<N>kB` first abnormal (app01/kernel, 2x) **<- likely root cause** |
| 13:31:43 | Error | `connect() failed (<N>: Connection refused) while connecting to upstream, client: <IP>, server: shop.example.c…` first abnormal (web01/nginx, 65x) |
| 13:31:43 | Error | `orders-api.service: Failed with result 'oom-kill'.` first abnormal (app01/systemd, 1x) |
| 13:31:54 | Change | `Started orders-api.service - Orders API.` (app01/systemd) |
| 13:41:00 | Spike | Error spike begins (baseline 0 per 30s) |
| 13:41:30 | Peak | Busiest bucket: 22 events in 30s |
| 13:42:00 | Recovery | Spike ends; rate back near baseline |

### Recommended fix

1. Identify the victim and its peak RSS in the kernel log (dmesg | grep -i 'killed process').
2. Check for a leak or an unbounded cache/queue introduced recently; compare memory graphs against the last deploy.
3. Right-size the memory limit, add swap or headroom as a stopgap, and alert on memory pressure before the OOM killer acts.

Later failures in the same incident have causes of their own; once the above is handled, check:

- **Network** (`connection-refused`): Check that the target service is running and listening (systemctl status, ss -ltnp).

### Errors during the incident

| Count | Level | First | Signature | Source |
|---|---|---|---|---|
| 30 | ERROR | 13:41:21 | `connect() failed (<N>: Connection refused) while connecting to upstream, client…` | web01/nginx |
| 1 | CRIT | 13:41:20 | `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>kB, file-r…` | app01/kernel |
| 1 | ERROR | 13:41:35 | `Failed password for <USER> from <IP> port <N> ssh<N>` | web01/sshd |

### Ranked candidates

| # | Score | Onset | Signature | Source | Count | Rule |
|---|---|---|---|---|---|---|
| 1 | 9.4 | 13:31:42 | `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>…` | app01/kernel | 2 | out-of-memory |
| 2 | 6.5 | 13:31:43 | `connect() failed (<N>: Connection refused) while connecting to upstre…` | web01/nginx | 65 | connection-refused |
| 3 | 6.2 | 13:31:43 | `orders-api.service: Failed with result 'oom-kill'.` | app01/systemd | 1 | out-of-memory |

## Top error signatures

| Count | Level | First seen | Last seen | Signature | Busiest source |
|---|---|---|---|---|---|
| 65 | ERROR | 2026-07-14 13:31:43 | 2026-07-14 13:41:53 | `connect() failed (<N>: Connection refused) while connecting to upstream, client…` | web01/nginx |
| 24 | ERROR | 2026-07-14 13:03:57 | 2026-07-14 13:49:26 | `Failed password for <USER> from <IP> port <N> ssh<N>` | web01/sshd |
| 15 | ERROR | 2026-07-14 13:00:25 | 2026-07-14 13:47:31 | `Failed to send receipt email to user <N>: SMTP <N> try again later` | app/c.a.orders.EmailJob |
| 2 | CRIT | 2026-07-14 13:31:42 | 2026-07-14 13:41:20 | `Out of memory: Killed process <N> (java) total-vm:<N>kB, anon-rss:<N>kB, file-r…` | app01/kernel |
| 1 | CRIT | 2026-07-14 13:31:43 | 2026-07-14 13:31:43 | `orders-api.service: Failed with result 'oom-kill'.` | app01/systemd |

---
Generated by LogSleuth 0.1.0.
