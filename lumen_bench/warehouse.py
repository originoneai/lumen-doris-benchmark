"""Compile Doris tables, lineage, and deliberate quality defects from journals."""
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
import re

from .fixtures import START, END, Journals, aggregate, entities
from .model import Domain, Measure

SAFE_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
KNOWN_FAULTS = ("replay", "wrong_day", "state_filter", "scope_loss", "unit_scale", "fanout",
                "stale_snapshot", "test_copy")


def ident(name):
    if not SAFE_IDENTIFIER.fullmatch(name):
        raise ValueError(f"invalid identifier: {name!r}")
    return f"`{name}`"


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, Decimal)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class Column:
    key: str
    name: str
    sql_type: str
    description: str


@dataclass(frozen=True)
class Reference:
    column: str
    table: str
    key: str


@dataclass
class Table:
    name: str
    kind: str
    columns: tuple[Column, ...]
    keys: tuple[str, ...]
    rows: list[dict]
    refs: tuple[Reference, ...]
    note: str
    upstream: tuple[str, ...] = ()

    def physical(self, key):
        return next(c.name for c in self.columns if c.key == key)

    def values(self, row):
        return tuple(row[c.key] for c in self.columns)

    def ddl(self):
        lines = [
            f"  {ident(c.name)} {c.sql_type} NOT NULL"
            + (f" COMMENT {literal(c.description)}" if c.description else "")
            for c in self.columns
        ]
        keys = ", ".join(ident(self.physical(k)) for k in self.keys)
        comment = f"COMMENT {literal(self.note)}\n" if self.note else ""
        joined = ",\n".join(lines)
        return (
            f"CREATE TABLE {ident(self.name)} (\n{joined}\n) ENGINE=OLAP\n"
            f"DUPLICATE KEY ({keys})\n{comment}"
            f"DISTRIBUTED BY HASH ({ident(self.physical(self.keys[0]))}) BUCKETS 1\n"
            'PROPERTIES ("replication_num" = "1");\n'
        )

    def insert(self, rows=None):
        rows = self.rows if rows is None else rows
        values = [f"({', '.join(literal(v) for v in self.values(row))})" for row in rows]
        if not values:
            raise ValueError(f"empty insert into {self.name}")
        names = ", ".join(ident(c.name) for c in self.columns)
        return f"INSERT INTO {ident(self.name)} ({names}) VALUES\n" + ",\n".join(values) + ";\n"


@dataclass
class Warehouse:
    tables: dict[str, Table]
    facts: dict[str, Table]
    dimensions: dict[str, Table]
    etl: dict[str, str]


FIELDS = {
    "id": ("BIGINT", "业务主键", "bh"),
    "code": ("VARCHAR(96)", "业务编码", "bm"),
    "name": ("VARCHAR(160)", "显示名称", "mc"),
    "class": ("VARCHAR(96)", "经营分类", "fl"),
    "business_day": ("DATE", "事件发生日", "rq"),
    "business_month": ("DATE", "事件发生月首日", "yf"),
    "version": ("BIGINT", "实体或源记录的版本号", "bb"),
    "valid_from": ("DATE", "生效日", "ksrq"),
    "valid_to": ("DATE", "失效日，右端不包含", "jsrq"),
    "record_id": ("BIGINT", "源事件业务标识", "dh"),
    "state": ("VARCHAR(96)", "业务状态", "zt"),
    "source_system": ("VARCHAR(96)", "源系统", "ly"),
    "parent_record_id": ("BIGINT", "关联上游事件", "ydh"),
    "ingest_id": ("BIGINT", "采集系统记录标识", "rbbh"),
    "captured_day": ("DATE", "采集时间按日折算", "cjrq"),
    "event_count": ("BIGINT", "事件记录数，含不同业务状态", "jls"),
    "batch_day": ("DATE", "批处理日期", "pcrq"),
}


def columns(domain, keys, measures=()):
    metrics: dict[str, Measure] = {m.key: m for m in measures}
    entity_aliases = {f"{item.key}_id": f"{item.legacy}bh"
                      for item in entities(domain) if item.legacy}
    result = []
    for key in keys:
        if key in metrics or key.startswith("total_"):
            metric = metrics[key.removeprefix("total_")]
            field_type = "DECIMAL(18,2)" if metric.decimals else "BIGINT"
            text = f"{metric.title}{'合计' if key.startswith('total_') else ''}"
            short_name = ("hj" if key.startswith("total_") else "") + (metric.legacy or "sz")
        elif key in FIELDS:
            field_type, text, short_name = FIELDS[key]
        elif key.endswith("_id"):
            field_type, text, short_name = "BIGINT", "关联维度的业务标识", entity_aliases.get(key, key)
        else:
            raise ValueError(f"no column contract for {key}")
        name = short_name if domain.quality == 1 else key
        while name in [c.name for c in result]:
            name += "2"
        result.append(Column(key, name, field_type, text if domain.quality >= 4 else ""))
    return tuple(result)


def names(domain, process):
    if domain.quality == 1:
        if not process.legacy:
            raise ValueError(f"missing old system table name for {process.key}")
        return tuple(f"{prefix}_{process.legacy}" for prefix in ("ls", "f", "hz", "yf"))
    if domain.quality == 2:
        source = process.source
        return (f"src_{source}_{process.key}", f"{source}_f_{process.key}",
                f"{source}_dm_{process.key}_day", f"{source}_rpt_{process.key}_month")
    if domain.quality == 3:
        return (f"stg_{process.source}_{process.key}", f"dwd_{process.key}",
                f"mart_{process.source}_{process.key}_day", f"rpt_{process.key}_month")
    return (f"ods_{process.key}_cdc", f"dwd_{process.key}",
            f"dws_{process.key}_day_site", f"ads_{process.key}_month_{process.sums_scope}")


def raw_versions(rows, process, quality):
    result = []
    replay_every = {5: 11, 4: 9, 3: 7, 2: 5, 1: 3}[quality]
    for index, row in enumerate(rows, 1):
        if index % replay_every == 0:
            prior = dict(row)
            if quality >= 4:
                prior["state"] = process.states[-1]
            result.append(dict(ingest_id=len(result) + 1, version=1,
                               captured_day=row["business_day"], **prior))
        version = 2 if index % replay_every == 0 else 1
        result.append(dict(ingest_id=len(result) + 1, version=version,
                           captured_day=row["business_day"], **row))
    return result


def projection(table, keys, alias="f"):
    return ", ".join(f"{alias}.{ident(table.physical(k))}" for k in keys)


def aggregate_sql(table, keys, measures, *, where="", from_extra=""):
    group = projection(table, keys)
    metrics = ", ".join(f"SUM(f.{ident(table.physical(m.key))}) AS {ident('total_' + m.key)}"
                        for m in measures)
    return (f"SELECT {group}, COUNT(*) AS event_count, {metrics}\n"
            f"FROM {ident(table.name)} f{from_extra}{where}\nGROUP BY {group};\n")


def build(domain: Domain, journals: Journals) -> Warehouse:
    tables, facts, dims, etl = {}, {}, {}, {}
    full_metadata = domain.quality >= 4

    def register(name, kind, rows, keys, refs=(), note="", measures=(), upstream=()):
        if name in tables or not rows:
            raise ValueError(f"duplicate or empty table: {name}")
        table = Table(name, kind, columns(domain, tuple(rows[0]), measures),
                      tuple(keys), rows, tuple(refs), note if full_metadata else "", tuple(upstream))
        tables[name] = table
        return table

    days = [(START + timedelta(days=i)).isoformat() for i in range((END - START).days + 1)]
    calendar = register("rq_k" if domain.quality == 1 else "dim_calendar", "dimension",
                        [{"business_day": day, "business_month": day[:8] + "01"} for day in days],
                        ("business_day",), note="经营日历，含次年结算缓冲期")
    for spec in entities(domain):
        table_name = f"{spec.legacy}_da" if domain.quality == 1 else f"dim_{spec.key}"
        refs = [Reference(f"{parent}_id", dims[parent].name, "id") for parent in spec.parents]
        current = register(table_name, "dimension", journals.dimensions[spec.key], ("id",),
                           refs, f"{spec.title}当前业务快照，每个业务主键一行")
        dims[spec.key] = current
        if spec.history:
            rows = []
            for row in current.rows:
                rows += [
                    dict(id=row["id"], version=1, valid_from="2024-01-01", valid_to="2025-07-01",
                         name=row["name"] + "（调整前）", **{"class": row["class"]}),
                    dict(id=row["id"], version=2, valid_from="2025-07-01", valid_to="2027-01-01",
                         name=row["name"], **{"class": row["class"]}),
                ]
            name = f"{spec.legacy}_ls" if domain.quality == 1 else f"dim_{spec.key}_history"
            register(name, "history", rows, ("id", "version"),
                     [Reference("id", current.name, "id")],
                     f"{spec.title}二型历史，按左闭右开时间关联", upstream=[current.name])

    for process in domain.processes:
        ods_name, fact_name, daily_name, monthly_name = names(domain, process)
        rows = journals.facts[process.key]
        measures = process.measures
        links = [Reference("business_day", calendar.name, "business_day")]
        links += [Reference(f"{dim}_id", dims[dim].name, "id")
                  for dim in ("site", *process.dimensions)]
        if process.after:
            links.append(Reference("parent_record_id", facts[process.after].name, "record_id"))
        ods = register(ods_name, "raw", raw_versions(rows, process, domain.quality), ("ingest_id",),
                       links, f"{process.title}源采集；含原记录及变更重放", measures)
        fact = register(fact_name, "fact", rows, ("record_id",), links,
                        f"{process.title}；一行一业务事件。{process.note}", measures, [ods.name])
        facts[process.key] = fact
        daily_keys = ("business_day", "site_id")
        monthly_keys = ("business_month", f"{process.sums_scope}_id", "state")
        register(daily_name, "daily", aggregate(rows, daily_keys, measures), daily_keys,
                 [Reference("business_day", calendar.name, "business_day"),
                  Reference("site_id", dims["site"].name, "id")],
                 f"{process.title}日粒度网点统计，含所有业务状态", measures, [fact.name])
        register(monthly_name, "monthly", aggregate(rows, monthly_keys, measures), monthly_keys,
                 [Reference("business_month", calendar.name, "business_day"),
                  Reference(f"{process.sums_scope}_id", dims[process.sums_scope].name, "id")],
                 f"{process.title}按月、{process.sums_scope}与业务状态汇总", measures, [fact.name])
        if domain.quality >= 4 or (domain.quality == 3 and process.source in {"ops", "billing"}):
            business_id = ident(ods.physical("record_id"))
            version = ident(ods.physical("version"))
            ingest = ident(ods.physical("ingest_id"))
            selected = ", ".join(ident(ods.physical(c.key)) for c in fact.columns)
            etl[fact.name] = (
                f"-- Extract one canonical revision per source event\n"
                f"SELECT {selected}\nFROM (\n"
                f"  SELECT *, ROW_NUMBER() OVER (PARTITION BY {business_id}"
                f" ORDER BY {version} DESC, {ingest} DESC) AS source_order\n"
                f"  FROM {ident(ods.name)}\n) latest\nWHERE source_order = 1;\n"
            )
            etl[daily_name] = aggregate_sql(fact, daily_keys, measures)
            etl[monthly_name] = aggregate_sql(fact, monthly_keys, measures)

    specs = {p.key: p for p in domain.processes}
    for flaw in domain.defects:
        spec = specs[flaw.process]
        table = facts[flaw.process]
        raw = tables[table.upstream[0]]
        rows, keys = table.rows, ("business_month", "site_id")
        if flaw.fault not in KNOWN_FAULTS:
            raise ValueError(f"unknown fault: {flaw.fault}")
        where, from_extra = "", ""
        source = table
        measures = spec.measures
        if flaw.fault == "replay":
            rows, source = raw.rows, raw
        elif flaw.fault == "wrong_day":
            keys = ("business_day", "site_id")
        elif flaw.fault == "state_filter":
            rows = [r for r in rows if r["state"] == spec.states[0]]
            where = f" WHERE f.{ident(table.physical('state'))} = {literal(spec.states[0])}"
        elif flaw.fault == "scope_loss":
            rows = [r for r in rows if r["site_id"] <= 3]
            where = f" WHERE f.{ident(table.physical('site_id'))} <= 3"
        elif flaw.fault == "unit_scale":
            first = measures[0]
            rows = [dict(r, **{first.key: r[first.key] * 100}) for r in rows]
        elif flaw.fault == "fanout":
            rows = [r for r in rows for _ in ("原件", "复核件")]
            from_extra = " CROSS JOIN (SELECT 1 AS copy_no UNION ALL SELECT 2) t"
        elif flaw.fault == "stale_snapshot":
            rows = [r for r in rows if r["business_day"] < "2025-10-01"]
            where = f" WHERE f.{ident(table.physical('business_day'))} < '2025-10-01'"
        elif flaw.fault == "test_copy":
            rows = [r for r in rows if r["record_id"] <= 40]
            where = f" WHERE f.{ident(table.physical('record_id'))} <= 40"
        shadow = register(flaw.table, "shadow", aggregate(rows, keys, measures), keys,
                          [Reference(keys[0], calendar.name, "business_day"),
                           Reference("site_id", dims["site"].name, "id")],
                          flaw.note, measures, [source.name])
        query = aggregate_sql(source, keys, measures, where=where, from_extra=from_extra)
        if flaw.fault == "unit_scale":
            metric = f"SUM(f.{ident(table.physical(measures[0].key))})"
            query = query.replace(metric, f"{metric} * 100", 1)
        etl[shadow.name] = f"-- {flaw.owner}\n{query}"
    return Warehouse(tables, facts, dims, etl)
