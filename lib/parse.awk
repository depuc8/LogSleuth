# parse.awk - turn raw log lines into LogSleuth's normalized record stream.
#
# Output is tab separated, one record per line:
#
#   timestamp<TAB>host<TAB>process<TAB>level<TAB>message
#
# timestamp is "YYYY-MM-DD HH:MM:SS" (taken as-is, no timezone conversion),
# level is one of DEBUG INFO WARN ERROR CRIT.
#
# Variables (-v):
#   year    year to assume for syslog timestamps, which don't carry one
#           (default: the current year)
#   source  label used in diagnostics (default: the input file name)
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

function emit(ts, host, proc, level, msg) {
    gsub(/\t/, " ", msg)
    sub(/[ \r]+$/, "", msg)
    printf "%s\t%s\t%s\t%s\t%s\n", ts, host, proc, level, msg
    parsed++
}

BEGIN {
    if (year == "") {
        "date +%Y" | getline year
        close("date +%Y")
    }
    parsed = 0
    skipped = 0
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

{ skipped++ }

END {
    printf "parse.awk: %s: %d records, %d lines skipped\n", \
        (source != "" ? source : FILENAME), parsed, skipped > "/dev/stderr"
}
