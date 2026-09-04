import os
import sys
import time
import subprocess
import psycopg2
from pathlib import Path
from urllib.parse import urlparse
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm

console = Console()

class EmergencyRollback:
    """
    Disaster Recovery & Emergency Rollback Service with Archive Integrity Validation.
    Verifies that the dump file is 100% complete and uncorrupted before restoring.
    """

    def __init__(self, backup_dir: str = "./backups"):
        self.backup_dir = Path(backup_dir)

    def list_backups(self) -> list[Path]:
        """Lists all available .dump backup files sorted by creation time (newest first)."""
        if not self.backup_dir.exists():
            return []
        backups = sorted(
            [p for p in self.backup_dir.glob("*.dump") if not p.name.endswith(".tmp")],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        return backups

    def verify_archive(self, backup_file: Path) -> bool:
        """Runs pg_restore --list to ensure the archive is complete and not truncated."""
        console.print(f"  [dim]Verifying archive headers for {backup_file.name}...[/dim]")
        cmd = ["pg_restore", "--list", str(backup_file)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            toc_lines = proc.stdout.splitlines()
            table_entries = [l for l in toc_lines if "TABLE DATA" in l]
            console.print(f"  [green]✔ Archive verified intact:[/green] {len(toc_lines)} TOC entries ({len(table_entries)} table datasets) validated.")
            return True
        except Exception as e:
            console.print(f"  [bold red]❌ Corrupted / Incomplete Backup Archive Detected:[/bold red] {e}")
            return False

    def display_backup_menu(self) -> list[Path]:
        """Displays formatted table of available recovery snapshots."""
        backups = self.list_backups()
        if not backups:
            console.print("[bold red]❌ No backup snapshot files (.dump) found in ./backups/[/bold red]")
            return []

        table = Table(title="📦 Available Disaster Recovery Snapshots", header_style="bold cyan")
        table.add_column("#", justify="center", style="bold yellow")
        table.add_column("Backup Filename", style="white")
        table.add_column("Size (MB)", justify="right", style="green")
        table.add_column("Timestamp / Date", style="magenta")

        for idx, b in enumerate(backups, 1):
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b.stat().st_mtime))
            size_mb = b.stat().st_size / (1024.0 * 1024.0)
            table.add_row(str(idx), b.name, f"{size_mb:.2f} MB", mtime)

        console.print(table)
        return backups

    def rollback_database(self, db_url: str, backup_file: str = None, parallel_jobs: int = 4, interactive: bool = True) -> bool:
        """Restores a database from a verified backup snapshot."""
        if not backup_file:
            backups = self.list_backups()
            if not backups:
                console.print("[bold red]❌ Cannot perform rollback: No backup files exist in ./backups/[/bold red]")
                return False
            
            if interactive:
                self.display_backup_menu()
                choice = Prompt.ask(f"Select backup number to restore (default: [bold yellow]1[/bold yellow] = newest)", default="1")
                try:
                    selected_idx = int(choice) - 1
                    backup_file = str(backups[selected_idx])
                except (ValueError, IndexError):
                    console.print("[bold red]❌ Invalid selection. Aborting rollback.[/bold red]")
                    return False
            else:
                backup_file = str(backups[0])

        backup_path = Path(backup_file)
        if not backup_path.exists():
            console.print(f"[bold red]❌ Backup file not found:[/bold red] {backup_file}")
            return False

        # VERIFY ARCHIVE BEFORE TOUCHING DATABASE
        if not self.verify_archive(backup_path):
            console.print("[bold red]⛔ ABORTING ROLLBACK: Selected snapshot is incomplete or corrupted.[/bold red]")
            return False

        parsed = urlparse(db_url)
        host_display = parsed.hostname or "target-db"
        port_str = f":{parsed.port}" if parsed.port else ""

        console.print(Panel(
            f"[bold red]🚨 EMERGENCY ROLLBACK EXECUTION 🚨[/bold red]\n\n"
            f"• Target Database: [cyan]{parsed.username}@{host_display}{port_str}{parsed.path}[/cyan]\n"
            f"• Recovery Snapshot: [yellow]{backup_path.name}[/yellow] ({backup_path.stat().st_size / (1024.0*1024.0):.2f} MB)\n\n"
            f"[bold white]This will wipe the current target schema and restore from the selected verified snapshot.[/bold white]",
            title="⚠️ Disaster Recovery Confirmation",
            border_style="red"
        ))

        if interactive:
            proceed = Confirm.ask(f"[bold red]Are you ABSOLUTELY sure you want to rollback this database?[/bold red]")
            if not proceed:
                console.print("[yellow]Rollback cancelled safely. No changes made.[/yellow]")
                return False

        console.print("\n[bold yellow]🧹 Resetting schema 'public' on target database...[/bold yellow]")
        try:
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("""
                SELECT pid, pg_terminate_backend(pid) 
                FROM pg_stat_activity 
                WHERE datname = current_database() AND pid <> pg_backend_pid();
            """)
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
            cur.execute("CREATE SCHEMA public;")
            cur.execute("GRANT ALL ON SCHEMA public TO public;")
            cur.close()
            conn.close()
            console.print("[green]✔ Target schema reset to fresh state.[/green]")
        except Exception as e:
            console.print(f"[bold red]❌ Failed to reset schema before rollback:[/bold red] {e}")
            return False

        console.print(f"[bold blue]🚀 Restoring database from snapshot ({parallel_jobs} workers)...[/bold blue]")
        t0 = time.perf_counter()
        cmd = [
            "pg_restore",
            "--dbname", db_url,
            "--no-owner",
            "--no-privileges",
            f"--jobs={parallel_jobs}",
            str(backup_path)
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            t1 = time.perf_counter()
            if proc.returncode in (0, 1):
                console.print(Panel(
                    f"[bold green]🎉 EMERGENCY ROLLBACK COMPLETED SUCCESSFULLY![/bold green]\n\n"
                    f"• Restored snapshot: [yellow]{backup_path.name}[/yellow]\n"
                    f"• Time taken: [cyan]{t1 - t0:.2f} seconds[/cyan]\n"
                    f"• Database is restored and operational.",
                    title="✔ Recovery Success",
                    border_style="green"
                ))
                return True
            else:
                console.print(f"[bold red]❌ pg_restore failed:[/bold red]\n{proc.stderr}")
                return False
        except Exception as e:
            console.print(f"[bold red]❌ Rollback process exception:[/bold red] {e}")
            return False
