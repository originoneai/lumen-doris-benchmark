import json
from pathlib import Path
import tempfile
import unittest

from lumen_bench.doris import verify_database_name
from lumen_bench.grading import grade, load_lines
from lumen_bench.model import CONTRACT
from lumen_bench.suite import assemble, export


class RetailContractTest(unittest.TestCase):
    def test_current_release_and_local_data_cross_checks(self):
        _, warehouse, _, gold, _, report = assemble("retail", CONTRACT["default_seed"])
        self.assertGreaterEqual(report["tables"], 150)
        self.assertEqual(report["tables"], report["connected_tables"])
        self.assertEqual(report["reference_checks"], report["sqlite_reference_checks"])
        self.assertEqual(report["etl_jobs"], report["sqlite_etl_cross_checks"])
        self.assertEqual({row["rule"] for row in gold}, {"numeric", "exact", "review"})
        self.assertTrue(warehouse.facts["return_case"].upstream)

    def test_candidate_bundle_has_no_gold_and_rebuilds_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            status = export("retail", CONTRACT["default_seed"], first)
            self.assertEqual(status, export("retail", CONTRACT["default_seed"], second))
            self.assertFalse((first / "candidate" / "evaluator").exists())
            tasks = load_lines(first / "candidate" / "tasks.jsonl")
            self.assertTrue(all(set(row) == {"id", "family", "objective", "role", "tags", "turns"} for row in tasks))
            self.assertTrue((first / "evaluator" / "gold.jsonl").is_file())
            self.assertEqual(json.loads((first / "manifest.json").read_text()), status)
            with self.assertRaises(FileExistsError):
                export("retail", CONTRACT["default_seed"], first)

    def test_provisioner_rejects_any_non_fixture_database(self):
        verify_database_name(CONTRACT["databases"]["retail"]["name"])
        for name in ("default", "information_schema", "lumen_bench_unapproved_v1"):
            with self.assertRaises(ValueError):
                verify_database_name(name)


class GradingTest(unittest.TestCase):
    def test_grade_preserves_ranking_and_requires_independent_review(self):
        key = {"case": "demo", "turn": 1}
        ranking = [{"key": ["区域一"], "value": "2"}, {"key": ["区域二"], "value": "1"}]
        gold = [
            {**key, "check": "rank", "rule": "numeric", "ordered": True, "expected": ranking},
            {**key, "check": "piece", "rule": "review"},
            {**key, "check": "decision", "rule": "exact", "expected": "clarify"},
        ]
        observed = [
            {**key, "check": "rank", "value": ranking[::-1]},
            {**key, "check": "piece", "performer": "x", "reviewer": "x", "evidence": "report", "outcome": "pass"},
        ]
        self.assertEqual([r["status"] for r in grade(gold, observed)], ["fail", "review_pending", "missing"])
        observed[0]["value"] = ranking
        observed[1]["reviewer"] = "different"
        observed.append({**key, "check": "decision", "value": "clarify"})
        self.assertTrue(all(r["status"] == "pass" for r in grade(gold, observed)))


if __name__ == "__main__":
    unittest.main()
