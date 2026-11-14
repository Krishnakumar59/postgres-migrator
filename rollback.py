#!/usr/bin/env python3
"""
Emergency PostgreSQL Rollback & Disaster Recovery CLI.
Instantly restores any database from a local snapshot backup.
"""
import os
import sys
import argparse
from dotenv import load_dotenv
from rich.console import Console

from pg_migrator.rollback import EmergencyRollback

load_dotenv()
console = Console()

def main():
    parser = argparse.ArgumentParser(
        description="Emergency PostgreSQL Rollback & Disaster Recovery Tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "--db", "-d",
        default=os.getenv("CURRENT_PRODUCTION_DB_URL"),
        help="Database URL to rollback/restore onto (defaults to CURRENT_PRODUCTION_DB_URL)"
    )

    parser.add_argument(
        "--file", "-f",
        type=str,
        default=None,
        help="Specific backup .dump file to restore (if not provided, shows interactive menu)"
    )

    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=4,
        help="Parallel pg_restore worker jobs"
    )

    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Bypass interactive confirmation"
    )

    args = parser.parse_args()

    if not args.db:
        console.print("[bold red]Error:[/bold red] Database URL not provided. Pass [cyan]--db[/cyan] or set [cyan]CURRENT_PRODUCTION_DB_URL[/cyan] in [yellow].env[/yellow]")
        sys.exit(1)

    runner = EmergencyRollback()
    ok = runner.rollback_database(
        db_url=args.db,
        backup_file=args.file,
        parallel_jobs=args.jobs,
        interactive=not args.yes
    )
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
