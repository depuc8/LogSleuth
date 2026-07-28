"""Look at the events around a time window: what changed, what warned first,
what showed up for the first time."""

import bisect
import re
from dataclasses import dataclass
from typing import Dict, List, Sequence

from .records import LEVELS, Record

# Operational events that often explain a change in behaviour. "started" and
# "stopped" are weak (systemd says them constantly) but cheap to include.
CHANGE_RE = re.compile(
    r"(deploy|releas(e|ing)|restart|reload|upgrad|migrat|roll(ed)?[ -]?back|"
    r"config(uration)? (change|updat|reload)|scal(e|ed|ing) (up|down|to)|"
    r"installed|started|stopped|shutting down|terminated)",
    re.IGNORECASE,
)

# The subset of changes that are deliberate (as opposed to a unit starting).
STRONG_CHANGE_RE = re.compile(
    r"(deploy|releas(e|ing)|upgrad|migrat|roll(ed)?[ -]?back|"
    r"config(uration)? (change|updat|reload)|scal(e|ed|ing) (up|down|to))",
    re.IGNORECASE,
)

# Auth chatter mentions "deploy" as a user name; it is not a deployment.
NOT_A_CHANGE_RE = re.compile(
    r"^(Accepted|Failed|Invalid|Received disconnect|Disconnected|"
    r"Connection (closed|reset)|pam_unix|session (opened|closed))",
    re.IGNORECASE,
)


class Index:
    """Time-ordered records with fast [lo, hi) slicing."""

    def __init__(self, records: Sequence[Record]):
        self.records = list(records)
        self._ts = [r.ts for r in self.records]

    def between(self, lo: int, hi: int) -> List[Record]:
        """Records with lo <= ts < hi."""
        a = bisect.bisect_left(self._ts, lo)
        b = bisect.bisect_left(self._ts, hi)
        return self.records[a:b]


@dataclass
class Precursor:
    """A warning-or-worse signature seen in the run-up to an incident."""
    sig: str
    level: str
    first_seen: int
    count: int
    example: str
    source: str


def change_events(index: Index, lo: int, hi: int, limit: int = 12) -> List[Record]:
    """Deploy/restart/reload style events in [lo, hi), oldest first.

    Consecutive repeats of the same signature are collapsed to the first, and
    only the most recent `limit` events are kept (closest to the incident).
    """
    found: List[Record] = []
    last_sig = None
    for rec in index.between(lo, hi):
        if (rec.is_error or NOT_A_CHANGE_RE.search(rec.msg)
                or not CHANGE_RE.search(rec.msg)):
            continue
        if rec.sig == last_sig:
            continue
        last_sig = rec.sig
        found.append(rec)
    return found[-limit:]


def precursors(index: Index, lo: int, hi: int,
               min_level: str = "WARN") -> List[Precursor]:
    """Warning-or-worse signatures in [lo, hi), ordered by first appearance."""
    floor = LEVELS[min_level]
    by_sig: Dict[str, Precursor] = {}
    for rec in index.between(lo, hi):
        if LEVELS.get(rec.level, 0) < floor:
            continue
        p = by_sig.get(rec.sig)
        if p is None:
            by_sig[rec.sig] = Precursor(rec.sig, rec.level, rec.ts, 1,
                                        rec.msg, rec.source)
        else:
            p.count += 1
            if LEVELS[rec.level] > LEVELS.get(p.level, 0):
                p.level = rec.level
    return sorted(by_sig.values(), key=lambda p: p.first_seen)


def new_signatures(first_seen: Dict[str, int], lo: int, hi: int) -> List[str]:
    """Signatures whose very first appearance in the input is within [lo, hi)."""
    return sorted((s for s, t in first_seen.items() if lo <= t < hi),
                  key=first_seen.__getitem__)
