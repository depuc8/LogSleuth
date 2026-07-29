import io
import unittest

from logsleuth.records import (Record, format_timestamp, merge_sorted,
                               parse_timestamp, read_records)


class TimestampTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(format_timestamp(parse_timestamp("2026-07-21 02:18:04")),
                         "2026-07-21 02:18:04")

    def test_epoch(self):
        self.assertEqual(parse_timestamp("1970-01-01 00:00:10"), 10)

    def test_rejects_garbage(self):
        for bad in ("", "2026-07-21", "2026/07/21 02:18:04", "Jul 21 02:18:04"):
            with self.assertRaises(ValueError):
                parse_timestamp(bad)


class ReadRecordsTests(unittest.TestCase):
    def test_skips_malformed_lines(self):
        data = (
            "2026-07-21 02:18:04\tweb01\tnginx\tERROR\tboom 1\tboom <N>\n"
            "not a record\n"
            "2026-07-21 02:18:05\tweb01\tnginx\tERROR\ttoo few columns\n"
            "bad-timestamp\ta\tb\tERROR\tm\ts\n"
            "2026-07-21 02:18:06\tweb01\tnginx\tWARN\tw\tw\n"
        )
        recs = list(read_records(io.StringIO(data)))
        self.assertEqual([r.level for r in recs], ["ERROR", "WARN"])
        self.assertEqual(recs[0].sig, "boom <N>")

    def test_level_helpers(self):
        mk = lambda lvl: Record(0, "h", "p", lvl, "m", "m")
        self.assertTrue(mk("ERROR").is_error)
        self.assertTrue(mk("CRIT").is_error)
        self.assertFalse(mk("WARN").is_error)
        self.assertTrue(mk("WARN").is_warning)

    def test_source_label(self):
        self.assertEqual(Record(0, "web01", "nginx", "INFO", "", "").source,
                         "web01/nginx")
        self.assertEqual(Record(0, "-", "nginx", "INFO", "", "").source, "nginx")

    def test_merge_sorted(self):
        a = [Record(t, "h", "p", "INFO", "m", "m") for t in (5, 1, 9)]
        b = [Record(t, "h", "q", "INFO", "m", "m") for t in (2, 8)]
        self.assertEqual([r.ts for r in merge_sorted([a, b])], [1, 2, 5, 8, 9])


if __name__ == "__main__":
    unittest.main()
