"""Independent reference calculations and SQL cross-checks for matched observations."""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import operator

from .warehouse import ident, literal

CONDITIONS = {
    "=": operator.eq, "!=": operator.ne, ">=": operator.ge, "<": operator.lt,
    ">": operator.gt, "<=": operator.le, "in": lambda value, choices: value in choices,
}


@dataclass(frozen=True)
class Query:
    process: str
    op: str = "count"
    field: str = ""
    where: tuple[tuple, ...] = ()
    group: tuple[str, ...] = ()
    top: int = 0


@dataclass(frozen=True)
class Arithmetic:
    left: Query
    right: Query
    kind: str = "difference"


@dataclass(frozen=True)
class Check:
    key: str
    expected: object = None
    rule: str = "exact"
    query: Query | Arithmetic | None = None
    guidance: str = ""


@dataclass(frozen=True)
class Turn:
    question: str
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class Case:
    key: str
    family: str
    objective: str
    turns: tuple[Turn, ...]
    role: str = "business_analyst"
    tags: tuple[str, ...] = field(default_factory=tuple)


def observe(key, query, guidance=""):
    return Check(key, query=query, guidance=guidance)


def expect(key, value, guidance=""):
    return Check(key, expected=value, guidance=guidance)


def review(key, criteria):
    return Check(key, rule="review", guidance=criteria)


def turn(question, *checks):
    return Turn(question, checks)


def case(key, family, objective, *turns, role="business_analyst", tags=()):
    return Case(key, family, objective, tuple(turns), role, tags)


def value_of(domain, journals, row, process, field):
    prefix, dot, tail = field.partition(".")
    if not dot:
        return row[prefix]
    if prefix == "parent":
        parent_key = next(p.after for p in domain.processes if p.key == process)
        parent = journals.facts[parent_key][row["parent_record_id"] - 1]
        return value_of(domain, journals, parent, parent_key, tail)
    dim = journals.dimensions[prefix][row[f"{prefix}_id"] - 1]
    return value_of(domain, journals, dim, process, tail)


def decimal(value):
    return Decimal(str(value))


def display_number(value):
    if value is None:
        return None
    n = decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(n.normalize(), "f") if n else "0"


def reduce_values(query, values):
    if query.op == "count":
        return len(values)
    if query.op == "distinct":
        return len(set(values))
    if query.op == "sum":
        return sum(values)
    if query.op == "avg":
        return sum(map(decimal, values)) / len(values) if values else None
    if query.op == "max":
        return max(values) if values else None
    if query.op == "min":
        return min(values) if values else None
    raise ValueError(f"unknown aggregate: {query.op}")


def from_journals(domain, journals, query):
    if isinstance(query, Arithmetic):
        left = from_journals(domain, journals, query.left)
        right = from_journals(domain, journals, query.right)
        return combine(query.kind, left, right)
    groups = defaultdict(list)
    for row in journals.facts[query.process]:
        def get(key):
            return value_of(domain, journals, row, query.process, key)
        if all(CONDITIONS[op](get(key), target) for key, op, target in query.where):
            group = tuple(str(get(field)) for field in query.group)
            groups[group].append(get(query.field) if query.field else 1)
    if not query.group:
        return display_number(reduce_values(query, groups[()]))
    results = [(g, reduce_values(query, values)) for g, values in groups.items()]
    if query.top:
        results = sorted(results, key=lambda pair: (-pair[1], pair[0]))[:query.top]
    else:
        results.sort()
    return [{"key": list(group), "value": display_number(value)} for group, value in results]


def combine(kind, left, right):
    if left is None or right is None:
        return None
    a, b = decimal(left), decimal(right)
    if kind == "difference":
        return display_number(a - b)
    if kind == "ratio_pct":
        return display_number(100 * a / b) if b else None
    if kind == "growth_pct":
        return display_number(100 * (a - b) / b) if b else None
    raise ValueError(f"unknown arithmetic: {kind}")


def operand_sql(query, domain, warehouse):
    table = warehouse.facts[query.process]
    joins = {}

    def reference(field, base=table, alias="f", process=query.process):
        prefix, dot, tail = field.partition(".")
        if not dot:
            return f"{alias}.{ident(base.physical(prefix))}"
        if (alias, prefix) not in joins:
            join_alias = f"j{len(joins) + 1}"
            if prefix == "parent":
                target_process = next(p.after for p in domain.processes if p.key == process)
                target = warehouse.facts[target_process]
                fk, pk = "parent_record_id", "record_id"
            else:
                target_process = process
                target = warehouse.dimensions[prefix]
                fk, pk = f"{prefix}_id", "id"
            clause = (f"JOIN {ident(target.name)} {join_alias} "
                      f"ON {alias}.{ident(base.physical(fk))} = {join_alias}.{ident(target.physical(pk))}")
            joins[alias, prefix] = target, join_alias, target_process, clause
        target, join_alias, target_process, _ = joins[alias, prefix]
        return reference(tail, target, join_alias, target_process)

    groups = [reference(key) for key in query.group]
    value = reference(query.field) if query.field else "*"
    if query.op == "distinct":
        agg = f"COUNT(DISTINCT {value})"
    elif query.op in {"sum", "max", "min", "avg", "count"}:
        agg = f"{query.op.upper()}({value})"
        if query.op == "sum":
            agg = f"COALESCE({agg}, 0)"
    else:
        raise ValueError(f"invalid SQL aggregate {query.op}")
    filters = []
    for key, op, target in query.where:
        if op not in CONDITIONS:
            raise ValueError(f"invalid predicate {op}")
        if op == "in":
            if not target:
                raise ValueError("IN requires values")
            right = "(" + ", ".join(literal(x) for x in target) + ")"
            op = "IN"
        else:
            right = literal(target)
        filters.append(f"{reference(key)} {op} {right}")

    # Rank ties are resolved in Python with the same stable sort as the reference.
    # This avoids encoding a dependency on the server's UTF-8 collation.
    parts = [f"SELECT {', '.join([*groups, agg + ' AS result_value'])}",
             f"FROM {ident(table.name)} f",
             *(clause for _, _, _, clause in joins.values())]
    if filters:
        parts.append("WHERE " + " AND ".join(filters))
    if groups:
        parts.append("GROUP BY " + ", ".join(groups))
    return "\n".join(parts)


def query_plan(query, domain, warehouse):
    if isinstance(query, Arithmetic):
        if query.left.group or query.right.group:
            raise ValueError("arithmetic requires scalar operands")
        return {
            "kind": query.kind,
            "queries": [operand_sql(q, domain, warehouse) for q in (query.left, query.right)],
            "grouped": False, "top": 0,
        }
    return {
        "kind": "aggregate",
        "queries": [operand_sql(query, domain, warehouse)],
        "grouped": bool(query.group), "top": query.top,
    }


def from_sql(plan, execute):
    a = execute(plan["queries"][0])
    if plan["kind"] != "aggregate":
        b = execute(plan["queries"][1])
        return combine(plan["kind"], a[0][0], b[0][0])
    if not plan["grouped"]:
        return display_number(a[0][0])
    groups = [(tuple(str(v) for v in row[:-1]), row[-1]) for row in a]
    if plan["top"]:
        groups = sorted(groups, key=lambda pair: (-pair[1], pair[0]))[:plan["top"]]
    else:
        groups.sort()
    return [{"key": list(group), "value": display_number(value)} for group, value in groups]
