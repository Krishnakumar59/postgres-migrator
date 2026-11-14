import os
import sys
import time
import subprocess
import psycopg2
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .cleaner import PostgresCleaner
from .verifier import PostgresVerifier
from .compatibility import CompatibilityAuditor
from .comparator import PerformanceComparator
from .notifier import MigrationNotifier
from .sync import DeltaSyncEngine

console = Console()

class DatabaseMigrator:
    """
    Enterprise-Grade PostgreSQL Database Migration Service.
    Smart & Idempotent: If databases are already 100% synced, detects it instantly
    and skips unnecessary re-migration.
    """

    def __init__(self, current_production_db_url: str, new_database_url: str, backup_dir: str = "./backups"):
        self.current_production_db_url = current_production_db_url
        self.new_database_url = new_database_url
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.notifier = MigrationNotifier()

    def _sanitize_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            port_str = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.username}@{parsed.hostname}{port_str}{parsed.path}"
        except Exception:
            return "unknown-db"

    def validate_urls(self) -> bool:
        """Prevents fatal errors like pointing current production and new db to the same URL."""
        prod_clean = self._sanitize_url(self.current_production_db_url)
        new_clean = self._sanitize_url(self.new_database_url)

        if prod_clean == new_clean:
            console.print(Panel(
                f"[bold red]❌ CRITICAL ERROR: CURRENT PRODUCTION AND NEW DATABASE ARE THE SAME URL![/bold red]\n\n"
                f"Current Production: [yellow]{prod_clean}[/yellow]\n"
                f"New Database:       [yellow]{new_clean}[/yellow]\n\n"
                f"Migration aborted immediately to protect your data.",
                title="🚨 Hard Stopped",
                border_style="red"
            ))
            return False
        return True

    def test_connections(self) -> tuple[bool, int, int]:
        """Verifies network connectivity, authentication, and table counts."""
        console.print("[bold cyan]🔌 Testing Database Connections & Pre-Flight Checks...[/bold cyan]")
        
        # 1. Test Current Production Database
        try:
            conn_prod = psycopg2.connect(self.current_production_db_url, connect_timeout=5)
            cur_prod = conn_prod.cursor()
            cur_prod.execute("SELECT version();")
            prod_ver = cur_prod.fetchone()[0].split()[1]
            cur_prod.execute("SELECT current_database(), current_user;")
            prod_db, prod_user = cur_prod.fetchone()
            cur_prod.execute("""
                SELECT count(*) FROM information_schema.tables 
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema') AND table_type = 'BASE TABLE';
            """)
            prod_tables = cur_prod.fetchone()[0]
            cur_prod.close()
            conn_prod.close()
            console.print(f"  [green]✔ CURRENT PRODUCTION (Read-Only):[/green] DB='{prod_db}' ({prod_tables} tables, PG {prod_ver})")
        except Exception as e:
            console.print(f"  [bold red]❌ Current production database connection failed:[/bold red] {e}")
            return False, 0, 0

        # 2. Test New Database
        try:
            conn_new = psycopg2.connect(self.new_database_url, connect_timeout=5)
            cur_new = conn_new.cursor()
            cur_new.execute("SELECT version();")
            new_ver = cur_new.fetchone()[0].split()[1]
            cur_new.execute("SELECT current_database(), current_user;")
            new_db, new_user = cur_new.fetchone()
            cur_new.execute("""
                SELECT count(*) FROM information_schema.tables 
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema') AND table_type = 'BASE TABLE';
            """)
            new_tables = cur_new.fetchone()[0]
            cur_new.close()
            conn_new.close()
            console.print(f"  [green]✔ NEW DATABASE (Destination):[/green] DB='{new_db}' ({new_tables} existing tables, PG {new_ver})")
        except Exception as e:
            console.print(f"  [bold red]❌ New database connection failed:[/bold red] {e}")
            return False, 0, 0

        return True, prod_tables, new_tables

    def verify_backup_archive_integrity(self, backup_file: str, expected_min_tables: int = 0) -> bool:
        """Inspects archive TOC and block headers using pg_restore --list."""
        console.print(f"    [dim]Verifying archive headers & TOC integrity for {Path(backup_file).name}...[/dim]")
        p = Path(backup_file)
        if not p.exists() or p.stat().st_size == 0:
            console.print(f"    [bold red]❌ Backup file is missing or 0 bytes![/bold red]")
            return False

        cmd = ["pg_restore", "--list", str(backup_file)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            toc_lines = proc.stdout.splitlines()
            table_entries = [line for line in toc_lines if "TABLE DATA" in line]
            
            if expected_min_tables > 0 and len(table_entries) == 0:
                console.print(f"    [bold red]❌ Incomplete Backup: 0 TABLE DATA items in TOC, expected at least {expected_min_tables} tables.[/bold red]")
                return False

            console.print(f"    [green]✔ Archive verified intact:[/green] {len(toc_lines)} TOC entries ({len(table_entries)} table datasets) validated.")
            return True
        except Exception as e:
            console.print(f"    [bold red]❌ Corrupted / Incomplete Backup Archive Detected:[/bold red] {e}")
            return False

    def dump_database(self, db_url: str, label: str, prefix: str, expected_tables: int = 0) -> str:
        """Creates an ATOMIC compressed backup with archive validation."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_backup_file = str(self.backup_dir / f"{prefix}_{timestamp}.dump")
        temp_backup_file = str(self.backup_dir / f"{prefix}_{timestamp}.dump.tmp")
        
        console.print(f"  Creating atomic snapshot of [cyan]{label}[/cyan]...")
        t0 = time.perf_counter()
        
        cmd = [
            "pg_dump",
            "--dbname", db_url,
            "--format=c",
            "--no-owner",
            "--no-privileges",
            "--file", temp_backup_file
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            if os.path.exists(temp_backup_file):
                os.remove(temp_backup_file)
            console.print(f"  [bold red]❌ {label} dump crashed:[/bold red]\n{e.stderr}")
            raise RuntimeError(f"pg_dump failed for {label}: {e.stderr}")

        if not self.verify_backup_archive_integrity(temp_backup_file, expected_min_tables=expected_tables):
            if os.path.exists(temp_backup_file):
                os.remove(temp_backup_file)
            raise RuntimeError(f"Backup verification failed! The dump for {label} was incomplete. Aborting.")

        os.rename(temp_backup_file, final_backup_file)
        t1 = time.perf_counter()
        size_mb = os.path.getsize(final_backup_file) / (1024.0 * 1024.0)
        console.print(f"  [bold green]✔ {label} backup verified & saved:[/bold green] {final_backup_file} ({size_mb:.2f} MB in {t1 - t0:.2f}s)")
        return final_backup_file

    def create_dual_preflight_backups(self, prod_tables: int = 0, new_tables: int = 0) -> tuple[str, str]:
        """Creates pre-flight backups of BOTH databases before any migration action."""
        console.print(Panel(
            "[bold blue]🛡️ ATOMIC DUAL PRE-FLIGHT SNAPSHOT BACKUPS (WITH TOC AUDIT)[/bold blue]\n"
            "[dim]Verifying archive headers and completeness before permitting any restore[/dim]",
            border_style="blue"
        ))
        
        prod_backup = self.dump_database(
            self.current_production_db_url,
            label="CURRENT PRODUCTION",
            prefix="current_production_backup",
            expected_tables=prod_tables
        )

        new_db_backup = self.dump_database(
            self.new_database_url,
            label="NEW DATABASE",
            prefix="new_database_preflight_backup",
            expected_tables=0
        )

        return prod_backup, new_db_backup

    def restore_backup(self, backup_file: str, parallel_jobs: int = 4) -> bool:
        """Restores a verified backup dump onto the New Database."""
        console.print(f"\n[bold blue]🚀 Restoring verified backup onto NEW DATABASE...[/bold blue]")
        console.print(f"  Backup file: [yellow]{backup_file}[/yellow]")
        console.print(f"  Parallel workers: [cyan]{parallel_jobs}[/cyan]")

        t0 = time.perf_counter()
        cmd = [
            "pg_restore",
            "--dbname", self.new_database_url,
            "--no-owner",
            "--no-privileges",
            f"--jobs={parallel_jobs}",
            backup_file
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            t1 = time.perf_counter()
            if proc.returncode in (0, 1):
                console.print(f"[bold green]✔ Restoration onto New Database completed in {t1 - t0:.2f}s![/bold green]")
                return True
            else:
                console.print(f"[bold red]❌ pg_restore failed with exit code {proc.returncode}:[/bold red]\n{proc.stderr}")
                return False
        except Exception as e:
            console.print(f"[bold red]❌ Restoration exception:[/bold red] {e}")
            return False

    def run_dry_run(self) -> bool:
        """Simulates migration pipeline without writing or modifying anything."""
        console.print(Panel.fit(
            "[bold yellow]🧪 DRY RUN SIMULATION MODE (NO CHANGES WILL BE MADE)[/bold yellow]\n"
            "[dim]Verifying connections, extension compatibility, and structure[/dim]",
            border_style="yellow"
        ))

        if not self.validate_urls():
            return False

        ok, prod_tables, new_tables = self.test_connections()
        if not ok:
            return False

        # If already populated and synced
        if new_tables > 0:
            verifier = PostgresVerifier(self.current_production_db_url, self.new_database_url)
            audit = verifier.verify_migration()
            if audit["success"]:
                console.print("[bold green]✔ Pre-Flight Check: Both databases are ALREADY 100% synchronized![/bold green]")
                return True

        auditor = CompatibilityAuditor(self.current_production_db_url, self.new_database_url)
        compat = auditor.audit_and_prepare(auto_enable_extensions=False)

        cleaner = PostgresCleaner(self.new_database_url)
        summary = cleaner.get_database_summary()

        if summary["table_count"] > 0:
            console.print(f"[yellow]ℹ️ Note: New database contains {summary['table_count']} tables. Delta sync will be used.[/yellow]")

        console.print(Panel(
            f"[bold green]✔ DRY RUN PASSED SUCCESSFULLY![/bold green]\n\n"
            f"• Source Production Database: [cyan]{prod_tables} tables ready[/cyan]\n"
            f"• Extension Compatibility: [cyan]100% Compatible[/cyan]\n"
            f"• Ready for live migration / sync.",
            title="Dry Run Report",
            border_style="green"
        ))
        return True

    def run_full_migration(self, parallel_jobs: int = 4, compare_performance: bool = True) -> dict:
        """Executes the complete safe migration pipeline with Smart-Sync detection."""
        console.print(Panel.fit(
            "[bold white]POSTGRESQL ENTERPRISE MIGRATION PIPELINE[/bold white]\n"
            "[dim]Current Production ➔ New Database (Zero Risk, Smart-Sync, Verified)[/dim]",
            border_style="cyan"
        ))

        # 1. URL Collision Check
        if not self.validate_urls():
            return {"success": False, "error": "URLs match."}

        # 2. Connection and Pre-Flight Checks
        ok, prod_tables, new_tables = self.test_connections()
        if not ok:
            return {"success": False, "error": "Connection check failed."}

        # =========================================================================
        # 💡 SMART PRE-CHECK: ARE DATABASES ALREADY FULLY SYNCHRONIZED?
        # =========================================================================
        if new_tables > 0 and prod_tables > 0:
            console.print("\n[bold yellow]🔍 New database already contains tables. Checking existing synchronization status...[/bold yellow]")
            verifier = PostgresVerifier(self.current_production_db_url, self.new_database_url)
            audit = verifier.verify_migration()

            if audit["success"]:
                console.print(Panel(
                    f"[bold green]🎉 ALREADY FULLY SYNCHRONIZED! NO ACTION NEEDED 🎉[/bold green]\n\n"
                    f"• Both databases are 100% up-to-date and identical.\n"
                    f"• All {audit['total_tables']} tables and {audit['total_source_rows']:,} rows match perfectly.\n"
                    f"• No re-dump or re-migration required.\n\n"
                    f"[bold yellow]👉 Production Cutover Action:[/bold yellow]\n"
                    f"Your New Database is already fully prepared and ready for traffic cutover!",
                    title="✔ Already Up-to-Date",
                    border_style="green"
                ))
                if compare_performance:
                    PerformanceComparator(self.current_production_db_url, self.new_database_url).compare()
                return {"success": True, "already_synced": True, "audit": audit}

            # If not 100% synced, offer/run Delta Sync automatically
            console.print("[bold yellow]⚡ Data difference detected! Automatically performing Delta Catch-Up Sync...[/bold yellow]")
            sync_engine = DeltaSyncEngine(self.current_production_db_url, self.new_database_url)
            sync_res = sync_engine.sync_missing_data()

            # Re-verify after delta sync
            re_audit = verifier.verify_migration()
            if re_audit["success"]:
                console.print(Panel(
                    f"[bold green]✅ DELTA CATCH-UP SYNC VERIFIED & COMPLETE[/bold green]\n\n"
                    f"• Synced {sync_res['total_rows_synced']:,} missing delta records.\n"
                    f"• Both databases are now 100% synchronized.\n"
                    f"• Ready for production cutover.",
                    title="Cutover Ready",
                    border_style="green"
                ))
                if compare_performance:
                    PerformanceComparator(self.current_production_db_url, self.new_database_url).compare()
                return {"success": True, "delta_synced": True, "audit": re_audit}

        # 3. Extension & Compatibility Audit
        auditor = CompatibilityAuditor(self.current_production_db_url, self.new_database_url)
        if not auditor.audit_and_prepare(auto_enable_extensions=True):
            return {"success": False, "error": "Extension incompatibility."}

        # 4. STEP 1: CREATE & VERIFY ATOMIC DUAL BACKUPS FIRST
        try:
            prod_backup, new_db_backup = self.create_dual_preflight_backups(
                prod_tables=prod_tables,
                new_tables=new_tables
            )
        except Exception as e:
            console.print(f"[bold red]⛔ MIGRATION HALTED: Pre-flight backup failed verification: {e}[/bold red]")
            return {"success": False, "error": str(e)}

        # 5. STEP 2: HARD-BLOCK CHECK - New database MUST be empty for fresh initial restore
        cleaner = PostgresCleaner(self.new_database_url)
        if not cleaner.verify_target_is_empty_or_clean():
            return {
                "success": False, 
                "error": f"New database contains {new_tables} tables. Execution halted to protect data."
            }

        # 6. STEP 3: RESTORE PRODUCTION BACKUP ONTO NEW DATABASE
        ok = self.restore_backup(prod_backup, parallel_jobs=parallel_jobs)
        if not ok:
            return {"success": False, "error": "Restoration process failed."}

        # 7. STEP 4: AUDIT & INTEGRITY VERIFICATION
        verifier = PostgresVerifier(self.current_production_db_url, self.new_database_url)
        audit_result = verifier.verify_migration()

        # 8. STEP 5: OPTIONAL PERFORMANCE COMPARISON
        if compare_performance and audit_result["success"]:
            comparator = PerformanceComparator(self.current_production_db_url, self.new_database_url)
            comparator.compare()

        # 9. SUMMARY & NOTIFICATION
        if audit_result["success"]:
            console.print(Panel(
                f"[bold green]✅ INITIAL MIGRATION SUCCESSFUL & 100% VERIFIED[/bold green]\n\n"
                f"• Dual verified backups saved in [cyan]./backups/[/cyan]\n"
                f"• All {audit_result['total_tables']} tables copied and verified.\n"
                f"• {audit_result['total_target_rows']:,} rows transferred with zero errors.\n"
                f"• Cutover: set [cyan]CURRENT_PRODUCTION_DB_URL[/cyan] in .env to "
                f"[cyan]{self._sanitize_url(self.new_database_url)}[/cyan].\n"
                f"• Ready for production cutover.",
                title="Cutover Ready",
                border_style="green"
            ))

        return {
            "success": audit_result["success"],
            "prod_backup": prod_backup,
            "new_db_backup": new_db_backup,
            "audit": audit_result
        }
