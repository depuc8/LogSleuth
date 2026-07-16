"""Bucket events over time and flag buckets that are far above normal.

The baseline is the median bucket count with a MAD-based spread, so a
handful of huge buckets (the incident we are looking for) can't drag the
baseline up and hide themselves the way a mean/stddev would.
"""

import math
import statistics
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

NICE_WIDTHS = (10, 30, 60, 300, 600, 1800, 3600)


@dataclass
class Spike:
    start: int          # first spiking bucket's start (unix seconds)
    end: int            # end of the last spiking bucket (exclusive)
    peak_ts: int        # start of the busiest bucket
    peak: int           # count in the busiest bucket
    total: int          # events across the whole spike
    baseline: float     # median bucket count
    score: float        # how many "sigmas" the peak is above baseline

    @property
    def duration(self) -> int:
        return self.end - self.start


def auto_bucket(span: int, target_buckets: int = 120) -> int:
    """Pick a bucket width that gives roughly target_buckets over the span."""
    want = max(span, 1) / target_buckets
    for w in NICE_WIDTHS:
        if w >= want:
            return w
    return NICE_WIDTHS[-1]


def bucketize(timestamps: Iterable[int], t0: int, t1: int,
              width: int) -> List[int]:
    """Count timestamps per bucket over [t0, t1]; t0 should be aligned."""
    n = (t1 - t0) // width + 1
    counts = [0] * n
    for ts in timestamps:
        i = (ts - t0) // width
        if 0 <= i < n:
            counts[i] += 1
    return counts


def baseline_stats(counts: Sequence[int]) -> Tuple[float, float]:
    """(median, sigma) of the series; sigma is MAD scaled to stddev-ish units.

    Sparse series have MAD 0, which would make every nonzero bucket look
    infinitely anomalous, so sigma is floored at the Poisson spread
    sqrt(median) (and at 1).
    """
    if not counts:
        return 0.0, 1.0
    med = statistics.median(counts)
    mad = statistics.median(abs(c - med) for c in counts)
    sigma = max(1.4826 * mad, math.sqrt(max(med, 1.0)), 1.0)
    return med, sigma


def detect_spikes(counts: Sequence[int], t0: int, width: int,
                  z: float = 3.5, min_count: int = 5,
                  merge_gap: int = 1) -> List[Spike]:
    """Find runs of buckets whose count is well above the baseline.

    A bucket spikes when count >= median + z*sigma and count >= min_count.
    Spiking buckets separated by at most merge_gap quiet buckets are merged
    into one Spike, so a ragged incident is reported once.
    """
    med, sigma = baseline_stats(counts)
    limit = max(med + z * sigma, min_count)
    hot = [i for i, c in enumerate(counts) if c >= limit]

    runs: List[List[int]] = []
    for i in hot:
        if runs and i - runs[-1][-1] <= merge_gap + 1:
            runs[-1].append(i)
        else:
            runs.append([i])

    spikes = []
    for run in runs:
        lo, hi = run[0], run[-1]
        seg = counts[lo:hi + 1]
        peak_i = lo + max(range(len(seg)), key=seg.__getitem__)
        spikes.append(Spike(
            start=t0 + lo * width,
            end=t0 + (hi + 1) * width,
            peak_ts=t0 + peak_i * width,
            peak=counts[peak_i],
            total=sum(seg),
            baseline=med,
            score=(counts[peak_i] - med) / sigma,
        ))
    return spikes
