import os
import sys
import argparse
from dotenv import load_dotenv
from rich.console import Console

from .migrator import DatabaseMigrator
from .cleaner import PostgresCleaner
from .verifier import PostgresVerifier
from .compatibility import CompatibilityAuditor
from .comparator import PerformanceComparator
from .sync import DeltaSyncEngine
from .notifier import MigrationNotifier

load_dotenv()
console = Console()

def main():
    parser = argparse.ArgumentParser(
        description="Enterprise Safe PostgreSQL Migration & Cutover Tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "--current-prod", "-p",
        default=os.getenv("CURRENT_PRODUCTION_DB_URL"),
        help="Current Production Database URL (Data will be READ from here)"
    )
    
    parser.add_argument(
        "--new-db", "-n",
        default=os.getenv("NEW_DATABASE_URL"),
        help="New Empty Database URL (Data will be RESTORED here)"
    )

    parser.add_argument(
        "--sync-missing", "--delta-sync",
        action="store_true",
        dest="delta_sync",
        help="Incremental Catch-Up: Only sync missing/new rows without re-dumping the whole database"
    )

    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Send a test alert to verify Discord, Slack, Telegram, and Desktop notifications"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without modifying or transferring data"
    )

    parser.add_argument(
        "--audit-compatibility",
        action="store_true",
        help="Only audit extensions, custom types, and version compatibility"
    )

    parser.add_argument(
        "--compare-performance",
        action="store_true",
        help="Run side-by-side performance benchmark comparing Old vs New provider"
    )

    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only audit and compare row counts & sequences between Current Production and New Database"
    )
    
    parser.add_argument(
        "--backup-both",
        action="store_true",
        help="Only create binary snapshot dumps of BOTH databases into ./backups/"
    )
    
    parser.add_argument(
        "--restore-file",
        type=str,
        help="Restore a specific dump file onto the New Database"
    )
    
    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=4,
        help="Number of parallel workers for pg_restore"
    )

    args = parser.parse_args()

    # 1. Test Notification Only
    if args.test_notification:
        notifier = MigrationNotifier()
        notifier.test_alert()
        sys.exit(0)

    if not args.current_prod and not args.restore_file:
        console.print("[bold red]Error:[/bold red] Current production database URL not provided.")
        console.print("Pass [cyan]--current-prod[/cyan] or set [cyan]CURRENT_PRODUCTION_DB_URL[/cyan] in [yellow].env[/yellow]")
        sys.exit(1)

    if not args.new_db:
        console.print("[bold red]Error:[/bold red] New database URL not provided.")
        console.print("Pass [cyan]--new-db[/cyan] or set [cyan]NEW_DATABASE_URL[/cyan] in [yellow].env[/yellow]")
        sys.exit(1)

    # 2. Delta Catch-Up Sync Mode
    if args.delta_sync:
        sync_engine = DeltaSyncEngine(args.current_prod, args.new_db)
        sync_engine.sync_missing_data()
        
        verifier = PostgresVerifier(args.current_prod, args.new_db)
        res = verifier.verify_migration()
        sys.exit(0 if res["success"] else 1)

    # 3. Compatibility Audit Only
    if args.audit_compatibility:
        auditor = CompatibilityAuditor(args.current_prod, args.new_db)
        ok = auditor.audit_and_prepare()
        sys.exit(0 if ok else 1)

    # 4. Performance Comparison Only
    if args.compare_performance and not args.dry_run:
        comparator = PerformanceComparator(args.current_prod, args.new_db)
        comparator.compare()
        sys.exit(0)

    # 5. Verify Only
    if args.verify_only:
        verifier = PostgresVerifier(args.current_prod, args.new_db)
        res = verifier.verify_migration()
        sys.exit(0 if res["success"] else 1)

    migrator = DatabaseMigrator(args.current_prod, args.new_db)

    # 6. Dry Run Mode
    if args.dry_run:
        ok = migrator.run_dry_run()
        sys.exit(0 if ok else 1)

    # 7. Backup Both Databases Only
    if args.backup_both:
        prod_dump, new_dump = migrator.create_dual_preflight_backups()
        console.print(f"[bold green]✔ Dual backups ready in ./backups/:[/bold green]")
        console.print(f"  • Production: [yellow]{prod_dump}[/yellow]")
        console.print(f"  • New DB:     [yellow]{new_dump}[/yellow]")
        sys.exit(0)

    # 8. Restore Specific File Only
    if args.restore_file:
        cleaner = PostgresCleaner(args.new_db)
        if not cleaner.verify_target_is_empty_or_clean():
            sys.exit(1)
        ok = migrator.restore_backup(args.restore_file, parallel_jobs=args.jobs)
        sys.exit(0 if ok else 1)

    # 9. Full End-to-End Enterprise Migration Pipeline
    res = migrator.run_full_migration(
        parallel_jobs=args.jobs,
        compare_performance=True
    )
    sys.exit(0 if res.get("success") else 1)

if __name__ == "__main__":
    main()
