"""Provision and verify only the versioned fixture databases on a pinned cluster."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

from .model import ALLOWED_DATABASES, CONTRACT
from .suite import assemble, assert_etl, assert_reference_checks, canonical_table_rows, dump_json, row_digest
from .warehouse import ident, literal

MARKER = "bench_release_contract"
PREFLIGHT_FIELDS = {
    "SHOW FRONTENDS": ("Name", "Host", "QueryPort", "HttpPort", "Alive", "IsMaster", "ClusterId", "Version"),
    "SHOW BACKENDS": ("BackendId", "Host", "Alive", "HeartbeatPort", "Version", "LastStartTime", "Tag"),
}


def read_connection(path: Path):
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("connection JSON must be a regular private file with mode 0600")
    settings = json.loads(path.read_text(encoding="utf-8"))
    if set(settings) != {"host", "port", "user", "password"}:
        raise ValueError("connection JSON keys must be host, port, user, password")
    if not isinstance(settings["port"], int) or not 1 <= settings["port"] <= 65535:
        raise ValueError("invalid Doris query port")
    if not re.fullmatch(r"[\w.]+(?:-[\w.]+)*", settings["host"], re.ASCII):
        raise ValueError("Doris host must be a DNS name or IPv4 address")
    if not all(isinstance(settings[k], str) and settings[k] for k in ("user", "password")):
        raise ValueError("Doris user and password are required")
    return settings


@contextmanager
def connection_settings(path, ssh_host):
    settings = read_connection(path)
    yield_config = dict(settings)
    if not ssh_host:
        yield settings, yield_config
        return
    if not re.fullmatch(r"[\w.]+(?:-[\w.]+)*", ssh_host, re.ASCII):
        raise ValueError("invalid SSH host")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    forward = f"127.0.0.1:{port}:{settings['host']}:{settings['port']}"
    command = [
        "ssh",
        "-T",
        "-N",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=8",
        "-L",
        forward,
        ssh_host,
    ]
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as tunnel:
        try:
            for _ in range(60):
                if tunnel.poll() is not None:
                    raise RuntimeError("SSH tunnel exited before becoming usable")
                try:
                    with socket.create_connection(("127.0.0.1", port), 0.15):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("SSH tunnel did not become usable")
            yield settings, dict(yield_config, host="127.0.0.1", port=port)
        finally:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=4)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait()


def inspect_cluster(db, endpoint, cluster_id):
    topology = {}
    with db.cursor() as cursor:
        for command, fields in PREFLIGHT_FIELDS.items():
            cursor.execute(command)
            cols = [d[0] for d in cursor.description]
            topology[command] = sorted(
                [{k: row[cols.index(k)] for k in fields if k in cols} for row in cursor.fetchall()],
                key=lambda row: (str(row.get("Host", "")), str(row.get("BackendId", ""))),
            )
    frontends = topology["SHOW FRONTENDS"]
    backends = topology["SHOW BACKENDS"]
    frontend = next(
        (
            row
            for row in frontends
            if str(row.get("ClusterId")) == str(cluster_id)
            and row.get("Host") == endpoint["host"]
            and str(row.get("QueryPort")) == str(endpoint["port"])
        ),
        None,
    )
    if (
        not frontend
        or str(frontend.get("Alive", "")).lower() != "true"
        or {str(r.get("ClusterId")) for r in frontends} != {str(cluster_id)}
        or not backends
        or any(str(r.get("Alive", "")).lower() != "true" for r in backends)
        or sum(str(r.get("IsMaster", "")).lower() == "true" for r in frontends) != 1
    ):
        raise RuntimeError("authorized Doris FE/BE topology could not be verified")
    with db.cursor() as cursor:
        disks = {}
        for backend in backends:
            backend_id = str(backend.get("BackendId", ""))
            if not backend_id.isdecimal():
                raise RuntimeError("invalid Doris BE ID")
            cursor.execute(f"SHOW PROC '/backends/{backend_id}'")
            names = [field[0] for field in cursor.description]
            fields = ("RootPath", "State", "StorageMedium")
            rows = [{key: row[names.index(key)] for key in fields if key in names} for row in cursor.fetchall()]
            if not rows or any(not row.get("RootPath") for row in rows):
                raise RuntimeError("Doris backend storage paths could not be verified")
            disks[backend_id] = sorted(rows, key=lambda row: row["RootPath"])
        topology["backend_disks"] = disks
    return topology


def dataset_identity(warehouse):
    digest = sha256()
    table_rows = {}
    for table in warehouse.tables.values():
        rows_sha = row_digest(table.values(row) for row in table.rows)
        table_rows[table.name] = rows_sha
        digest.update(table.ddl().encode())
        digest.update(bytes.fromhex(rows_sha))
    return digest.hexdigest(), table_rows


def verify_database_name(name):
    if name not in ALLOWED_DATABASES:
        raise ValueError(f"database is outside the published fixture set: {name}")


def select_database(cursor, name):
    verify_database_name(name)
    cursor.execute(f"USE {ident(name)}")
    cursor.execute("SELECT DATABASE()")
    if cursor.fetchone() != (name,):
        raise RuntimeError("current Doris database differs from selected fixture")


def initialize(connect, domain, seed, fingerprint, mode, wanted_tables):
    name = domain.database
    verify_database_name(name)
    with connect() as db, db.cursor() as cursor:
        cursor.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s", (name,))
        existed = bool(cursor.fetchone())
        if not existed:
            if mode == "verify":
                raise RuntimeError(f"fixture database does not exist: {name}")
            cursor.execute(f"CREATE DATABASE {ident(name)}")
        select_database(cursor, name)
        cursor.execute("SHOW TABLES")
        existing = {item[0] for item in cursor.fetchall()}
        if not existed:
            if existing:
                raise RuntimeError("new benchmark database is not empty")
            cursor.execute(f"""CREATE TABLE {ident(MARKER)} (
                contract_version BIGINT NOT NULL,
                dataset VARCHAR(32) NOT NULL,
                seed BIGINT NOT NULL,
                checksum VARCHAR(64) NOT NULL
            ) ENGINE=OLAP DUPLICATE KEY (contract_version)
            DISTRIBUTED BY HASH (contract_version) BUCKETS 1
            PROPERTIES ("replication_num" = "1")
            """)
            cursor.execute(
                f"INSERT INTO {ident(MARKER)} VALUES (%s, %s, %s, %s)",
                (CONTRACT["version"], domain.key, seed, fingerprint),
            )
        elif MARKER not in existing or existing - (wanted_tables | {MARKER}):
            raise RuntimeError("existing database is outside the fixture ownership contract")
        cursor.execute(f"SELECT contract_version, dataset, seed, checksum FROM {ident(MARKER)}")
        if cursor.fetchall() != ((CONTRACT["version"], domain.key, seed, fingerprint),):
            raise RuntimeError("database contains a different fixture release")
        return existing - {MARKER}


def verify_table(cursor, table, expected_sha):
    cursor.execute(f"SHOW CREATE TABLE {ident(table.name)}")
    definition = cursor.fetchone()[1]
    key_list = ", ".join(ident(table.physical(k)) for k in table.keys)
    distribution = ident(table.physical(table.keys[0]))
    compact = re.sub(r"\s+", "", definition)
    if (
        "ENGINE=OLAP" not in compact
        or f"DUPLICATEKEY({key_list.replace(' ', '')})" not in compact
        or f"DISTRIBUTEDBYHASH({distribution})BUCKETS1" not in compact
        or 'replication_allocation"="tag.location.default:1"' not in compact
        or (table.note and f"COMMENT {literal(table.note)}" not in definition)
    ):
        raise RuntimeError(f"table storage definition mismatch for {table.name}")
    cursor.execute(f"SHOW FULL COLUMNS FROM {ident(table.name)}")
    keys = [d[0] for d in cursor.description]
    columns = [dict(zip(keys, row, strict=True)) for row in cursor.fetchall()]
    if len(columns) != len(table.columns):
        raise RuntimeError(f"column count mismatch for {table.name}")
    for found, expected in zip(columns, table.columns, strict=True):
        actual_type = str(found["Type"]).upper().replace(" ", "")
        allowed_types = {expected.sql_type}
        if expected.sql_type == "DATE":
            allowed_types.add("DATEV2")
        if expected.sql_type == "BIGINT":
            allowed_types.add("BIGINT(20)")
        if expected.sql_type.startswith("DECIMAL"):
            allowed_types.add(expected.sql_type.replace("DECIMAL", "DECIMALV3"))
        if (
            found["Field"] != expected.name
            or actual_type not in allowed_types
            or found["Null"] != "NO"
            or str(found["Comment"] or "") != expected.description
        ):
            raise RuntimeError(f"column definition mismatch for {table.name}.{expected.name}")
    columns = ", ".join(ident(column.name) for column in table.columns)
    cursor.execute(f"SELECT {columns} FROM {ident(table.name)}")
    rows = canonical_table_rows(table, cursor.fetchall())
    if row_digest(rows) != expected_sha:
        raise RuntimeError(f"row fingerprint mismatch for {table.name}")
    return len(rows)


def verify_catalog(connect, database, table_names):
    with connect() as db, db.cursor() as cursor:
        select_database(cursor, database)
        cursor.execute("SHOW TABLES")
        names = {item[0] for item in cursor.fetchall()}
        if names != table_names | {MARKER}:
            raise RuntimeError("Doris table inventory differs from the versioned fixture")


def publish(dataset, seed, path, cluster_id, mode, ssh_host, workers, receipt):
    import pymysql

    if receipt.exists():
        raise FileExistsError(f"receipt destination already exists: {receipt}")
    if workers not in (1, 2, 3):
        raise ValueError("a fixture load uses at most three Doris workers")
    domain, warehouse, _, gold, oracle, summary = assemble(dataset, seed)
    checksum, table_rows = dataset_identity(warehouse)
    started = datetime.now(timezone.utc)
    with connection_settings(path, ssh_host) as (endpoint, settings):

        def connect():
            return pymysql.connect(
                **settings, connect_timeout=6, read_timeout=100, write_timeout=60, autocommit=True, charset="utf8mb4"
            )

        with connect() as admin:
            baseline = inspect_cluster(admin, endpoint, cluster_id)
        try:
            existing = initialize(connect, domain, seed, checksum, mode, set(warehouse.tables))

            def run_batch(tables):
                with connect() as db, db.cursor() as cursor:
                    select_database(cursor, domain.database)
                    checks = {}
                    for table in tables:
                        if table.name not in existing:
                            if mode == "verify":
                                raise RuntimeError(f"table missing: {table.name}")
                            cursor.execute(table.ddl())
                            cursor.execute(table.insert())
                        elif mode == "deploy":
                            cursor.execute(f"SELECT COUNT(*) FROM {ident(table.name)}")
                            if cursor.fetchone()[0] == 0:
                                cursor.execute(table.insert())
                        checks[table.name] = verify_table(cursor, table, table_rows[table.name])
                    return checks

            tables = list(warehouse.tables.values())
            with ThreadPoolExecutor(max_workers=workers) as pool:
                checks = {}
                for result in pool.map(run_batch, [tables[i::workers] for i in range(workers)]):
                    checks.update(result)
            verify_catalog(connect, domain.database, set(checks))

            with connect() as db, db.cursor() as cursor:
                select_database(cursor, domain.database)

                def execute(statement):
                    cursor.execute(statement)
                    return cursor.fetchall()

                reference_count = assert_reference_checks(gold, oracle, execute)
                etl_count = assert_etl(warehouse, execute)
        finally:
            with connect() as admin:
                after = inspect_cluster(admin, endpoint, cluster_id)
            if after != baseline:
                raise RuntimeError("Doris FE/BE identity changed during execution")
    result = {
        **summary,
        "mode": mode,
        "dataset_sha256": checksum,
        "doris_tables_verified": len(checks),
        "doris_rows_verified": sum(checks.values()),
        "doris_reference_checks": reference_count,
        "doris_etl_cross_checks": etl_count,
        "started_at": started.isoformat(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    private = {
        **result,
        "target_cluster": str(cluster_id),
        "target_endpoint": f"{endpoint['host']}:{endpoint['port']}",
        "topology_before": baseline,
        "topology_after": after,
        "table_rows_verified": checks,
        "row_digests": table_rows,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("x", encoding="utf-8") as out:
        receipt.chmod(0o600)
        out.write(dump_json(private))
    return result
