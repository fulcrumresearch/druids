"""Migrate trace files from {user_id}/{slug}.jsonl to {execution_id}.jsonl.

Looks up each slug in the database to find the execution_id, then renames
the trace file. Skips files that can't be matched. Safe to run multiple
times — already-migrated files are ignored.

Usage:
    cd server && uv run python scripts/migrate_traces.py [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

from druids_server.db.models.execution import ExecutionRecord
from druids_server.db.session import get_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


TRACES_DIR = Path(__file__).parent.parent / "logs" / "traces"


async def migrate(dry_run: bool = False) -> None:
    """Find user_id/slug.jsonl files and rename to execution_id.jsonl."""
    if not TRACES_DIR.exists():
        print("No traces directory found.")
        return

    migrated = 0
    skipped = 0
    errors = 0

    for user_dir in sorted(TRACES_DIR.iterdir()):
        if not user_dir.is_dir():
            continue

        # Skip directories that aren't user_id UUIDs
        try:
            UUID(user_dir.name)
        except ValueError:
            continue

        user_id = UUID(user_dir.name)

        for trace_file in sorted(user_dir.glob("*.jsonl")):
            slug = trace_file.stem

            async with get_session() as db:
                result = await db.execute(
                    select(ExecutionRecord).where(
                        ExecutionRecord.user_id == user_id,
                        ExecutionRecord.slug == slug,
                    )
                )
                record = result.scalar_one_or_none()

            if not record:
                print(f"  SKIP {user_dir.name}/{slug}.jsonl (no matching execution)")
                skipped += 1
                continue

            dest = TRACES_DIR / f"{record.id}.jsonl"

            if dest.exists():
                print(f"  SKIP {user_dir.name}/{slug}.jsonl (destination already exists)")
                skipped += 1
                continue

            if dry_run:
                print(f"  WOULD MOVE {user_dir.name}/{slug}.jsonl -> {record.id}.jsonl")
            else:
                trace_file.rename(dest)
                print(f"  MOVED {user_dir.name}/{slug}.jsonl -> {record.id}.jsonl")
            migrated += 1

        # Remove empty user directories
        if not dry_run and user_dir.exists() and not any(user_dir.iterdir()):
            user_dir.rmdir()
            print(f"  REMOVED empty directory {user_dir.name}/")

    print(f"\nDone. Migrated: {migrated}, Skipped: {skipped}, Errors: {errors}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no files will be moved.\n")
    asyncio.run(migrate(dry_run=dry_run))
