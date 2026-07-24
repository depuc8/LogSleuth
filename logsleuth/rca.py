"""Root-cause ranking: which signature most plausibly started an incident."""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .analyze import Analysis, Incident
from .records import LEVELS, Record
from .rules import Rule, match_rule

GENERIC_FIX = [
    "Read the raw log lines around the first occurrence of the leading signature and identify the failing component.",
    "Check what changed shortly before onset (deploys, config, dependencies, traffic) and roll back if it lines up.",
    "Add a rule for this failure to a custom rules file so LogSleuth names it next time (see README).",
]


@dataclass
class Candidate:
    sig: str
    level: str
    example: str
    source: str
    onset: int                  # when it first looked abnormal
    count: int                  # occurrences inside the incident
    spiked: bool
    is_new: bool
    rule: Optional[Rule] = None
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class Diagnosis:
    incident: Incident
    candidates: List[Candidate]            # best first
    confidence: str                        # high / medium / low / none
    change: Optional[Record] = None        # most recent change before onset

    @property
    def cause(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None

    @property
    def rule(self) -> Optional[Rule]:
        return self.cause.rule if self.cause else None

    @property
    def fix(self) -> List[str]:
        return self.rule.fix if self.rule else GENERIC_FIX

    @property
    def supporting(self) -> List[Candidate]:
        """Other candidates pointing the same way as the leading one."""
        if not self.cause or not self.cause.rule:
            return []
        return [c for c in self.candidates[1:]
                if c.rule and c.rule.id == self.cause.rule.id]

    @property
    def downstream(self) -> List[Candidate]:
        """Later symptoms: candidates that started after the leading one."""
        if not self.cause:
            return []
        return [c for c in self.candidates[1:]
                if c.onset >= self.cause.onset and c not in self.supporting]


def _candidates(inc: Incident, rules: List[Rule]) -> List[Candidate]:
    """Signatures worth considering: spiking in the incident, or new to it.

    A warning that was already there before and didn't spike is background
    noise, not a lead.
    """
    new = set(inc.new_sigs)
    spike_start: Dict[str, int] = {}
    for ss in inc.spikes:
        spike_start.setdefault(ss.sig.sig, ss.first_ts)

    found: Dict[str, Candidate] = {}

    def upsert(sig, level, example, source, onset, count):
        c = found.get(sig)
        if c is None:
            found[sig] = Candidate(
                sig=sig, level=level, example=example, source=source,
                onset=onset, count=count, spiked=sig in spike_start,
                is_new=sig in new,
                rule=match_rule(rules, example) or match_rule(rules, sig))
        else:
            c.onset = min(c.onset, onset)
            c.count = max(c.count, count)
            if LEVELS[level] > LEVELS.get(c.level, 0):
                c.level = level

    for p in inc.precursors:
        if p.sig in new or p.sig in spike_start:
            upsert(p.sig, p.level, p.example, p.source, p.first_seen, p.count)
    for w in inc.window_sigs:
        if w.sig in new or w.sig in spike_start:
            upsert(w.sig, w.level, w.example, w.source, w.first_in_window, w.count)

    # A signature that was already around turned abnormal when it spiked (the
    # first occurrence inside the spike), not at its first stray occurrence;
    # a brand-new one starts at first sight.
    for sig, start in spike_start.items():
        c = found.get(sig)
        if c is not None:
            c.onset = min(c.onset, start) if c.is_new else start
    return list(found.values())


def _score(cands: List[Candidate]) -> None:
    if not cands:
        return
    t_lo = min(c.onset for c in cands)
    t_hi = max(c.onset for c in cands)
    biggest = max(c.count for c in cands)
    for c in cands:
        s = 0.0
        if c.rule:
            s += c.rule.weight if c.rule.role == "cause" else 0.5 * c.rule.weight
            c.reasons.append(
                f"matches known {c.rule.role} pattern '{c.rule.id}'")
        if t_hi > t_lo:
            early = 3.0 * (1 - (c.onset - t_lo) / (t_hi - t_lo))
        else:
            early = 3.0
        s += early
        if early >= 2.25 and len(cands) > 1:
            c.reasons.append("among the first to go abnormal")
        if c.is_new:
            s += 2.0
            c.reasons.append("first seen just before the incident")
        if c.spiked:
            s += 1.0
            c.reasons.append("spiked above its own baseline")
        if biggest > 1:
            s += 1.5 * math.log10(1 + c.count) / math.log10(1 + biggest)
        if c.level == "CRIT":
            s += 1.0
            c.reasons.append("logged at critical severity")
        c.score = s


def diagnose(analysis: Analysis, inc: Incident, rules: List[Rule]) -> Diagnosis:
    cands = _candidates(inc, rules)
    _score(cands)
    cands.sort(key=lambda c: (-c.score, c.onset))

    if not cands:
        confidence = "none"
    else:
        gap = cands[0].score - (cands[1].score if len(cands) > 1 else 0.0)
        best_rule = cands[0].rule
        if best_rule and best_rule.role == "cause" and gap >= 1.5:
            confidence = "high"
        elif (best_rule and best_rule.role == "cause") or gap >= 1.5:
            confidence = "medium"
        else:
            confidence = "low"

    change = None
    if cands:
        onset = min(c.onset for c in cands)
        before = [r for r in inc.changes if r.ts <= onset + analysis.width]
        change = before[-1] if before else None
    return Diagnosis(inc, cands, confidence, change)


def diagnose_all(analysis: Analysis, rules: List[Rule]) -> List[Diagnosis]:
    return [diagnose(analysis, inc, rules) for inc in analysis.incidents]
