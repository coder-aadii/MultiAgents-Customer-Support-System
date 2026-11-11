#!/usr/bin/env bash
set -euo pipefail

# MCSS Migration Lifecycle Verification Script (Alembic 0014–0018)
# Performs automated lifecycle testing (upgrade head -> downgrade 0013 -> upgrade head)
# against a disposable PostgreSQL database without modifying development or production data.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
API_DIR="$PROJECT_ROOT/api"

echo "================================================================="
echo " MCSS Alembic 0014–0018 Migration Lifecycle Verification Gate"
echo "================================================================="

# Detect execution environment: host or inside container
IN_CONTAINER=false
if [ -f "/.dockerenv" ] || [ -f "/app/alembic.ini" ]; then
    IN_CONTAINER=true
fi

run_lifecycle_python() {
python - <<'EOF'
import os
import sys
import uuid
import psycopg
from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, inspect, text

base_url = os.environ.get("MCSS_DATABASE_URL", "postgresql+psycopg://mcss:change-me@postgres:5432/mcss")
temp_db_name = f"mcss_lifecycle_test_{uuid.uuid4().hex[:8]}"

import re
match = re.search(r"://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(.+)", base_url)
if not match:
    print(f"ERROR: Cannot parse database URL: {base_url}")
    sys.exit(1)

user, password, host, port, _ = match.groups()
port = int(port) if port else 5432

print(f"Creating disposable verification database '{temp_db_name}'...")
with psycopg.connect(
    dbname="postgres",
    user=user,
    password=password,
    host=host,
    port=port,
    autocommit=True,
) as conn:
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{temp_db_name}"')

lifecycle_db_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{temp_db_name}"

cfg = Config("alembic.ini")
cfg.set_main_option("script_location", "alembic")
cfg.set_main_option("sqlalchemy.url", lifecycle_db_url)

try:
    print("\n--- Step 1: Upgrading disposable database to 'head' ---")
    command.upgrade(cfg, "head")

    engine = create_engine(lifecycle_db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    # Check 0014-0016 tables
    assert "agent_execution_audits" in tables, "Table 'agent_execution_audits' missing in head"
    assert "workflow_checkpoints" in tables, "Table 'workflow_checkpoints' missing in head"
    assert "knowledge_ingestion_jobs" in tables, "Table 'knowledge_ingestion_jobs' missing in head"

    # Check 0017 column
    doc_cols = {col["name"] for col in inspector.get_columns("knowledge_documents")}
    assert "content_hash" in doc_cols, "Column 'content_hash' missing on knowledge_documents in head"

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        print(f"  Head revision verified: {rev}")

    print("\n--- Step 2: Downgrading to '0013_support_operations' ---")
    command.downgrade(cfg, "0013_support_operations")

    inspector_downgraded = inspect(engine)
    tables_downgraded = set(inspector_downgraded.get_table_names())

    assert "agent_execution_audits" not in tables_downgraded, "Table 'agent_execution_audits' was not dropped"
    assert "workflow_checkpoints" not in tables_downgraded, "Table 'workflow_checkpoints' was not dropped"
    assert "knowledge_ingestion_jobs" not in tables_downgraded, "Table 'knowledge_ingestion_jobs' was not dropped"

    doc_cols_downgraded = {col["name"] for col in inspector_downgraded.get_columns("knowledge_documents")}
    assert "content_hash" not in doc_cols_downgraded, "Column 'content_hash' was not dropped"

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        print(f"  Downgrade revision verified: {rev}")
        assert rev == "0013_support_operations"

    print("\n--- Step 3: Re-upgrading to 'head' ---")
    command.upgrade(cfg, "head")

    inspector_reupgraded = inspect(engine)
    tables_reupgraded = set(inspector_reupgraded.get_table_names())

    assert "agent_execution_audits" in tables_reupgraded, "Table 'agent_execution_audits' missing after re-upgrade"
    assert "workflow_checkpoints" in tables_reupgraded, "Table 'workflow_checkpoints' missing after re-upgrade"
    assert "knowledge_ingestion_jobs" in tables_reupgraded, "Table 'knowledge_ingestion_jobs' missing after re-upgrade"

    doc_cols_reupgraded = {col["name"] for col in inspector_reupgraded.get_columns("knowledge_documents")}
    assert "content_hash" in doc_cols_reupgraded, "Column 'content_hash' missing after re-upgrade"

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        print(f"  Re-upgrade revision verified: {rev}")

    engine.dispose()
    print("\nSUCCESS: All migration lifecycle assertions passed cleanly (0014 -> 0018 reversible).")

finally:
    print(f"\nCleaning up disposable database '{temp_db_name}'...")
    with psycopg.connect(
        dbname="postgres",
        user=user,
        password=password,
        host=host,
        port=port,
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{temp_db_name}' AND pid <> pg_backend_pid()
            """)
            cur.execute(f'DROP DATABASE IF EXISTS "{temp_db_name}"')
    print("Cleanup complete.")
EOF
}

if [ "$IN_CONTAINER" = true ]; then
    cd /app
    run_lifecycle_python
else
    echo "Running lifecycle verification inside 'mcss-api' container..."
    docker exec -i mcss-api python - <<'PY_SCRIPT'
import os
import sys
import uuid
import psycopg
from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, inspect, text

base_url = os.environ.get("MCSS_DATABASE_URL", "postgresql+psycopg://mcss:change-me@postgres:5432/mcss")
temp_db_name = f"mcss_lifecycle_test_{uuid.uuid4().hex[:8]}"

import re
match = re.search(r"://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(.+)", base_url)
if not match:
    print(f"ERROR: Cannot parse database URL: {base_url}")
    sys.exit(1)

user, password, host, port, _ = match.groups()
port = int(port) if port else 5432

print(f"Creating disposable verification database '{temp_db_name}'...")
with psycopg.connect(
    dbname="postgres",
    user=user,
    password=password,
    host=host,
    port=port,
    autocommit=True,
) as conn:
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{temp_db_name}"')

lifecycle_db_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{temp_db_name}"

cfg = Config("alembic.ini")
cfg.set_main_option("script_location", "alembic")
cfg.set_main_option("sqlalchemy.url", lifecycle_db_url)

try:
    print("\n--- Step 1: Upgrading disposable database to 'head' ---")
    command.upgrade(cfg, "head")

    engine = create_engine(lifecycle_db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert "agent_execution_audits" in tables, "Table 'agent_execution_audits' missing in head"
    assert "workflow_checkpoints" in tables, "Table 'workflow_checkpoints' missing in head"
    assert "knowledge_ingestion_jobs" in tables, "Table 'knowledge_ingestion_jobs' missing in head"

    doc_cols = {col["name"] for col in inspector.get_columns("knowledge_documents")}
    assert "content_hash" in doc_cols, "Column 'content_hash' missing on knowledge_documents in head"

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        print(f"  Head revision verified: {rev}")

    print("\n--- Step 2: Downgrading to '0013_support_operations' ---")
    command.downgrade(cfg, "0013_support_operations")

    inspector_downgraded = inspect(engine)
    tables_downgraded = set(inspector_downgraded.get_table_names())

    assert "agent_execution_audits" not in tables_downgraded, "Table 'agent_execution_audits' was not dropped"
    assert "workflow_checkpoints" not in tables_downgraded, "Table 'workflow_checkpoints' was not dropped"
    assert "knowledge_ingestion_jobs" not in tables_downgraded, "Table 'knowledge_ingestion_jobs' was not dropped"

    doc_cols_downgraded = {col["name"] for col in inspector_downgraded.get_columns("knowledge_documents")}
    assert "content_hash" not in doc_cols_downgraded, "Column 'content_hash' was not dropped"

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        print(f"  Downgrade revision verified: {rev}")
        assert rev == "0013_support_operations"

    print("\n--- Step 3: Re-upgrading to 'head' ---")
    command.upgrade(cfg, "head")

    inspector_reupgraded = inspect(engine)
    tables_reupgraded = set(inspector_reupgraded.get_table_names())

    assert "agent_execution_audits" in tables_reupgraded, "Table 'agent_execution_audits' missing after re-upgrade"
    assert "workflow_checkpoints" in tables_reupgraded, "Table 'workflow_checkpoints' missing after re-upgrade"
    assert "knowledge_ingestion_jobs" in tables_reupgraded, "Table 'knowledge_ingestion_jobs' missing after re-upgrade"

    doc_cols_reupgraded = {col["name"] for col in inspector_reupgraded.get_columns("knowledge_documents")}
    assert "content_hash" in doc_cols_reupgraded, "Column 'content_hash' missing after re-upgrade"

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        print(f"  Re-upgrade revision verified: {rev}")

    engine.dispose()
    print("\nSUCCESS: All migration lifecycle assertions passed cleanly (0014 -> 0018 reversible).")

finally:
    print(f"\nCleaning up disposable database '{temp_db_name}'...")
    with psycopg.connect(
        dbname="postgres",
        user=user,
        password=password,
        host=host,
        port=port,
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{temp_db_name}' AND pid <> pg_backend_pid()
            """)
            cur.execute(f'DROP DATABASE IF EXISTS "{temp_db_name}"')
    print("Cleanup complete.")
PY_SCRIPT
fi

echo "================================================================="
echo " Verification Complete: Exit 0"
echo "================================================================="
