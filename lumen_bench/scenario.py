"""Create independent case folders for real Lumen client runs."""

from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from .grading import load_lines
from .model import CONTRACT
from .suite import candidate_fingerprint, dump_json

CASE_SUFFIX = re.compile(r"[A-Z0-9-]{2,32}\Z")
SCENARIO_FAMILIES = {"multi_turn", "explore", "report"}


def validated_cases(bundle):
    candidate = bundle / "candidate"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    domain = manifest["dataset"]
    if domain not in CONTRACT["databases"] or manifest["database"] != CONTRACT["databases"][domain]["name"]:
        raise ValueError("candidate identifies an unknown benchmark source")
    if manifest["candidate_sha256"] != candidate_fingerprint(candidate):
        raise ValueError("candidate files differ from their released digest")
    tasks = load_lines(candidate / "tasks.jsonl")
    roles = json.loads((candidate / "roles.json").read_text(encoding="utf-8"))
    identifiers = set()
    for task in tasks:
        topic, _, name = task["id"].partition("/")
        if topic != domain or not CASE_SUFFIX.fullmatch(name) or task["id"] in identifiers:
            raise ValueError("invalid case ID in candidate")
        if task["role"] not in roles or not task["turns"]:
            raise ValueError(f"case has no declared role or conversation: {task['id']}")
        identifiers.add(task["id"])
    if len(tasks) != manifest["tasks"]:
        raise ValueError("candidate tasks do not match the manifest")
    return manifest, roles, tasks


def plan_run(bundle: Path, output: Path, selected=()):
    manifest, roles, tasks = validated_cases(bundle)
    known = {task["id"] for task in tasks}
    missing = set(selected) - known
    if missing:
        raise ValueError(f"unknown scenarios: {sorted(missing)}")
    report = {
        "contract_version": CONTRACT["version"],
        "run": uuid4().hex,
        "dataset": manifest["dataset"],
        "database": manifest["database"],
        "candidate_sha256": manifest["candidate_sha256"],
        "cases": [],
    }
    output.mkdir(parents=True, exist_ok=False)
    for task in tasks:
        if selected and task["id"] not in selected:
            continue
        case_name = task["id"].split("/")[1]
        root = output / case_name
        capture = root / "capture"
        capture.mkdir(parents=True)
        (root / "role-policy.json").write_text(dump_json(roles[task["role"]]), encoding="utf-8")
        graph = []
        prompts = []
        for i, question in enumerate(task["turns"], 1):
            prompt_path = f"turn-{i:02d}.txt"
            (root / prompt_path).write_text(question + "\n", encoding="utf-8")
            prompts.append(prompt_path)
            step = f"turn-{i:02d}"
            parents = [f"turn-{i - 1:02d}"] if i > 1 else []
            graph.append(
                {"step": step, "actor": "performer", "depends_on": parents, "capture": f"capture/{step}.json"}
            )
        if task["family"] in SCENARIO_FAMILIES:
            graph.extend(
                [
                    {
                        "step": "review",
                        "actor": "independent_reviewer",
                        "depends_on": [graph[-1]["step"]],
                        "capture": "capture/review.json",
                    },
                    {
                        "step": "delivery",
                        "actor": "performer",
                        "depends_on": ["review"],
                        "capture": "capture/delivery.json",
                    },
                ]
            )
        plan = {
            "id": task["id"],
            "family": task["family"],
            "objective": task["objective"],
            "namespace": f"{report['run']}/{task['id']}",
            "fixture": {
                "candidate_sha256": report["candidate_sha256"],
                "case_sha256": sha256(dump_json(task).encode()).hexdigest(),
                "database": report["database"],
                "role": task["role"],
                "role_policy": "role-policy.json",
            },
            "inputs": prompts,
            "workflow": graph,
            "status": "planned",
        }
        (root / "case-plan.json").write_text(dump_json(plan), encoding="utf-8")
        report["cases"].append(
            {"id": task["id"], "plan": f"{case_name}/case-plan.json", "turns": len(prompts), "status": "planned"}
        )
    (output / "run-index.json").write_text(dump_json(report), encoding="utf-8")
    return report
