"""Group records by message signature."""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from .records import LEVELS, Record


@dataclass
class Signature:
    sig: str
    count: int = 0
    level: str = "DEBUG"              # worst level seen for this signature
    first_seen: int = 0
    last_seen: int = 0
    example: str = ""                 # raw message of the first occurrence
    sources: Counter = field(default_factory=Counter)
    timestamps: List[int] = field(default_factory=list)

    @property
    def id(self) -> str:
        return "sig-" + hashlib.sha1(self.sig.encode()).hexdigest()[:6]

    @property
    def top_source(self) -> str:
        return self.sources.most_common(1)[0][0] if self.sources else "-"

    def add(self, rec: Record) -> None:
        if self.count == 0:
            self.first_seen = rec.ts
            self.example = rec.msg
        self.count += 1
        self.last_seen = max(self.last_seen, rec.ts)
        self.first_seen = min(self.first_seen, rec.ts)
        if LEVELS.get(rec.level, 0) > LEVELS.get(self.level, 0):
            self.level = rec.level
        self.sources[rec.source] += 1
        self.timestamps.append(rec.ts)


def group_by_signature(records: Iterable[Record],
                       errors_only: bool = False) -> Dict[str, Signature]:
    """Map signature text -> Signature, in order of first appearance.

    With errors_only, records below ERROR are ignored (warnings and info
    lines are still useful as context, but they're not what we rank).
    """
    groups: Dict[str, Signature] = {}
    for rec in records:
        if errors_only and not rec.is_error:
            continue
        sig = groups.get(rec.sig)
        if sig is None:
            sig = groups[rec.sig] = Signature(sig=rec.sig)
        sig.add(rec)
    return groups


def top_signatures(groups: Dict[str, Signature], n: int = 10) -> List[Signature]:
    """Most frequent first; ties broken by earliest first appearance."""
    return sorted(groups.values(), key=lambda s: (-s.count, s.first_seen))[:n]
