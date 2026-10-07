"""Restore a logical backup into an isolated database and verify additive migration.

Usage: python tests/restore_smoke.py SOURCE_CONTAINER TARGET_CONTAINER BACKUP_PATH
The source must be the isolated copy, never a production container.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    source, target, destination = sys.argv[1:]
    assert source == "code-mortis-upgrade-source-check"
    assert target == "code-mortis-tests-postgres-1"
    database = "mortis_upgrade_check"

    def sql(container, name, query):
        return subprocess.check_output(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "mortis",
                "-d",
                name,
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                query,
            ],
            text=True,
        ).strip()

    columns = json.loads(
        sql(
            source,
            "mortis",
            """SELECT json_object_agg(table_name, cols) FROM (
        SELECT table_name, json_agg(column_name ORDER BY ordinal_position) cols
        FROM information_schema.columns WHERE table_schema='public'
        AND table_name NOT IN ('alembic_version','oauth_states') GROUP BY table_name) t""",
        )
    )

    def fingerprints(container, name):
        result = {}
        for table, names in columns.items():
            fields = ",".join('"' + field + '"' for field in names)
            rows = json.loads(
                sql(
                    container,
                    name,
                    f"SELECT COALESCE(json_agg(t),'[]'::json) FROM (SELECT {fields} FROM \"{table}\") t",
                )
            )
            canonical = sorted(json.dumps(row, sort_keys=True) for row in rows)
            result[table] = (len(rows), hashlib.sha256(json.dumps(canonical).encode()).hexdigest())
        return result

    before = fingerprints(source, "mortis")
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as output:
        path.chmod(0o600)
        subprocess.run(
            ["docker", "exec", source, "pg_dump", "-U", "mortis", "-d", "mortis", "-Fc"],
            stdout=output,
            check=True,
        )
    subprocess.run(["docker", "exec", target, "createdb", "-U", "mortis", database], check=True)
    with path.open("rb") as backup:
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                target,
                "pg_restore",
                "-U",
                "mortis",
                "-d",
                database,
                "--no-owner",
                "--exit-on-error",
            ],
            stdin=backup,
            check=True,
        )
    env = {
        **os.environ,
        "CM_DATABASE_URL": f"postgresql+psycopg://mortis:mortis-test-only@127.0.0.1:55439/{database}",
        "CM_ENCRYPTION_KEY": base64.urlsafe_b64encode(bytes(32)).decode(),
        "CM_BOOTSTRAP_TOKEN": "restore-check-only-not-for-deployment",
        "CM_OWNER_GITHUB_LOGIN": "test-owner",
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    after = fingerprints(target, database)
    assert before == after, "An existing record changed during restore or additive migration"
    assert sql(target, database, "SELECT version_num FROM alembic_version") == "20261006_connections"
    print(
        f"Backup restored and migrated; {sum(count for count, _ in before.values())} existing records preserved across {len(before)} tables."
    )
    print("OAuth attempts intentionally invalidated; original volume and instance untouched.")


if __name__ == "__main__":
    main()
