"""One-off, opt-in archival of verified legacy browser fixtures. No file deletion.

Export -> rehearse (DELETE/restore/ROLLBACK) -> apply. JSON stays outside Git.
The full pg_dump backup is mandatory. Unknown plants and all users are retained.
"""

import argparse
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.core.db import engine

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Parent-before-child order, reviewed against the migration foreign keys.
TABLES = (
    "plant",
    "production_line",
    "device",
    "tag",
    "acquisition_task",
    "acquisition_node",
    "import_batch",
    "import_field_mapping",
    "import_job",
    "data_quality_issue",
    "tag_sample",
    "knowledge_document",
    "assistant_run",
    "user_plant_access",
    "retention_preview",
)


def verified_fixture(row: dict) -> bool:
    code, name, location = row["code"], row["name"], row.get("location")
    if re.fullmatch(r"AI_UI_\d{13}_\d{1,4}", code):
        return name == f"AI界面测试 {code}" and location == "合成数据测试"
    if re.fullmatch(r"P_E2E_\d{8}", code):
        return name == "数据治理测试工厂" and location == "隔离测试环境"
    if re.fullmatch(r"PLANT_[A-Z0-9]{8}", code):
        return (
            name in (f"Synthetic Plant {code}", f"Synthetic Plant {code} Updated")
            and location == "Test Zone"
        )
    if re.fullmatch(r"AI_LIVE_\d{13}", code):
        return name == f"AI真实联调合成工厂 {code}" and location == "合成验收数据"
    if re.fullmatch(r"AI_BUSINESS_\d{13}", code):
        return (
            name == f"AI业务验收演示工厂 {code.removeprefix('AI_BUSINESS_')}"
            and location == "合成演示，不是生产数据"
        )
    if re.fullmatch(r"RET_[AB]_\d{13}", code):
        return name == f"保留预览测试{code[4]}" and location is None
    return False


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rows(connection, table, where="TRUE", params=None):
    return (
        connection.execute(
            text(f'SELECT to_jsonb(t) FROM public."{table}" t WHERE {where}'),
            params or {},
        )
        .scalars()
        .all()
    )


def snapshot(connection, plant_ids):
    result = {
        "plant": rows(
            connection, "plant", "id = ANY(CAST(:ids AS uuid[]))", {"ids": plant_ids}
        )
    }

    def related(table, field, parent):
        ids = [row["id"] for row in result[parent]]
        result[table] = rows(
            connection, table, f'"{field}" = ANY(CAST(:ids AS uuid[]))', {"ids": ids}
        )

    related("production_line", "plant_id", "plant")
    related("device", "production_line_id", "production_line")
    related("tag", "device_id", "device")
    related("acquisition_task", "plant_id", "plant")
    related("import_batch", "plant_id", "plant")
    for table in ("import_field_mapping", "import_job", "data_quality_issue"):
        related(table, "batch_id", "import_batch")
    for table in (
        "knowledge_document",
        "assistant_run",
        "user_plant_access",
        "retention_preview",
    ):
        related(table, "plant_id", "plant")
    tags = [row["id"] for row in result["tag"]]
    tasks = [row["id"] for row in result["acquisition_task"]]
    batches = [row["id"] for row in result["import_batch"]]
    for table in ("acquisition_node", "tag_sample"):
        conditions = "tag_id = ANY(CAST(:tags AS uuid[])) OR task_id = ANY(CAST(:tasks AS uuid[]))"
        if table == "tag_sample":
            conditions += " OR import_batch_id = ANY(CAST(:batches AS uuid[]))"
        result[table] = rows(
            connection,
            table,
            conditions,
            {"tags": tags, "tasks": tasks, "batches": batches},
        )
        for row in result[table]:
            if (
                row["tag_id"] not in tags
                or (row.get("task_id") and row["task_id"] not in tasks)
                or (
                    row.get("import_batch_id") and row["import_batch_id"] not in batches
                )
            ):
                raise RuntimeError("Cross-plant relationship: manual review required")
    for row in rows(connection, "import_batch", "duplicate_of_id IS NOT NULL"):
        if (row["id"] in batches) != (row["duplicate_of_id"] in batches):
            raise RuntimeError("Cross-scope duplicate batch reference: refusing")
    if any(row["desired_state"] != "stopped" for row in result["acquisition_task"]):
        raise RuntimeError("Candidate task is not stopped")
    if any(
        row["status"] in ("queued", "running", "retrying")
        for row in result["import_job"]
    ):
        raise RuntimeError("Candidate import job is active")
    return {table: sorted(result[table], key=canonical) for table in TABLES}


def lock_tables(connection):
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '120s'"))
    names = ", ".join(f'public."{table}"' for table in (*TABLES, "user"))
    connection.execute(text(f"LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE"))


def remove(connection, data):
    for table in reversed(TABLES):
        if table == "user_plant_access":
            for row in data[table]:
                connection.execute(
                    text(
                        "DELETE FROM public.user_plant_access WHERE user_id=CAST(:user_id AS uuid) AND plant_id=CAST(:plant_id AS uuid)"
                    ),
                    row,
                )
        elif data[table]:
            connection.execute(
                text(
                    f'DELETE FROM public."{table}" WHERE id IN (SELECT id FROM jsonb_populate_recordset(NULL::public."{table}", CAST(:data AS jsonb)))'
                ),
                {"data": canonical(data[table])},
            )


def restore(connection, data):
    for table in TABLES:
        records = data[table]
        if table == "import_batch":
            records = [{**row, "duplicate_of_id": None} for row in records]
        if records:
            connection.execute(
                text(
                    f'INSERT INTO public."{table}" SELECT * FROM jsonb_populate_recordset(NULL::public."{table}", CAST(:data AS jsonb))'
                ),
                {"data": canonical(records)},
            )
    for row in data["import_batch"]:
        if row["duplicate_of_id"]:
            connection.execute(
                text(
                    "UPDATE import_batch SET duplicate_of_id=CAST(:duplicate_of_id AS uuid) WHERE id=CAST(:id AS uuid)"
                ),
                row,
            )


def fingerprints(connection, data):
    """Stream all non-target rows through SHA256; no large memory/disk staging."""
    result = {}
    raw = connection.connection.driver_connection
    for table in (*TABLES, "user"):
        excluded = data.get(table, [])
        if table == "user_plant_access":
            # These grants all belong to exactly the frozen plant IDs.
            ids = [row["id"] for row in data["plant"]]
            where = "plant_id <> ALL(%s::uuid[])"
            order = "plant_id,user_id"
        else:
            ids = [row["id"] for row in excluded]
            where = (
                "id <> ALL(%s::text[])"  # UUID/int -> text for one safe parameter type
            )
            where = where.replace("id <>", "id::text <>")
            order = "id"
        digest = hashlib.sha256()
        with raw.cursor() as cursor:
            with cursor.copy(
                f'COPY (SELECT * FROM public."{table}" WHERE {where} ORDER BY {order}) TO STDOUT',
                (ids,),
            ) as stream:
                for chunk in stream:
                    digest.update(chunk)
        result[table] = digest.hexdigest()
    return result


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "rehearse", "apply", "restore"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-plants", type=int, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    if not str(directory).startswith("/mnt/e/"):
        parser.error("Archive must be on E:")
    backup = directory / "backup.dump"
    if not backup.is_file() or backup.stat().st_size == 0:
        parser.error("Full backup.dump required")
    archive = directory / "fixtures.json"
    if args.mode == "export":
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                plants = rows(connection, "plant")
                ids = [row["id"] for row in plants if verified_fixture(row)]
                if len(ids) != args.expected_plants:
                    raise RuntimeError(
                        "Unexpected candidate count; review before proceeding"
                    )
                data = snapshot(connection, ids)
                database = connection.execute(
                    text("SELECT current_database()")
                ).scalar_one()
            write_new(
                archive,
                {
                    "database": database,
                    "created_at": datetime.now(UTC).isoformat(),
                    "backup_sha256": digest_file(backup),
                    "data": data,
                },
            )
        logger.info(json.dumps({table: len(value) for table, value in data.items()}))
        return
    manifest = json.loads(archive.read_text(encoding="utf-8"))
    data = manifest["data"]
    if (
        set(data) != set(TABLES)
        or len(data["plant"]) != args.expected_plants
        or not all(verified_fixture(row) for row in data["plant"])
    ):
        raise RuntimeError("Invalid frozen candidate manifest")
    if digest_file(backup) != manifest["backup_sha256"]:
        raise RuntimeError("Backup digest changed")
    report_path = directory / f"{args.mode}-report.json"
    if report_path.exists():
        raise RuntimeError("Report already exists; refusing repeat execution")
    archive_hash = digest_file(archive)
    if args.mode == "apply":
        rehearsal = json.loads((directory / "rehearse-report.json").read_text())
        if (
            rehearsal["archive_sha256"] != archive_hash
            or not rehearsal["restored_exactly"]
        ):
            raise RuntimeError("Matching successful recovery rehearsal required")
    ids = [row["id"] for row in data["plant"]]
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            lock_tables(connection)
            if (
                connection.execute(text("SELECT current_database()")).scalar_one()
                != manifest["database"]
            ):
                raise RuntimeError("Wrong target database")
            before = fingerprints(connection, data)
            current = snapshot(connection, ids)
            if args.mode == "restore":
                if any(current.values()):
                    raise RuntimeError(
                        "Restore target IDs already exist; refusing overwrite"
                    )
                restore(connection, data)
            else:
                if canonical(current) != canonical(data):
                    raise RuntimeError("Candidate rows changed since export; refusing")
                remove(connection, data)
                if any(snapshot(connection, ids).values()):
                    raise RuntimeError("Incomplete removal")
                if args.mode == "rehearse":
                    restore(connection, data)
            restored = args.mode != "apply" and canonical(
                snapshot(connection, ids)
            ) == canonical(data)
            if args.mode != "apply" and not restored:
                raise RuntimeError("Restore row comparison failed")
            after = fingerprints(connection, data)
            if before != after:
                raise RuntimeError("Protected rows changed; rolling back")
            if args.mode == "rehearse":
                transaction.rollback()
            else:
                transaction.commit()
        except BaseException:
            transaction.rollback()
            raise
    report = {
        "mode": args.mode,
        "archive_sha256": archive_hash,
        "backup_sha256": manifest["backup_sha256"],
        "restored_exactly": restored,
        "protected_rows_unchanged": before == after,
        "protected_sha256": after,
        "counts": {table: len(value) for table, value in data.items()},
        "at": datetime.now(UTC).isoformat(),
    }
    write_new(report_path, report)
    logger.info(
        json.dumps(
            {key: value for key, value in report.items() if key != "protected_sha256"}
        )
    )


if __name__ == "__main__":
    main()
