#!/usr/bin/env bash
# Tests for the awk/sed stages and the bin/logsleuth driver.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LS="$ROOT/bin/logsleuth"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export TMPDIR="$WORK/tmp"; mkdir -p "$TMPDIR"

pass=0 fail=0
ok()   { pass=$((pass + 1)); }
bad()  { fail=$((fail + 1)); printf 'FAIL: %s\n' "$1"; [ $# -lt 2 ] || printf '%s\n' "$2"; }

# assert_eq NAME EXPECTED ACTUAL
assert_eq() {
    if [ "$2" = "$3" ]; then ok; else bad "$1" "  expected: $2"$'\n'"  actual:   $3"; fi
}
# assert_contains NAME NEEDLE HAYSTACK
assert_contains() {
    case "$3" in *"$2"*) ok ;; *) bad "$1" "  missing: $2" ;; esac
}
assert_not_contains() {
    case "$3" in *"$2"*) bad "$1" "  unexpected: $2" ;; *) ok ;; esac
}

T=$'\t'

# ---------------------------------------------------------------- parse ----
parse() { "$LS" parse "$@" 2>/dev/null; }

printf 'Jul 12 03:14:15 web01 nginx[812]: upstream timed out (110: Connection timed out)\n' > "$WORK/sys.log"
assert_eq "syslog line" \
    "2026-07-12 03:14:15${T}web01${T}nginx${T}ERROR${T}upstream timed out (110: Connection timed out)" \
    "$(parse --year 2026 "$WORK/sys.log")"

printf 'Jul  2 03:14:16 h sshd[1]: Accepted publickey for u\n' > "$WORK/sys.log"
assert_eq "single-digit day is padded" "2026-07-02 03:14:16" \
    "$(parse --year 2026 "$WORK/sys.log" | cut -f1)"

printf '%s\n' 'Dec 31 23:59:59 h a: x' 'Jan  1 00:00:01 h a: y' > "$WORK/sys.log"
assert_eq "year rolls over at new year" "2026-12-31 23:59:59 2027-01-01 00:00:01" \
    "$(parse --year 2026 "$WORK/sys.log" | cut -f1 | paste -sd ' ' | sed 's/\t/ /')"

printf '2026-07-21T02:18:05.123+00:00 db01 postgres[77]: FATAL:  remaining connection slots\n' > "$WORK/iso.log"
assert_eq "iso syslog" "2026-07-21 02:18:05${T}db01${T}postgres${T}CRIT${T}FATAL:  remaining connection slots" \
    "$(parse "$WORK/iso.log")"

printf '2026-07-21 02:18:04,512 ERROR [pool-3] com.acme.Db - Connection is not available\n' > "$WORK/app.log"
assert_eq "app log with thread and logger" \
    "2026-07-21 02:18:04${T}-${T}com.acme.Db${T}ERROR${T}Connection is not available" \
    "$(parse "$WORK/app.log")"

printf '2026-07-21 02:18:05 [WARN] pool nearly full\n' > "$WORK/app.log"
assert_eq "app log with bracketed level" "WARN${T}pool nearly full" \
    "$(parse "$WORK/app.log" | cut -f4,5)"

printf 'time=2026-07-21T02:18:09Z level=error msg="cache miss storm" service=api host=api02\n' > "$WORK/kv.log"
assert_eq "logfmt" "2026-07-21 02:18:09${T}api02${T}api${T}ERROR${T}cache miss storm" \
    "$(parse "$WORK/kv.log")"

assert_eq "--host overrides the default" "edge1" \
    "$(parse --host edge1 "$WORK/app.log" | cut -f2)"

printf 'garbage\n\nmore garbage\n' > "$WORK/junk.log"
assert_eq "unparseable lines are dropped" "" "$(parse "$WORK/junk.log")"
assert_contains "skipped lines are reported" "0 records, 2 lines skipped" \
    "$("$LS" parse "$WORK/junk.log" 2>&1 >/dev/null)"

printf 'Jul 12 03:14:15 h p: gz works\n' | gzip > "$WORK/z.log.gz"
assert_eq "gzip input" "gz works" "$(parse --year 2026 "$WORK/z.log.gz" | cut -f5)"
assert_eq "stdin input" "gz works" \
    "$(printf 'Jul 12 03:14:15 h p: gz works\n' | "$LS" parse --year 2026 - 2>/dev/null | cut -f5)"

# ----------------------------------------------------- level inference ----
level_of() { printf 'Jul 12 03:14:15 h p: %s\n' "$1" > "$WORK/l.log"; parse --year 2026 "$WORK/l.log" | cut -f4; }
assert_eq "level: oom is critical"  CRIT  "$(level_of 'Out of memory: Killed process 1')"
assert_eq "level: timeout is error" ERROR "$(level_of 'upstream timed out')"
assert_eq "level: retry is warning" WARN  "$(level_of 'retrying in 5s')"
assert_eq "level: default is info"  INFO  "$(level_of 'Started Daily apt')"

# ------------------------------------------------------------ normalize ----
sig() {
    printf '2026-07-21 02:18:04\th\tp\tERROR\t%s\n' "$1" | "$LS" normalize | cut -f6
}
assert_eq "numbers"  "timed out after <N>ms" "$(sig 'timed out after 30000ms')"
assert_eq "decimals" "took <N>s" "$(sig 'took 3.52s')"
assert_eq "ipv4"     "client <IP>, retry" "$(sig 'client 203.0.113.45:8080, retry')"
assert_eq "uuid"     "request <UUID> failed" "$(sig 'request 3f2504e0-4f89-41d3-9a0c-0305e82c3301 failed')"
assert_eq "hex"      "at <HEX> sha <HEX>" "$(sig 'at 0xdeadbeef sha 9f86d081884c7d659a2feaa0c55ad015')"
assert_eq "ssh user" "Failed password for <USER> from <IP> port <N>" \
    "$(sig 'Failed password for invalid user admin from 198.51.100.7 port 51022')"
assert_eq "whitespace" "a b c" "$(sig '  a    b   c  ')"
assert_eq "same signature for different values" "$(sig 'pool 3 of 20 busy')" "$(sig 'pool 17 of 20 busy')"
assert_eq "different text stays different" "no" \
    "$([ "$(sig 'disk full')" = "$(sig 'disk slow')" ] && echo yes || echo no)"

# ------------------------------------------------------------ analyze ------
SAMPLES="$ROOT/samples"
report="$("$LS" analyze -q --year 2026 "$SAMPLES/db-pool/syslog.log" "$SAMPLES/db-pool/app.log")"
assert_contains "db-pool: one incident"      "**Incidents**: 1" "$report"
assert_contains "db-pool: leading cause"     "slow-queries-locks" "$report"
assert_contains "db-pool: pool exhaustion"   "db-connections-exhausted" "$report"
assert_contains "db-pool: names the deploy"  "Deploying release v2.14.0" "$report"
assert_contains "db-pool: has a fix section" "### Recommended fix" "$report"
assert_not_contains "db-pool: sshd login is not a change" "Accepted publickey" "$report"

report="$("$LS" analyze -q --year 2026 "$SAMPLES/oom/syslog.log" "$SAMPLES/oom/app.log")"
assert_contains "oom: two incidents"  "**Incidents**: 2" "$report"
assert_contains "oom: names the kill" "out-of-memory" "$report"

"$LS" analyze -q --year 2026 -o "$WORK/out.md" "$SAMPLES/oom/syslog.log" >/dev/null
assert_contains "--out writes the report" "# Incident analysis" "$(cat "$WORK/out.md")"

report="$("$LS" analyze -q "$WORK/junk.log" 2>&1)"
assert_contains "no records is handled" "No log records could be parsed" "$report"

# a quiet log produces a report, not an error
printf '%s\n' 'Jul 12 03:14:15 h p: hello' 'Jul 12 03:15:15 h p: world' > "$WORK/quiet.log"
assert_contains "quiet log has no incidents" "**Incidents**: none" \
    "$("$LS" analyze -q --year 2026 "$WORK/quiet.log")"

# ------------------------------------------------------------ driver -------
"$LS" analyze -q "$WORK/does-not-exist.log" >/dev/null 2>&1; assert_eq "missing file exits 2" 2 "$?"
"$LS" analyze >/dev/null 2>&1;                               assert_eq "no files exits 2" 2 "$?"
"$LS" bogus >/dev/null 2>&1;                                 assert_eq "unknown command exits 2" 2 "$?"
assert_contains "--version" "logsleuth " "$("$LS" --version)"
assert_contains "--help lists analyze" "analyze" "$("$LS" --help)"

ln -s "$LS" "$WORK/logsleuth-link"
assert_contains "works through a symlink" "# Incident analysis" \
    "$("$WORK/logsleuth-link" analyze -q --year 2026 "$SAMPLES/oom/syslog.log")"

leftovers="$(ls -A "$TMPDIR")"
assert_eq "no temp files left behind" "" "$leftovers"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
