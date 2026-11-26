"""Build and validate a domain, then emit separately consumable benchmark bundles."""

from collections import Counter, defaultdict, deque
from contextlib import closing
from datetime import date
from decimal import Decimal
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
import sqlite3
import tempfile

from .fixtures import aggregate, generate
from .model import CONTRACT
from .oracle import Query, decimal, from_journals, from_sql, query_plan
from .warehouse import build, ident


def load_domain(key):
    if key not in CONTRACT["databases"]:
        raise ValueError(f"unknown dataset: {key}")
    return import_module(f"{__package__}.domains.{key}")


def prepare(key, seed=CONTRACT["default_seed"]):
    module = load_domain(key)
    domain = module.DOMAIN
    journals = generate(domain, seed)
    warehouse = build(domain, journals)
    cases = module.scenarios(domain, warehouse)
    return domain, journals, warehouse, cases


def scalar(value):
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def dump_json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def row_digest(rows):
    lines = sorted(json.dumps([scalar(value) for value in row], ensure_ascii=False) for row in rows)
    return sha256("\n".join(lines).encode()).hexdigest()


def check_structure(warehouse):
    tables = warehouse.tables
    links, primary = defaultdict(set), {}
    for name, table in tables.items():
        ident(name)
        for field in table.columns:
            ident(field.name)
        if tuple(c.key for c in table.columns[: len(table.keys)]) != table.keys:
            raise ValueError(f"Doris key order is invalid: {name}")
        keys = [tuple(row[k] for k in table.keys) for row in table.rows]
        if len(set(keys)) != len(keys):
            raise ValueError(f"non-unique row key: {name}")
        for row in table.rows:
            if set(row) != {c.key for c in table.columns}:
                raise ValueError(f"row schema mismatch: {name}")
        primary[name] = {k: {row[k] for row in table.rows} for k in table.keys}
        for upstream in table.upstream:
            if upstream not in tables:
                raise ValueError(f"missing upstream: {name} -> {upstream}")
            links[name].add(upstream)
            links[upstream].add(name)
    relations = 0
    for name, table in tables.items():
        for ref in table.refs:
            target = primary[ref.table][ref.key]
            if len(target) != len(tables[ref.table].rows):
                raise ValueError(f"invalid FK target: {ref.table}.{ref.key}")
            if any(row[ref.column] not in target for row in table.rows):
                raise ValueError(f"orphan key: {name}.{ref.column}")
            links[name].add(ref.table)
            links[ref.table].add(name)
            relations += 1
    reachable = set()
    queue = deque([next(iter(tables))])
    while queue:
        name = queue.popleft()
        if name not in reachable:
            reachable.add(name)
            queue.extend(links[name] - reachable)
    if reachable != set(tables):
        raise ValueError(f"disconnected tables: {sorted(set(tables) - reachable)}")
    total_rows = sum(len(t.rows) for t in tables.values())
    if len(tables) < CONTRACT["minimum_business_tables"] or total_rows < CONTRACT["minimum_populated_rows"]:
        raise ValueError("warehouse below the published scale")
    return {
        "tables": len(tables),
        "connected_tables": len(reachable),
        "rows": total_rows,
        "foreign_key_relationships": relations,
        "table_types": dict(Counter(t.kind for t in tables.values())),
        "commented_columns": sum(bool(c.description) for t in tables.values() for c in t.columns),
        "obfuscated_field_columns": sum(c.name != c.key for t in tables.values() for c in t.columns),
        "etl_jobs": len(warehouse.etl),
    }


def defect_proof(domain, warehouse):
    """Show that each legacy output actually differs from the proper monthly rollup."""
    processes = {item.key: item for item in domain.processes}
    groups = defaultdict(list)
    evidence = []
    for flaw in domain.defects:
        shadow = warehouse.tables[flaw.table]
        base = warehouse.facts[flaw.process]
        process = processes[flaw.process]
        reference = aggregate(base.rows, ("business_month", "site_id"), process.measures)
        baseline = row_digest(tuple(row.values()) for row in reference)
        observed = row_digest(shadow.values(row) for row in shadow.rows)
        if baseline == observed:
            raise ValueError(f"seeded defect has no observable effect: {shadow.name}")
        groups[observed].append(shadow.name)
        metric_keys = ["event_count", *(f"total_{m.key}" for m in process.measures)]
        evidence.append(
            {
                "table": shadow.name,
                "upstream": base.name,
                "fault": flaw.fault,
                "canonical_groups": len(reference),
                "shadow_groups": len(shadow.rows),
                "observed_hash": observed,
                "canonical_hash": baseline,
                "canonical_totals": {k: scalar(sum(r[k] for r in reference)) for k in metric_keys},
                "shadow_totals": {k: scalar(sum(r[k] for r in shadow.rows)) for k in metric_keys},
            }
        )
    return {
        "evidence": evidence,
        "identical_legacy_output_groups": [items for items in groups.values() if len(items) > 1],
    }


def roles(domain):
    return {
        "business_analyst": {
            "label": "经营分析师",
            "sites": list(domain.sites),
            "mode": "read_aggregate",
            "sensitive_export": False,
            "write": False,
        },
        "site_analyst": {
            "label": "网点分析师",
            "sites": [domain.sites[0]],
            "mode": "read_aggregate",
            "sensitive_export": False,
            "write": False,
        },
    }


def evaluation_rows(domain, journals, warehouse, cases):
    tasks, gold, sql = [], [], []
    keys, prompts = set(), set()
    for case in cases:
        if case.key in keys:
            raise ValueError(f"duplicate case ID: {case.key}")
        keys.add(case.key)
        case_key = f"{domain.key}/{case.key}"
        if case.family not in CONTRACT["minimum_tasks_by_family"] or case.role not in roles(domain):
            raise ValueError(f"invalid case classification: {case_key}")
        if case.family in {"multi_turn", "explore", "report"} and len(case.turns) < CONTRACT["scenario_turn_minimum"]:
            raise ValueError(f"missing meaningful follow-ups: {case_key}")
        tasks.append(
            {
                "id": case_key,
                "family": case.family,
                "objective": case.objective,
                "role": case.role,
                "tags": list(case.tags),
                "turns": [t.question for t in case.turns],
            }
        )
        for index, turn in enumerate(case.turns, 1):
            if not turn.question.strip() or turn.question in prompts:
                raise ValueError(f"empty or duplicate prompt: {case_key} turn {index}")
            prompts.add(turn.question)
            if not turn.checks or len({check.key for check in turn.checks}) != len(turn.checks):
                raise ValueError(f"missing or ambiguous check: {case_key} turn {index}")
            for check in turn.checks:
                binding = {"case": case_key, "turn": index, "check": check.key}
                expected = check.expected
                if check.query:
                    expected = from_journals(domain, journals, check.query)
                    if expected is None or expected == []:
                        raise ValueError(f"missing fixture support: {case_key} turn {index} check {check.key}")
                    sql.append({**binding, "plan": query_plan(check.query, domain, warehouse)})
                elif check.rule == "exact" and expected is None:
                    raise ValueError(f"missing answer: {case_key} turn {index} check {check.key}")
                gold.append(
                    {
                        **binding,
                        "rule": "numeric" if check.query else check.rule,
                        "expected": expected,
                        "guidance": check.guidance,
                        "ordered": isinstance(check.query, Query) and bool(check.query.top),
                    }
                )
    families = Counter(c.family for c in cases)
    for family, count in CONTRACT["minimum_tasks_by_family"].items():
        if families[family] < count:
            raise ValueError(f"{domain.key}: {family} has only {families[family]} tasks")
    if len(tasks) < CONTRACT["minimum_tasks"] or len(sql) < CONTRACT["minimum_oracle_checks"]:
        raise ValueError(f"{domain.key} has too few cases or SQL cross-checks")
    return tasks, gold, sql, dict(families)


def keyed(rows):
    return {(r["case"], r["turn"], r["check"]): r for r in rows}


def assert_reference_checks(gold, queries, execute):
    expected = keyed(gold)
    for row in queries:
        key = (row["case"], row["turn"], row["check"])
        result = from_sql(row["plan"], execute)
        correct = expected[key]["expected"]
        if result != correct:
            raise ValueError(f"reference SQL and business journal diverge for {key}: {result!r} != {correct!r}")
    return len(queries)


def canonical_table_rows(table, rows):
    """Normalize driver-specific scalar types using the physical schema."""
    result = []
    for row in rows:
        values = []
        for column, value in zip(table.columns, row, strict=True):
            if column.sql_type.startswith("DECIMAL"):
                values.append(decimal(value).quantize(Decimal("0.01")))
            elif column.sql_type == "BIGINT":
                values.append(int(value))
            else:
                values.append(scalar(value))
        result.append(tuple(values))
    return result


def assert_etl(warehouse, execute):
    for name, query in warehouse.etl.items():
        table = warehouse.tables[name]
        actual = canonical_table_rows(table, execute(query))
        if row_digest(actual) != row_digest(table.values(row) for row in table.rows):
            raise ValueError(f"published ETL does not match table contents: {name}")
    return len(warehouse.etl)


def sqlite_cross_check(warehouse, gold, queries):
    """Cross-check the warehouse projection with a separate relational engine locally."""
    with closing(sqlite3.connect(":memory:")) as db:
        for name, table in warehouse.tables.items():
            fields = ", ".join(f"{ident(c.name)} {c.sql_type}" for c in table.columns)
            db.execute(f"CREATE TABLE {ident(name)} ({fields})")
            slots = ", ".join("?" for _ in table.columns)
            db.executemany(
                f"INSERT INTO {ident(name)} VALUES ({slots})",
                (tuple(scalar(v) for v in table.values(r)) for r in table.rows),
            )

        def execute(statement):
            return db.execute(statement).fetchall()

        count = assert_reference_checks(gold, queries, execute)
        jobs = assert_etl(warehouse, execute)
    return {"sqlite_reference_checks": count, "sqlite_etl_cross_checks": jobs}


def assemble(key, seed):
    domain, journals, warehouse, cases = prepare(key, seed)
    structure = check_structure(warehouse)
    defects = defect_proof(domain, warehouse)
    tasks, gold, sql, families = evaluation_rows(domain, journals, warehouse, cases)
    status = {
        "dataset": key,
        "database": domain.database,
        "company": domain.company,
        "seed": seed,
        "quality_rank": domain.quality,
        **structure,
        "tasks": len(tasks),
        "turns": sum(len(t["turns"]) for t in tasks),
        "coverage": families,
        "gold_checks": len(gold),
        "reference_checks": len(sql),
        "business_documents": len(domain.documents),
        "known_issue_types": sorted({fault.fault for fault in domain.defects}),
        "legacy_marts": len(domain.defects),
        "duplicate_legacy_groups": len(defects["identical_legacy_output_groups"]),
        **sqlite_cross_check(warehouse, gold, sql),
    }
    return domain, warehouse, tasks, gold, sql, status


def export(key, seed, output):
    if output.exists():
        raise FileExistsError(f"use a new output directory: {output}")
    domain, warehouse, tasks, gold, sql, status = assemble(key, seed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".lumen-bench-", dir=output.parent) as staging:
        root = Path(staging) / "release"
        candidate = root / "candidate"
        evaluator = root / "evaluator"
        for directory in (candidate / "load", candidate / "docs", candidate / "etl", evaluator):
            directory.mkdir(parents=True)
        schema = []
        table_index = {}
        for name, table in warehouse.tables.items():
            schema.append(table.ddl())
            (candidate / "load" / f"{name}.sql").write_text(table.insert(), encoding="utf-8")
            table_index[name] = {
                "rows": len(table.rows),
                "columns": [c.name for c in table.columns],
                "rows_sha256": row_digest(table.values(row) for row in table.rows),
            }
        (candidate / "schema.sql").write_text("\n".join(schema), encoding="utf-8")
        (candidate / "tasks.jsonl").write_text(jsonl(tasks), encoding="utf-8")
        (candidate / "roles.json").write_text(dump_json(roles(domain)), encoding="utf-8")
        for filename, contents in domain.documents.items():
            (candidate / "docs" / filename).write_text(contents, encoding="utf-8")
        for name, contents in warehouse.etl.items():
            (candidate / "etl" / f"{name}.sql").write_text(contents, encoding="utf-8")
        (candidate / "readme.md").write_text(
            f"# {domain.company}\n\n{domain.overview}\n\n"
            "评测日期为 2026 年 3 月 1 日。经营日历和金额单位以各系统的资料为准。\n",
            encoding="utf-8",
        )
        candidate_digest = sha256()
        for item in sorted(candidate.rglob("*")):
            if item.is_file():
                candidate_digest.update(str(item.relative_to(candidate)).encode() + b"\0")
                candidate_digest.update(sha256(item.read_bytes()).digest())
        status["candidate_sha256"] = candidate_digest.hexdigest()
        (evaluator / "gold.jsonl").write_text(jsonl(gold), encoding="utf-8")
        (evaluator / "reference-sql.jsonl").write_text(jsonl(sql), encoding="utf-8")
        (evaluator / "physical-rows.json").write_text(dump_json(table_index), encoding="utf-8")
        (evaluator / "model-truth.json").write_text(
            dump_json(
                {
                    "dataset": key,
                    "entities": {k: t.name for k, t in warehouse.dimensions.items()},
                    "canon": {k: t.name for k, t in warehouse.facts.items()},
                    "tables": {
                        name: {
                            "kind": table.kind,
                            "semantic_columns": {c.key: c.name for c in table.columns},
                            "upstream": list(table.upstream),
                            "relationships": [vars(ref) for ref in table.refs],
                        }
                        for name, table in warehouse.tables.items()
                    },
                    "intentional_data_defects": [vars(flaw) for flaw in domain.defects],
                }
            ),
            encoding="utf-8",
        )
        (evaluator / "defect-proof.json").write_text(dump_json(defect_proof(domain, warehouse)), encoding="utf-8")
        for secret in evaluator.iterdir():
            secret.chmod(0o600)
        evaluator.chmod(0o700)
        (root / "manifest.json").write_text(dump_json(status), encoding="utf-8")
        root.rename(output)
    return status
