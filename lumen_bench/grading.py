"""Grade independently captured structured observations against evaluator-only gold."""
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json

from .oracle import display_number

VALID_RULES = {"review", "exact", "numeric"}


def load_lines(path):
    return [json.loads(row) for row in Path(path).read_text(encoding="utf-8").splitlines() if row.strip()]


def key_of(row):
    return (row["case"], row["turn"], row["check"])


def normalize_numeric(value):
    if isinstance(value, list):
        return [{"key": [str(k) for k in row["key"]], "value": normalize_numeric(row["value"])}
                for row in value]
    return display_number(Decimal(str(value)))


def grade(gold, actual):
    if not gold:
        raise ValueError("the benchmark gold file is empty")
    gold_keys = [key_of(row) for row in gold]
    if len(set(gold_keys)) != len(gold_keys) or any(row["rule"] not in VALID_RULES for row in gold):
        raise ValueError("invalid or duplicate golden check")
    supplied = {}
    for row in actual:
        key = key_of(row)
        if key in supplied:
            raise ValueError(f"duplicate observation: {key}")
        supplied[key] = row
    result = []
    for item in gold:
        key = key_of(item)
        observation = supplied.pop(key, None)
        if observation is None:
            state = "missing"
        elif item["rule"] == "review":
            performer = observation.get("performer")
            reviewer = observation.get("reviewer")
            evidence = observation.get("evidence")
            outcome = observation.get("outcome")
            if (not performer or not reviewer or performer == reviewer or not evidence
                    or outcome not in {"pass", "fail"}):
                state = "review_pending"
            else:
                state = outcome
        elif item["rule"] == "exact":
            state = "pass" if "value" in observation and observation["value"] == item["expected"] else "fail"
        else:
            try:
                answer = normalize_numeric(observation["value"])
                expected = item["expected"]
                if isinstance(answer, list) and isinstance(expected, list):
                    if not item.get("ordered", False):
                        answer = sorted(answer, key=lambda row: row["key"])
                        expected = sorted(expected, key=lambda row: row["key"])
                state = "pass" if answer == expected else "fail"
            except (TypeError, ValueError, InvalidOperation, KeyError):
                state = "fail"
        result.append({"case": key[0], "turn": key[1], "check": key[2], "status": state})
    if supplied:
        raise ValueError(f"observations do not belong to this benchmark: {sorted(supplied)[:4]}")
    return result
