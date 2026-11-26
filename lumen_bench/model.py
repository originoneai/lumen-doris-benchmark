"""Domain vocabulary shared by the fixture compiler and the reference evaluator."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import json

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "v1.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
ALLOWED_DATABASES = frozenset(x["name"] for x in CONTRACT["databases"].values())


@dataclass(frozen=True)
class Entity:
    key: str
    title: str
    labels: tuple[str, ...]
    groups: tuple[str, ...]
    parents: tuple[str, ...] = ()
    legacy: str = ""
    history: bool = False


@dataclass(frozen=True)
class Measure:
    key: str
    title: str
    lo: int
    hi: int
    decimals: int = 0
    legacy: str = ""
    minus: tuple[str, str] | None = None
    parent_measure: str | None = None


@dataclass(frozen=True)
class Process:
    key: str
    title: str
    dimensions: tuple[str, ...]
    measures: tuple[Measure, ...]
    states: tuple[str, ...]
    source: str
    rows: int = 180
    after: str | None = None
    legacy: str = ""
    sums_scope: str = "site"
    note: str = ""
    parent_states: tuple[str, ...] = ()
    panel: str = ""
    enrich: Callable | None = None


@dataclass(frozen=True)
class Defect:
    table: str
    process: str
    fault: str
    owner: str
    note: str


@dataclass(frozen=True)
class Domain:
    key: str
    company: str
    overview: str
    sites: tuple[str, ...]
    entities: tuple[Entity, ...]
    processes: tuple[Process, ...]
    defects: tuple[Defect, ...]
    documents: dict[str, str]
    risk_subject: str

    @property
    def database(self):
        return CONTRACT["databases"][self.key]["name"]

    @property
    def quality(self):
        return CONTRACT["databases"][self.key]["quality_rank"]


def entity(key, title, labels, groups, parents="", *, legacy="", history=False):
    return Entity(
        key, title, tuple(labels.split("|")), tuple(groups.split("|")), tuple(parents.split()), legacy, history
    )


def process(
    key,
    title,
    refs,
    measures,
    states,
    source,
    *,
    rows=180,
    after=None,
    legacy="",
    scope="site",
    note="",
    parent_states="",
    panel="",
    enrich=None,
):
    return Process(
        key,
        title,
        tuple(refs.split()),
        tuple(measures),
        tuple(states.split("|")),
        source,
        rows,
        after,
        legacy,
        scope,
        note,
        tuple(parent_states.split("|")) if parent_states else (),
        panel,
        enrich,
    )


def count(key, title, lo=1, hi=80, *, legacy="sl"):
    return Measure(key, title, lo, hi, legacy=legacy)


def amount(key, title, lo=1000, hi=85000, *, legacy="je", parent=None):
    return Measure(key, title, lo, hi, decimals=2, legacy=legacy, parent_measure=parent)


def difference(key, title, left, right, *, legacy="jje"):
    return Measure(key, title, 0, 0, decimals=2, legacy=legacy, minus=(left, right))
