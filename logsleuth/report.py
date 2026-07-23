"""Render an analysis as a Markdown RCA summary."""

from typing import Iterable, List, Sequence, Tuple

from . import __version__
from .analyze import Analysis
from .rca import Candidate, Diagnosis
from .records import format_timestamp
from .signatures import top_signatures

MAX_TIMELINE_ROWS = 30
CELL_WIDTH = 110


def _t(ts: int) -> str:
    return format_timestamp(ts)


def _hms(ts: int) -> str:
    return format_timestamp(ts, "%H:%M:%S")


def _duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if m else f"{h}h"


def _cell(text: str, width: int = CELL_WIDTH) -> str:
    """Make arbitrary log text safe inside a Markdown table cell."""
    text = text.replace("|", "\\|").replace("`", "'").replace("\n", " ")
    return text if len(text) <= width else text[:width - 1] + "…"


def _code(text: str, width: int = CELL_WIDTH) -> str:
    return f"`{_cell(text, width)}`"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> List[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _timeline(d: Diagnosis, analysis: Analysis) -> List[Tuple[int, str, str]]:
    inc = d.incident
    rows: List[Tuple[int, str, str]] = []

    for rec in inc.changes:
        if rec.ts <= inc.end:
            rows.append((rec.ts, "Change",
                         f"{_code(rec.msg)} ({rec.source})"))

    for i, c in enumerate(d.candidates):
        kind = "Warning" if c.level == "WARN" else "Error"
        note = f"{_code(c.sig)} first abnormal ({c.source}, {c.count:,}x)"
        if i == 0:
            note += " **<- likely root cause**"
        rows.append((c.onset, kind, note))

    if inc.global_spike:
        spike_text = (f"Error spike begins (baseline "
                      f"{inc.global_spike.baseline:g} per {analysis.width}s)")
    else:
        spike_text = "Spike begins in individual signatures"
    rows.append((inc.start, "Spike", spike_text))
    if inc.peak:
        rows.append((inc.peak_ts, "Peak",
                     f"Busiest bucket: {inc.peak:,} events in "
                     f"{analysis.width}s"))
    if inc.end < analysis.t1:
        rows.append((inc.end, "Recovery",
                     "Spike ends; rate back near baseline"))
    else:
        rows.append((analysis.t1, "End of log", "Still elevated when the log ends"))

    rows.sort(key=lambda r: r[0])
    if len(rows) > MAX_TIMELINE_ROWS:
        keep = rows[:MAX_TIMELINE_ROWS // 2] + rows[-MAX_TIMELINE_ROWS // 2:]
        rows = keep
    return rows


def _likely_cause(d: Diagnosis) -> List[str]:
    c = d.cause
    if c is None:
        return ["No leading signal could be identified: nothing new or "
                "unusually early accompanied this spike. Inspect the top "
                "signatures below."]
    lines = []
    if c.rule:
        lines.append(f"**{c.rule.category}**: {c.rule.cause}")
    else:
        lines.append(
            f"No known failure pattern matched. The first signature to go "
            f"abnormal was {_code(c.sig)} from `{c.source}`.")
    lines += ["", f"Confidence: **{d.confidence}**.", "", "Evidence:"]
    lines.append(f"- Leading signal {_code(c.sig)} from `{c.source}` went abnormal "
                 f"at {_t(c.onset)} and occurred {c.count:,} times in the incident.")
    lines.append(f"- Example: {_code(c.example, 160)}")
    for reason in c.reasons:
        lines.append(f"- {reason[0].upper() + reason[1:]}.")
    if d.change:
        gap = max(c.onset - d.change.ts, 0)
        when = "just before" if gap < 60 else f"{_duration(gap)} before"
        lines.append(f"- A change was logged {when} onset at {_t(d.change.ts)}: "
                     f"{_code(d.change.msg)} ({d.change.source}).")
    for s in d.supporting[:3]:
        lines.append(f"- Corroborated by {_code(s.sig)} ({s.source}, {s.count:,}x).")
    down = [x for x in d.downstream if x.rule and x.rule.role == "symptom"]
    if down:
        names = ", ".join(_code(x.sig, 60) for x in down[:3])
        lines.append(f"- Followed by downstream symptoms: {names}.")
    return lines


def _candidate_rows(cands: Sequence[Candidate]) -> List[List[str]]:
    return [[str(i), f"{c.score:.1f}", _t(c.onset), _code(c.sig, 70),
             _cell(c.source, 30), f"{c.count:,}",
             c.rule.id if c.rule else "-"]
            for i, c in enumerate(cands, 1)]


def render(analysis: Analysis, diagnoses: Sequence[Diagnosis],
           sources: Sequence[str], top: int = 10) -> str:
    out: List[str] = []
    out.append("# Incident analysis")
    out.append("")

    if not analysis.records:
        out.append("No log records could be parsed from the input.")
        return "\n".join(out) + "\n"

    out += ["## Summary", ""]
    out.append(f"- **Input**: {', '.join(f'`{s}`' for s in sources)}")
    out.append(f"- **Window**: {_t(analysis.t0)} to {_t(analysis.t1)} "
               f"({_duration(analysis.t1 - analysis.t0)}), "
               f"{len(analysis.records):,} records")
    out.append(f"- **Errors**: {analysis.error_total:,} across "
               f"{len(analysis.signatures):,} signatures "
               f"({analysis.warn_total:,} further warnings)")
    out.append(f"- **Detection**: {analysis.width}s buckets, "
               f"spike = median + {analysis.options.z:g} sigma (min "
               f"{analysis.options.min_count} events)")
    if diagnoses:
        out.append(f"- **Incidents**: {len(diagnoses)}")
        for n, d in enumerate(diagnoses, 1):
            cause = (d.rule.id if d.rule else
                     (_code(d.cause.sig, 60) if d.cause else "unknown"))
            out.append(f"  - [Incident {n}](#incident-{n}): "
                       f"{_hms(d.incident.start)}-{_hms(d.incident.end)}, "
                       f"{d.incident.error_count:,} errors, likely {cause} "
                       f"({d.confidence} confidence)")
    else:
        out.append("- **Incidents**: none. No error spikes stood out from the "
                   "baseline.")
    out.append("")

    for n, d in enumerate(diagnoses, 1):
        inc = d.incident
        out += [f"## Incident {n}", ""]
        out.append(f"**{_t(inc.start)} to {_t(inc.end)}** "
                   f"({_duration(inc.end - inc.start)}), "
                   f"{inc.error_count:,} errors, peak {inc.peak:,} "
                   f"in one {analysis.width}s bucket at {_hms(inc.peak_ts)}.")
        out += ["", "### Likely cause", ""] + _likely_cause(d)

        out += ["", "### Timeline", ""]
        out += _table(["Time", "Type", "Event"],
                      [[_hms(ts) if _t(ts)[:10] == _t(inc.start)[:10] else _t(ts),
                        kind, text]
                       for ts, kind, text in _timeline(d, analysis)])

        out += ["", "### Recommended fix", ""]
        out += [f"{i}. {step}" for i, step in enumerate(d.fix, 1)]

        out += ["", "### Errors during the incident", ""]
        out += _table(["Count", "Level", "First", "Signature", "Source"],
                      [[f"{w.count:,}", w.level, _hms(w.first_in_window),
                        _code(w.sig, 80), _cell(w.source, 30)]
                       for w in inc.window_sigs[:top]])
        if d.candidates:
            out += ["", "### Ranked candidates", ""]
            out += _table(["#", "Score", "Onset", "Signature", "Source",
                           "Count", "Rule"], _candidate_rows(d.candidates[:top]))
        out.append("")

    out += ["## Top error signatures", ""]
    sigs = top_signatures(analysis.signatures, top)
    if sigs:
        out += _table(["Count", "Level", "First seen", "Last seen",
                       "Signature", "Busiest source"],
                      [[f"{s.count:,}", s.level, _t(s.first_seen), _t(s.last_seen),
                        _code(s.sig, 80), _cell(s.top_source, 30)]
                       for s in sigs])
    else:
        out.append("No errors in the input.")
    out += ["", "---", f"Generated by LogSleuth {__version__}.", ""]
    return "\n".join(out)
