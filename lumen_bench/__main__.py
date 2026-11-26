"""Run `python -m lumen_bench --help` from the project root."""

from argparse import ArgumentParser
from collections import Counter
from pathlib import Path

from .grading import grade, load_lines, summarize
from .model import CONTRACT
from .suite import assemble, dump_json, export


def main():
    parser = ArgumentParser(description="Generate and validate synthetic enterprise benchmarks")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "build"):
        sub = commands.add_parser(name)
        sub.add_argument("dataset", choices=CONTRACT["databases"])
        sub.add_argument("--seed", type=int, default=CONTRACT["default_seed"])
        if name == "build":
            sub.add_argument("--output", required=True, type=Path)
    for name in ("deploy-doris", "verify-doris"):
        sub = commands.add_parser(name)
        sub.add_argument("dataset", choices=CONTRACT["databases"])
        sub.add_argument("--seed", type=int, default=CONTRACT["default_seed"])
        sub.add_argument("--connection", required=True, type=Path)
        sub.add_argument("--cluster-id", required=True)
        sub.add_argument("--ssh-host")
        sub.add_argument("--workers", type=int, choices=(1, 2, 3), default=2)
        sub.add_argument("--receipt", required=True, type=Path)
    run = commands.add_parser("plan-run")
    run.add_argument("--bundle", required=True, type=Path)
    run.add_argument("--scenario", action="append", default=[])
    run.add_argument("--output", required=True, type=Path)
    scoring = commands.add_parser("grade")
    scoring.add_argument("--gold", required=True, type=Path)
    scoring.add_argument("--observations", required=True, type=Path)
    scoring.add_argument("--output", required=True, type=Path)
    scoring.add_argument("--tasks", type=Path)
    scoring.add_argument("--scenario", action="append", default=[])
    args = parser.parse_args()
    if args.command == "validate":
        result = assemble(args.dataset, args.seed)[-1]
    elif args.command == "build":
        result = export(args.dataset, args.seed, args.output)
    elif args.command in {"deploy-doris", "verify-doris"}:
        from .doris import publish

        result = publish(
            args.dataset,
            args.seed,
            args.connection,
            args.cluster_id,
            args.command.split("-")[0],
            args.ssh_host,
            args.workers,
            args.receipt,
        )
    elif args.command == "plan-run":
        from .scenario import plan_run

        result = plan_run(args.bundle, args.output, args.scenario)
    else:
        gold = load_lines(args.gold)
        tasks = load_lines(args.tasks) if args.tasks else None
        if args.scenario:
            selected = set(args.scenario)
            if selected - {row["case"] for row in gold}:
                parser.error("a selected scenario does not exist in this gold file")
            gold = [row for row in gold if row["case"] in selected]
            if tasks is not None:
                tasks = [row for row in tasks if row["id"] in selected]
        results = grade(gold, load_lines(args.observations))
        result = {"checks": dict(Counter(row["status"] for row in results)), "results": results}
        if tasks is not None:
            result["tasks"] = summarize(tasks, results)
        args.output.write_text(dump_json(result), encoding="utf-8")
        print(dump_json(result["checks"]), end="")
        return 0 if all(row["status"] == "pass" for row in results) else 1
    print(dump_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
