"""Generate the original business journals before compiling any warehouse tables."""

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from random import Random

from .model import Domain, entity

START = date(2025, 1, 1)
END = date(2026, 2, 28)
REGIONS = ("华东", "华南", "华北", "西南")


@dataclass
class Journals:
    dimensions: dict[str, list[dict]]
    facts: dict[str, list[dict]]


def entities(domain: Domain):
    return (
        entity("region", "经营区域", "|".join(REGIONS), "沿海|内陆", legacy="qy"),
        entity("site", "经营网点", "|".join(domain.sites), "自营|联合运营", "region", legacy="wd", history=True),
        *domain.entities,
    )


def rng_for(seed, domain, key):
    digest = sha256(f"{seed}/{domain}/{key}".encode()).digest()
    return Random(int.from_bytes(digest[:8], "big"))


def generate(domain: Domain, seed: int) -> Journals:
    dimensions = {}
    for spec in entities(domain):
        if spec.key in dimensions:
            raise ValueError(f"duplicate dimension: {spec.key}")
        dimensions[spec.key] = [
            {
                "id": index + 1,
                "code": f"{spec.key.upper()}-{index + 1:03}",
                "name": label,
                "class": spec.groups[index % len(spec.groups)],
                **{f"{parent}_id": index % len(dimensions[parent]) + 1 for parent in spec.parents},
            }
            for index, label in enumerate(spec.labels)
        ]

    facts = {}
    for spec in domain.processes:
        panel_rows = dimensions[spec.panel] if spec.panel else ()
        row_count = 12 * len(panel_rows) if panel_rows else spec.rows
        if spec.key in facts or row_count < 48 or (panel_rows and spec.after):
            raise ValueError(f"invalid process: {spec.key}")
        rng = rng_for(seed, domain.key, spec.key)
        parents = facts[spec.after] if spec.after else []
        if spec.parent_states:
            parents = [row for row in parents if row["state"] in spec.parent_states]
        if spec.after and not parents:
            raise ValueError(f"no eligible parent events for {spec.key}")
        selected = (
            (rng.sample(parents, row_count) if row_count <= len(parents) else rng.choices(parents, k=row_count))
            if parents
            else [None] * row_count
        )
        lines = []
        for index, parent in enumerate(selected, 1):
            if parent:
                day = date.fromisoformat(parent["business_day"]) + timedelta(days=rng.randint(1, 24))
                day = min(day, END)
                site = parent["site_id"]
            elif panel_rows:
                month = 1 + (index - 1) // len(panel_rows)
                day = date(2025, month, monthrange(2025, month)[1])
                subject = panel_rows[(index - 1) % len(panel_rows)]
                site = subject.get("site_id", 1 + (subject["id"] - 1) % len(dimensions["site"]))
            else:
                first_of_month = date(2025, 1 + (index - 1) % 12, 1)
                day = first_of_month + timedelta(days=rng.randrange(28))
                site = rng.choice(dimensions["site"])["id"]
            row = {
                "record_id": index,
                "business_day": day.isoformat(),
                "business_month": day.replace(day=1).isoformat(),
                "site_id": site,
                "state": rng.choice(spec.states + (spec.states[0],) * 5),
                "source_system": spec.source,
            }
            for dim in spec.dimensions:
                field = f"{dim}_id"
                if dim == spec.panel:
                    row[field] = subject["id"]
                elif parent and field in parent:
                    row[field] = parent[field]
                else:
                    row[field] = rng.choice(dimensions[dim])["id"]
            if parent:
                row["parent_record_id"] = parent["record_id"]
            for measure in spec.measures:
                if measure.minus:
                    left, right = measure.minus
                    row[measure.key] = row[left] - row[right]
                elif measure.parent_measure:
                    if not parent or measure.parent_measure not in parent:
                        raise ValueError(f"invalid linked measure in {spec.key}: {measure.key}")
                    part = Decimal(parent[measure.parent_measure]) * Decimal(rng.randint(15, 70)) / 100
                    row[measure.key] = part.quantize(Decimal("0.01"))
                else:
                    value = rng.randint(measure.lo, measure.hi)
                    if measure.decimals > 0:
                        value = Decimal(value + (day.month - 1) * (measure.hi // 79))
                        value = value.scaleb(-measure.decimals)
                    row[measure.key] = value
            if spec.enrich:
                spec.enrich(row, parent, rng)
            lines.append(row)
        facts[spec.key] = lines
    return Journals(dimensions, facts)


def aggregate(rows, keys, measures):
    buckets = defaultdict(list)
    for row in rows:
        buckets[tuple(row[key] for key in keys)].append(row)
    return [
        {
            **dict(zip(keys, key, strict=True)),
            "event_count": len(group),
            **{f"total_{m.key}": sum(r[m.key] for r in group) for m in measures},
        }
        for key, group in sorted(buckets.items())
    ]
