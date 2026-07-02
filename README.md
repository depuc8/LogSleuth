# LogSleuth

A Unix log analyzer and root-cause tool. Point it at syslog and application
logs and it will:

1. parse them with `awk` into a normalized record stream,
2. collapse messages into signatures with `sed`,
3. group errors by signature and flag spikes, and
4. write a Markdown RCA summary: timeline, likely cause, suggested fix.

Status: work in progress.
