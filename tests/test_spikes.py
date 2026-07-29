import random
import unittest

from logsleuth.spikes import (auto_bucket, baseline_stats, bucketize,
                              detect_spikes)


def noisy(n=120, seed=1):
    rng = random.Random(seed)
    return [rng.choice([0, 1, 1, 2]) for _ in range(n)]


class BucketTests(unittest.TestCase):
    def test_bucketize(self):
        self.assertEqual(bucketize([0, 5, 59, 60, 125], 0, 179, 60), [3, 1, 1])

    def test_ignores_out_of_range(self):
        self.assertEqual(bucketize([-5, 10, 500], 0, 59, 60), [1])

    def test_auto_bucket_scales_with_span(self):
        self.assertEqual(auto_bucket(600), 10)
        self.assertEqual(auto_bucket(2700), 30)
        self.assertEqual(auto_bucket(86400), 1800)
        self.assertEqual(auto_bucket(10 ** 9), 3600)


class BaselineTests(unittest.TestCase):
    def test_incident_does_not_inflate_baseline(self):
        counts = noisy()
        base = baseline_stats(counts)[0]
        counts[60:66] = [20, 60, 80, 70, 30, 12]
        self.assertEqual(baseline_stats(counts)[0], base)

    def test_sparse_series_has_nonzero_sigma(self):
        med, sigma = baseline_stats([0] * 50)
        self.assertEqual(med, 0)
        self.assertGreaterEqual(sigma, 1.0)

    def test_empty(self):
        self.assertEqual(baseline_stats([]), (0.0, 1.0))


class DetectTests(unittest.TestCase):
    def test_finds_one_merged_spike(self):
        counts = noisy()
        counts[60:66] = [20, 60, 80, 70, 30, 12]
        spikes = detect_spikes(counts, 1000, 60)
        self.assertEqual(len(spikes), 1)
        s = spikes[0]
        self.assertEqual(s.start, 1000 + 60 * 60)
        self.assertEqual(s.end, 1000 + 66 * 60)
        self.assertEqual((s.peak, s.peak_ts), (80, 1000 + 62 * 60))
        self.assertEqual(s.total, 272)
        self.assertEqual(s.duration, 360)

    def test_quiet_series_has_no_spikes(self):
        self.assertEqual(detect_spikes(noisy(), 0, 60), [])
        self.assertEqual(detect_spikes([0] * 30, 0, 60), [])
        self.assertEqual(detect_spikes([], 0, 60), [])

    def test_single_stray_error_is_not_a_spike(self):
        counts = [0] * 60
        counts[30] = 3          # below min_count
        self.assertEqual(detect_spikes(counts, 0, 60), [])

    def test_gap_merging(self):
        counts = [0] * 60
        counts[10], counts[12], counts[30] = 40, 40, 40
        spikes = detect_spikes(counts, 0, 60, merge_gap=1)
        self.assertEqual([(s.start, s.end) for s in spikes],
                         [(600, 780), (1800, 1860)])
        spikes = detect_spikes(counts, 0, 60, merge_gap=0)
        self.assertEqual(len(spikes), 3)


if __name__ == "__main__":
    unittest.main()
