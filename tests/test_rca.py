import unittest

from logsleuth.analyze import Options, analyze
from logsleuth.rca import diagnose_all
from logsleuth.records import Record
from logsleuth.report import _cell, render
from logsleuth.rules import load_rules, match_rule

BASE = 1_784_600_000        # 2026-07-21 02:13:20 UTC


def rec(offset, level, msg, proc="app", host="h"):
    return Record(BASE + offset, host, proc, level, msg, msg)


def incident_log():
    """An hour of quiet traffic, a deploy, pool errors, then gateway errors."""
    rs = [rec(t, "INFO", "ok") for t in range(0, 7200, 10)]
    rs += [rec(t, "ERROR", "stray failure") for t in range(100, 7200, 700)]
    rs.append(rec(3590, "INFO", "deploying release v<N>", "deploy"))
    rs += [rec(t, "ERROR", "remaining connection slots are reserved", "postgres", "db01")
           for t in range(3600, 3900, 3)]
    rs += [rec(t + 30, "ERROR", "upstream timed out while reading response header",
               "nginx", "web01") for t in range(3600, 3900, 4)]
    return rs


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()

    def test_bundled_rules_load_and_compile(self):
        self.assertGreater(len(self.rules), 10)
        self.assertEqual(len({r.id for r in self.rules}), len(self.rules))

    def test_matching(self):
        cases = {
            "FATAL: remaining connection slots are reserved": "db-connections-exhausted",
            "Out of memory: Killed process <N> (java)": "out-of-memory",
            "No space left on device": "disk-full",
            "upstream timed out (<N>: Connection timed out)": "upstream-timeouts",
        }
        for text, rule_id in cases.items():
            self.assertEqual(match_rule(self.rules, text).id, rule_id, text)
        self.assertIsNone(match_rule(self.rules, "everything is fine"))

    def test_heaviest_rule_wins(self):
        # "connection timed out ... connection pool" hits both a symptom rule
        # and a cause rule; the cause must win
        text = "connection pool timeout: request timed out after <N>ms"
        self.assertEqual(match_rule(self.rules, text).id, "db-connections-exhausted")


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()
        self.analysis = analyze(incident_log(), Options())
        self.diagnoses = diagnose_all(self.analysis, self.rules)

    def test_one_incident_around_the_errors(self):
        self.assertEqual(len(self.diagnoses), 1)
        inc = self.diagnoses[0].incident
        self.assertLessEqual(inc.start, BASE + 3600)
        self.assertGreaterEqual(inc.end, BASE + 3900)

    def test_root_cause_is_the_backend_not_the_gateway(self):
        d = self.diagnoses[0]
        self.assertEqual(d.rule.id, "db-connections-exhausted")
        self.assertIn("connection slots", d.cause.sig)
        self.assertEqual(d.confidence, "high")

    def test_gateway_errors_are_downstream(self):
        d = self.diagnoses[0]
        self.assertEqual([c.rule.id for c in d.downstream], ["upstream-timeouts"])

    def test_background_error_is_not_a_candidate(self):
        sigs = [c.sig for c in self.diagnoses[0].candidates]
        self.assertNotIn("stray failure", sigs)

    def test_deploy_is_cited_as_the_change(self):
        d = self.diagnoses[0]
        self.assertIn("deploying release", d.change.msg)

    def test_quiet_log_yields_no_incidents(self):
        rs = [rec(t, "INFO", "ok") for t in range(0, 3600, 10)]
        rs += [rec(500, "ERROR", "one-off")]
        a = analyze(rs, Options())
        self.assertEqual(a.incidents, [])

    def test_empty_input(self):
        a = analyze([], Options())
        self.assertEqual(a.incidents, [])
        self.assertIn("No log records", render(a, [], ["x"]))


class ReportTests(unittest.TestCase):
    def setUp(self):
        rules = load_rules()
        a = analyze(incident_log(), Options())
        self.text = render(a, diagnose_all(a, rules), ["a.log", "b.log"])

    def test_sections_present(self):
        for heading in ("## Summary", "## Incident 1", "### Likely cause",
                        "### Timeline", "### Recommended fix",
                        "## Top error signatures"):
            self.assertIn(heading, self.text)

    def test_names_the_cause_and_fix(self):
        self.assertIn("connection pool is exhausted", self.text)
        self.assertIn("PgBouncer", self.text)

    def test_cell_escapes_pipes_and_backticks(self):
        self.assertEqual(_cell("a | b `c`"), "a \\| b 'c'")
        self.assertEqual(len(_cell("x" * 500, 20)), 20)

    def test_deterministic(self):
        rules = load_rules()
        a = analyze(incident_log(), Options())
        again = render(a, diagnose_all(a, rules), ["a.log", "b.log"])
        self.assertEqual(self.text, again)


if __name__ == "__main__":
    unittest.main()
