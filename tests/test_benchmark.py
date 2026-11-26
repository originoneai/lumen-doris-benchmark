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


class IndustryMatrixTest(unittest.TestCase):
    def test_each_industry_has_its_own_warehouse_and_full_coverage(self):
        databases = set()
        positive_cases = 0
        negative_cases = 0
        legacy_sizes = []
        for key in CONTRACT["databases"]:
            with self.subTest(dataset=key):
                domain, warehouse, tasks, _, _, report = assemble(key, CONTRACT["default_seed"])
                self.assertNotIn(report["database"], databases)
                databases.add(report["database"])
                self.assertGreaterEqual(report["tables"], CONTRACT["minimum_business_tables"])
                self.assertEqual(report["connected_tables"], report["tables"])
                self.assertEqual(report["reference_checks"], report["sqlite_reference_checks"])
                self.assertEqual(report["etl_jobs"], report["sqlite_etl_cross_checks"])
                self.assertGreaterEqual(len({p.source for p in domain.processes}), 5)
                self.assertEqual(len(warehouse.facts), len(domain.processes))
                self.assertTrue(
                    all(len(x["turns"]) >= 3 for x in tasks if x["family"] in {"multi_turn", "explore", "report"})
                )
                positive_cases += report["tasks"] - report["coverage"]["counterexample"]
                negative_cases += report["coverage"]["counterexample"]
                legacy_sizes.append(report["legacy_marts"])
        self.assertEqual(len(databases), 5)
        self.assertGreaterEqual(positive_cases, 110)
        self.assertGreaterEqual(negative_cases, 30)
        self.assertEqual(legacy_sizes, sorted(set(legacy_sizes)))

    def test_wholesale_retains_duplicates_bad_sql_and_obfuscated_metadata(self):
        domain, warehouse, _, _, _, report = assemble("wholesale", CONTRACT["default_seed"])
        self.assertEqual(report["commented_columns"], 0)
        self.assertGreater(report["obfuscated_field_columns"], 1000)
        self.assertGreaterEqual(report["legacy_marts"], 25)
        self.assertGreaterEqual(report["duplicate_legacy_groups"], 3)
        self.assertTrue({"test", "ceshi"} <= warehouse.tables.keys())
        self.assertTrue({"replay", "fanout", "unit_scale", "scope_loss"} <= set(report["known_issue_types"]))
        self.assertLess(sum(map(len, domain.documents.values())), 160)

    def test_second_seed_changes_data_and_still_supports_each_task(self):
        for key in CONTRACT["databases"]:
            with self.subTest(dataset=key):
                report = assemble(key, 7720)[-1]
                self.assertEqual(report["reference_checks"], report["sqlite_reference_checks"])


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
