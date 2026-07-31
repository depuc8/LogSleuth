# parse.awk - turn raw log lines into LogSleuth's normalized record stream.
#
# Output is tab separated, one record per line:
#
#   timestamp<TAB>host<TAB>process<TAB>level<TAB>message
#
# timestamp is "YYYY-MM-DD HH:MM:SS" (taken as-is, no timezone conversion),
# level is one of DEBUG INFO WARN ERROR CRIT.
#
# Recognised formats:
#   syslog     Jul 12 03:14:15 host proc[123]: message
#   iso-syslog 2026-07-12T03:14:15.123+00:00 host proc[123]: message
#   app log    2026-07-12 03:14:15,123 ERROR [thread] logger - message
#              2026-07-12 03:14:15 [WARN] message
#   logfmt     time=2026-07-12T03:14:15Z level=error msg="message" ...
#
# Variables (-v):
#   year    year to assume for syslog timestamps, which don't carry one
#           (default: the current year)
#   host    host name for formats that don't carry one (default: "-")
#   source  label used in diagnostics (default: the input file name)
#   quiet   set to 1 to suppress the per-file summary on stderr
#
# Stack traces: frames are dropped, and the last exception line of the trace
# (the root cause in a Java "Caused by:" chain, the final line of a Python
# traceback) is appended to the message of the record it belongs to as
# "[ExceptionType: text]".
#
# Only POSIX awk features are used, so this runs on gawk, mawk and BSD awk.

function month_num(m) {
    # position of the name in the string, or a non-integer/zero result for
    # anything that isn't a month ("anF" straddles two names)
    return (index("JanFebMarAprMayJunJulAugSepOctNovDec", m) - 1) / 3 + 1
}

# Guess a severity from the text of a message when the log line has no
# explicit level (plain syslog doesn't).
function infer_level(msg,    m) {
    m = tolower(msg)
    if (m ~ /(panic|fatal|emerg|critical|segfault|out of memory|oom-kill|kernel bug)/) return "CRIT"
    if (m ~ /(error|fail|exception|denied|refused|timed out|timeout|unreachable|abort|no space left|cannot|can't|unable to)/) return "ERROR"
    if (m ~ /(warn|deprecated|retry|retrying|slow|degraded|throttl)/) return "WARN"
    if (m ~ /debug/) return "DEBUG"
    return "INFO"
}

# Map the many spellings of a severity onto DEBUG INFO WARN ERROR CRIT.
# Returns "" for anything that isn't a severity word.
function norm_level(w) {
    w = toupper(w)
    if (w == "TRACE" || w == "DEBUG") return "DEBUG"
    if (w == "INFO" || w == "NOTICE") return "INFO"
    if (w == "WARN" || w == "WARNING") return "WARN"
    if (w == "ERROR" || w == "ERR" || w == "SEVERE") return "ERROR"
    if (w == "CRIT" || w == "CRITICAL" || w == "FATAL" || w == "ALERT" || \
        w == "EMERG" || w == "PANIC") return "CRIT"
    return ""
}

# Pull key=value out of a logfmt line; handles key="quoted value".
function logfmt_get(line, key,    pat, v) {
    pat = "(^| )" key "=\"[^\"]*\""
    if (match(line, pat)) {
        v = substr(line, RSTART, RLENGTH)
        sub(/^ ?[^=]+="/, "", v)
        sub(/"$/, "", v)
        return v
    }
    pat = "(^| )" key "=[^ ]*"
    if (match(line, pat)) {
        v = substr(line, RSTART, RLENGTH)
        sub(/^ ?[^=]+=/, "", v)
        return v
    }
    return ""
}

# A record is held back until the next one starts, so that any stack trace
# lines that follow it can be folded into it.
function flush(    msg) {
    if (!have) return
    msg = pend_msg
    if (pend_exc != "") msg = msg " [" pend_exc "]"
    gsub(/\t/, " ", msg)
    sub(/[ \r]+$/, "", msg)
    printf "%s\t%s\t%s\t%s\t%s\n", pend_ts, pend_host, pend_proc, pend_level, msg
    have = 0
}

function emit(ts, host, proc, level, msg) {
    flush()
    pend_ts = ts; pend_host = host; pend_proc = proc
    pend_level = level; pend_msg = msg; pend_exc = ""
    have = 1
    parsed++
}

BEGIN {
    if (year == "") {
        "date +%Y" | getline year
        close("date +%Y")
    }
    parsed = 0
    skipped = 0
    folded = 0
    have = 0
}

# RFC 3164 syslog:  Jul 12 03:14:15 host proc[123]: message
/^[A-Z][a-z][a-z] +[0-9][0-9]? [0-9][0-9]:[0-9][0-9]:[0-9][0-9] / {
    mon = month_num($1)
    if (mon != int(mon) || mon < 1) { skipped++; next }
    # a log that runs through New Year goes from Dec back to Jan
    if (prev_mon - mon > 6) year++
    prev_mon = mon

    host = $4
    rest = $0
    sub(/^[A-Z][a-z][a-z] +[0-9]+ [0-9:]+ [^ ]+ ?/, "", rest)

    proc = "-"
    if (match(rest, /^[^ :\[]+(\[[0-9]+\])?: ?/)) {
        tag = substr(rest, 1, RLENGTH)
        rest = substr(rest, RLENGTH + 1)
        sub(/\[[0-9]+\]/, "", tag)
        sub(/: ?$/, "", tag)
        proc = tag
    }

    ts = sprintf("%04d-%02d-%02d %s", year, mon, $2, $3)
    emit(ts, host, proc, infer_level(rest), rest)
    next
}

# ISO-8601 timestamp: either iso-syslog or an application log line.
/^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][T ][0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ {
    ts = substr($0, 1, 10) " " substr($0, 12, 8)
    rest = substr($0, 20)
    sub(/^[.,][0-9]+/, "", rest)                          # fractional seconds
    sub(/^ ?(Z|[+-][0-9][0-9]:?[0-9][0-9])/, "", rest)    # UTC offset
    sub(/^ +/, "", rest)

    # application log: severity word right after the timestamp
    w = rest
    sub(/[ \t].*$/, "", w)
    lvl = norm_level(w)
    gsub(/[\[\]:]/, "", w)
    if (lvl == "") lvl = norm_level(w)
    if (lvl != "") {
        sub(/^[^ \t]+ */, "", rest)
        proc = "-"
        if (match(rest, /^\[[^]]*\] */)) {            # [thread]
            rest = substr(rest, RLENGTH + 1)
        }
        if (match(rest, /^[A-Za-z_][A-Za-z0-9_.$\/-]* +- +/)) {   # logger -
            proc = substr(rest, 1, RLENGTH)
            sub(/ +- +$/, "", proc)
            rest = substr(rest, RLENGTH + 1)
        } else if (match(rest, /^[A-Za-z_][A-Za-z0-9_.$\/-]*: /)) { # logger:
            proc = substr(rest, 1, RLENGTH - 2)
            rest = substr(rest, RLENGTH + 1)
        }
        emit(ts, (host != "" ? host : "-"), proc, lvl, rest)
        next
    }

    # iso-syslog: host proc[pid]: message
    if (match(rest, /^[^ ]+ [^ :\[]+(\[[0-9]+\])?: ?/)) {
        head = substr(rest, 1, RLENGTH)
        rest = substr(rest, RLENGTH + 1)
        h = head
        sub(/ .*$/, "", h)
        sub(/^[^ ]+ /, "", head)
        sub(/\[[0-9]+\]/, "", head)
        sub(/: ?$/, "", head)
        emit(ts, h, head, infer_level(rest), rest)
        next
    }

    # timestamp but no recognisable structure: keep the text, guess the level
    emit(ts, (host != "" ? host : "-"), "-", infer_level(rest), rest)
    next
}

# logfmt: time=... level=... msg="..."
/(^| )(time|ts|timestamp)=[0-9][0-9][0-9][0-9]-/ && /(^| )level=/ {
    t = logfmt_get($0, "time")
    if (t == "") t = logfmt_get($0, "ts")
    if (t == "") t = logfmt_get($0, "timestamp")
    ts = substr(t, 1, 10) " " substr(t, 12, 8)
    lvl = norm_level(logfmt_get($0, "level"))
    if (lvl == "") lvl = "INFO"
    m = logfmt_get($0, "msg")
    if (m == "") m = logfmt_get($0, "message")
    if (m == "") m = $0
    p = logfmt_get($0, "component")
    if (p == "") p = logfmt_get($0, "service")
    if (p == "") p = "-"
    h = logfmt_get($0, "host")
    if (h == "") h = (host != "" ? host : "-")
    emit(ts, h, p, lvl, m)
    next
}

# blank lines are just spacing, not lost data
/^[ \t\r]*$/ { next }

# stack trace lines belong to the record above them
have && (/^[ \t]+at / || /^[ \t]+File "/ || /^Traceback \(most recent/ || \
         /^[ \t]*\.\.\. [0-9]+ (more|common frames)/) { folded++; next }
have && /^(Caused by: )?[A-Za-z_][A-Za-z0-9_.$]*(Exception|Error)(:|$)/ {
    pend_exc = $0
    sub(/^Caused by: /, "", pend_exc)
    folded++
    next
}

{ skipped++ }

END {
    flush()
    if (quiet == 1) exit
    extra = (folded > 0) ? sprintf(", %d stack trace lines folded in", folded) : ""
    printf "parse.awk: %s: %d records, %d lines skipped%s\n", \
        (source != "" ? source : FILENAME), parsed, skipped, extra > "/dev/stderr"
}
