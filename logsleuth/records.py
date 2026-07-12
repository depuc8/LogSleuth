"""The record model and the reader for the awk/sed stage's TSV output."""

import calendar
import heapq
import time
from dataclasses import dataclass
from typing import IO, Iterable, Iterator, List

LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "CRIT": 4}
ERROR_LEVEL = LEVELS["ERROR"]


@dataclass(frozen=True)
class Record:
    ts: int          # unix seconds; the log's wall clock read as UTC
    host: str
    proc: str
    level: str
    msg: str
    sig: str         # message with ids/numbers/addresses replaced

    @property
    def is_error(self) -> bool:
        return LEVELS.get(self.level, 0) >= ERROR_LEVEL

    @property
    def is_warning(self) -> bool:
        return LEVELS.get(self.level, 0) == LEVELS["WARN"]

    @property
    def source(self) -> str:
        """Who emitted it: process, qualified by host when we have one."""
        if self.host in ("", "-"):
            return self.proc
        return f"{self.host}/{self.proc}"


def parse_timestamp(text: str) -> int:
    """'YYYY-MM-DD HH:MM:SS' -> unix seconds. Raises ValueError if malformed."""
    if len(text) != 19 or text[4] != "-" or text[10] != " ":
        raise ValueError(f"bad timestamp {text!r}")
    return calendar.timegm((
        int(text[0:4]), int(text[5:7]), int(text[8:10]),
        int(text[11:13]), int(text[14:16]), int(text[17:19]),
    ))


def format_timestamp(ts: int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return time.strftime(fmt, time.gmtime(ts))


def read_records(stream: IO[str]) -> Iterator[Record]:
    """Yield Records from the 6-column TSV (ts, host, proc, level, msg, sig).

    Malformed lines are skipped; callers that care can count what they get.
    """
    for line in stream:
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 6:
            continue
        try:
            ts = parse_timestamp(parts[0])
        except ValueError:
            continue
        yield Record(ts, parts[1], parts[2], parts[3], parts[4], parts[5])


def merge_sorted(streams: Iterable[Iterable[Record]]) -> List[Record]:
    """Merge several record streams into one list ordered by time."""
    return list(heapq.merge(*(sorted(s, key=lambda r: r.ts) for s in streams),
                            key=lambda r: r.ts))
