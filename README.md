# LogSleuth

A Unix log analyzer and root-cause tool. Point it at syslog and application
logs and it will:

1. **parse** them with `awk` into one normalized record stream,
2. **collapse** messages into signatures with `sed`, so 5,000 timeouts count as one problem,
3. **group** errors by signature and flag **spikes** in the error rate and in each signature, and
4. write an **RCA summary** in Markdown: timeline, likely cause and recommended fix.

It is plain Bash, awk, sed and Python 3 (standard library only). No install step, no dependencies.

```console
$ bin/logsleuth analyze --year 2026 samples/db-pool/syslog.log samples/db-pool/app.log
```

The report for that run is in [`examples/db-pool.rca.md`](examples/db-pool.rca.md). The interesting part:

```text
Incident 1: 02:18:30-02:26:30, 916 errors, likely slow-queries-locks (medium confidence)

Timeline
02:14:03  Change   Deploying release v2.14.0 to app01
02:16:13  Warning  Slow query took <N>ms: SELECT * FROM order_items ... <- likely root cause
02:18:19  Error    HikariPool-<N> - Connection is not available, request timed out ...
02:18:43  Error    Order lookup failed for order <N> [java.sql.SQLTransientConnectionException: ...]
02:18:48  Error    FATAL: remaining connection slots are reserved ...   (db01/postgres)
02:19:16  Error    upstream timed out (<N>: Connection timed out) ...   (web01/nginx)
02:26:30  Recovery Spike ends; rate back near baseline
```

A release went out, queries got slow, the pool filled, Postgres ran out of
connection slots, and nginx started returning 504s. The 504s are the loudest
signal (they are the most frequent error) but they are the *last* link, and
LogSleuth ranks the first one to go abnormal.

## Requirements

- Bash, a POSIX `awk`, `sed` with `-E`, `paste` and `cut` (developed against Bash 5, gawk and GNU sed; the awk and sed code sticks to POSIX features)
- Python 3.8+ (developed on 3.14)

## Usage

```text
logsleuth analyze [options] FILE...      full pipeline, Markdown report
logsleuth parse [--year YYYY] [--host NAME] FILE...
logsleuth normalize [FILE]
```

`FILE` may be `-` for stdin, and `*.gz` files are decompressed on the fly. Give
several files (syslog plus an app log, or several hosts) and they are merged by time.

| Option | Default | Meaning |
|---|---|---|
| `-o, --out FILE` | stdout | write the report to a file |
| `--year YYYY` | current year | syslog timestamps carry no year |
| `--host NAME` | file name | host label for logs that don't name one |
| `--width SECONDS` | auto | bucket width for counting events |
| `-z SIGMAS` | `3.5` | how far above the median a bucket must be to spike |
| `--min-count N` | `5` | ignore buckets with fewer events than this |
| `--lead SECONDS` | `600` | run-up before a spike to look for precursors |
| `--tail SECONDS` | `300` | time after a spike to include |
| `--lookback SECONDS` | `1800` | how far back to look for deploys and restarts |
| `--top N` | `10` | rows per table |
| `--rules FILE` | | extra rules JSON (repeatable) |
| `-q, --quiet` | | no progress output on stderr |

The stages are ordinary filters, so you can stop at any of them:

```console
$ logsleuth parse --year 2026 /var/log/messages | head      # normalized records
$ logsleuth parse app.log | logsleuth normalize | cut -f6 | sort | uniq -c | sort -rn | head
$ zcat /var/log/syslog.*.gz | logsleuth analyze --year 2026 -
```

### Recognised log formats

| Format | Example |
|---|---|
| syslog (RFC 3164) | `Jul 12 03:14:15 web01 nginx[812]: upstream timed out` |
| ISO-8601 syslog | `2026-07-12T03:14:15.123+00:00 db01 postgres[77]: FATAL: ...` |
| application log | `2026-07-12 03:14:15,123 ERROR [pool-3] com.acme.Db - message` |
| bracketed level | `2026-07-12 03:14:15 [WARN] message` |
| logfmt | `time=2026-07-12T03:14:15Z level=error msg="message" service=api` |

Syslog has no severity, so it is inferred from the text (`out of memory` is
critical, `timed out` / `failed` / `denied` are errors, `retry` / `slow` are
warnings). Java and Python stack traces are folded into the record above them:
the frames are dropped and the root-cause exception line is appended to the message.
Timestamps are taken as written and never converted between time zones, so
feed it logs from one zone (or already in UTC) when merging hosts.

## How it works

```
 raw logs ──► lib/parse.awk ──► lib/normalize.sed ──► logsleuth/ (Python) ──► report.md
              ts host proc      append a signature      spikes, context,
              level message     column                  ranking, Markdown
```

**Signatures.** `lib/normalize.sed` replaces UUIDs, IPs, hex, timestamps and
numbers with placeholders, so `timed out after 30000ms` and `timed out after
28114ms` become the same `timed out after <N>ms`. Sign-in messages also have
user names masked.

**Spikes.** Event counts are bucketed over time (the width is chosen from the
log's span, or set with `--width`). A bucket spikes when it exceeds the
*median* plus `z` times a MAD-based spread, with a floor at the Poisson spread
so sparse logs don't flag every lone error. The median/MAD baseline matters:
a mean and standard deviation get dragged up by the very incident you're
looking for. The overall error rate and every warning-or-worse signature are
checked, and overlapping spikes become one incident.

**Events around the spike.** For each incident LogSleuth collects warning-level
precursors from the run-up, signatures seen for the first time, and changes
(deploy, restart, reload, rollback, migration) from a longer lookback.

**Likely cause.** Candidates are signatures that spiked or were new, so steady
background noise is never a suspect. They are scored on:

- a match against a known failure pattern (`logsleuth/rules.json`), where *cause* patterns count for more than *symptom* patterns such as gateway timeouts,
- how early the signature went abnormal,
- whether it appeared for the first time just before the incident,
- how hard it spiked, how many events it produced, and its severity.

Confidence is *high* when a known cause pattern leads clearly, *low* when
nothing matched and the lead is thin. Later known causes in the same chain
are listed with their own fixes.

## Adding your own rules

Rules are JSON, merged over the bundled set (a rule with the same `id`
replaces the bundled one):

```json
[
  {
    "id": "payments-gateway-down",
    "category": "Payments",
    "role": "cause",
    "weight": 3.0,
    "pattern": "(stripe|payments-gateway).*(503|unavailable)",
    "cause": "The payment provider is returning errors.",
    "fix": ["Check the provider's status page.", "Enable the fallback processor."]
  }
]
```

```console
$ logsleuth analyze --rules my-rules.json app.log
```

`pattern` is a case-insensitive regular expression tried against the raw
example message and the signature. Use `"role": "symptom"` for patterns that
describe an effect rather than a cause.

## Development

```console
$ make test       # unit tests (python unittest) and shell tests
$ make demo       # analyze the bundled db-pool incident
$ make samples    # regenerate samples/ (deterministic)
```

`samples/` holds two generated incidents: `db-pool` (a release exhausts the
database connection pool) and `oom` (a leak ends in the OOM killer). They are
produced by `tools/gen_samples.py` from fixed seeds. The 2,500-line `db-pool`
incident analyzes in well under a second; a 216,000-line file takes about five
seconds on a laptop.

```
bin/logsleuth        driver: parse | normalize | analyze
lib/parse.awk        log lines -> normalized records
lib/normalize.sed    messages -> signatures
logsleuth/           records, signatures, spikes, context, rules, rca, report
tests/               unit tests and shell tests
tools/gen_samples.py sample incident generator
```

## Limitations

- Anomaly detection is statistical and the ranking is heuristic. The report
  is a strong lead to check against the raw logs, not a verdict. It always
  shows the ranked candidates and timeline so you can see why it concluded what it did.
- Cause naming only knows the patterns in the rules file; anything else is
  reported as "no known pattern" with the first signature to go abnormal.
- Syslog severity is guessed from keywords and will sometimes be wrong.

## License

MIT
