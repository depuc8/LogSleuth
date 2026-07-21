"""The analysis pipeline: records in, incidents (spikes plus context) out."""

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .context import (Index, Precursor, change_events, new_signatures,
                      precursors)
from .records import LEVELS, Record
from .signatures import Signature, group_by_signature
from .spikes import Spike, auto_bucket, bucketize, detect_spikes


@dataclass
class Options:
    width: Optional[int] = None    # bucket seconds; None = pick from the span
    z: float = 3.5                 # sigmas above baseline to call a spike
    min_count: int = 5             # ignore buckets with fewer events than this
    lead: int = 600                # seconds of run-up to inspect before onset
    tail: int = 300                # seconds after the spike ends
    lookback: int = 1800           # how far back to look for deploys/restarts
    top: int = 10                  # rows in the top-signature tables


@dataclass
class SigSpike:
    sig: Signature
    spike: Spike


@dataclass
class WindowSig:
    """A signature's activity inside an incident window."""
    sig: str
    level: str
    count: int
    first_in_window: int
    example: str
    source: str


@dataclass
class Incident:
    start: int
    end: int
    peak_ts: int
    peak: int                              # errors in the busiest bucket
    error_count: int
    spikes: List[SigSpike] = field(default_factory=list)
    global_spike: Optional[Spike] = None
    window_sigs: List[WindowSig] = field(default_factory=list)
    precursors: List[Precursor] = field(default_factory=list)
    changes: List[Record] = field(default_factory=list)
    new_sigs: List[str] = field(default_factory=list)


@dataclass
class Analysis:
    records: List[Record]
    options: Options
    t0: int
    t1: int
    width: int
    error_total: int
    warn_total: int
    signatures: Dict[str, Signature]       # ERROR and worse, by signature
    incidents: List[Incident] = field(default_factory=list)
    global_spikes: List[Spike] = field(default_factory=list)


def _cluster(intervals: List[Tuple[int, int]], gap: int) -> List[Tuple[int, int]]:
    """Merge intervals that overlap or sit within `gap` seconds of each other."""
    merged: List[List[int]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _window_sigs(index: Index, lo: int, hi: int) -> List[WindowSig]:
    seen: Dict[str, WindowSig] = {}
    counts: Counter = Counter()
    for rec in index.between(lo, hi):
        if not rec.is_error:
            continue
        counts[rec.sig] += 1
        if rec.sig not in seen:
            seen[rec.sig] = WindowSig(rec.sig, rec.level, 0, rec.ts,
                                      rec.msg, rec.source)
        elif LEVELS[rec.level] > LEVELS[seen[rec.sig].level]:
            seen[rec.sig].level = rec.level
    for sig, n in counts.items():
        seen[sig].count = n
    return sorted(seen.values(), key=lambda w: (-w.count, w.first_in_window))


def analyze(records: Sequence[Record], opts: Optional[Options] = None) -> Analysis:
    opts = opts or Options()
    recs = sorted(records, key=lambda r: r.ts)
    if not recs:
        return Analysis([], opts, 0, 0, opts.width or 60, 0, 0, {})

    span = recs[-1].ts - recs[0].ts
    width = opts.width or auto_bucket(span)
    t0 = recs[0].ts - recs[0].ts % width
    t1 = recs[-1].ts

    index = Index(recs)
    errors = [r for r in recs if r.is_error]
    warn_up = [r for r in recs if LEVELS.get(r.level, 0) >= LEVELS["WARN"]]
    error_sigs = group_by_signature(errors)
    warn_sigs = group_by_signature(warn_up)
    first_seen = {s: g.first_seen for s, g in group_by_signature(recs).items()}

    analysis = Analysis(
        records=recs, options=opts, t0=t0, t1=t1, width=width,
        error_total=len(errors), warn_total=len(warn_up) - len(errors),
        signatures=error_sigs,
    )

    # Spikes in the overall error rate, and in each warn+ signature on its own.
    err_counts = bucketize((r.ts for r in errors), t0, t1, width)
    analysis.global_spikes = detect_spikes(err_counts, t0, width,
                                           opts.z, opts.min_count)
    sig_spikes: List[SigSpike] = []
    for sig in warn_sigs.values():
        if sig.count < opts.min_count:
            continue
        counts = bucketize(sig.timestamps, t0, t1, width)
        for sp in detect_spikes(counts, t0, width, opts.z, opts.min_count):
            sig_spikes.append(SigSpike(sig, sp))

    intervals = [(s.start, s.end) for s in analysis.global_spikes]
    intervals += [(s.spike.start, s.spike.end) for s in sig_spikes]
    for lo, hi in _cluster(intervals, gap=2 * width):
        inc = Incident(start=lo, end=hi, peak_ts=lo, peak=0, error_count=0)
        for gs in analysis.global_spikes:
            if gs.start < hi and gs.end > lo and (
                    inc.global_spike is None or gs.peak > inc.global_spike.peak):
                inc.global_spike = gs
        inc.spikes = sorted(
            (ss for ss in sig_spikes if ss.spike.start < hi and ss.spike.end > lo),
            key=lambda ss: ss.spike.start)

        window = _window_sigs(index, lo, hi)
        inc.window_sigs = window
        inc.error_count = sum(w.count for w in window)
        if inc.global_spike:
            inc.peak_ts, inc.peak = inc.global_spike.peak_ts, inc.global_spike.peak
        elif inc.spikes:
            top = max(inc.spikes, key=lambda ss: ss.spike.peak)
            inc.peak_ts, inc.peak = top.spike.peak_ts, top.spike.peak

        lead_lo = lo - opts.lead
        inc.precursors = precursors(index, lead_lo, hi + opts.tail)
        inc.changes = change_events(index, lo - opts.lookback, hi + opts.tail)
        inc.new_sigs = new_signatures(first_seen, lead_lo, hi + opts.tail)
        analysis.incidents.append(inc)

    return analysis
