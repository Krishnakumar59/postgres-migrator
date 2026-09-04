import psycopg2
from urllib.parse import urlparse
from rich.console import Console
from rich.panel import Panel

console = Console()

class PostgresCleaner:
    """
    Strict Target Database Inspector & Cleaner.
    Enforces a HARD BLOCK policy: Refuses to run if target is not empty.
    """
    
    def __init__(self, db_url: str):
        self.db_url = db_url

    def get_database_summary(self) -> dict:
        """Inspects table counts, row counts, and database size."""
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT table_schema, table_name 
            FROM information_schema.tables 
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE';
        """)
        tables = cur.fetchall()
        table_count = len(tables)

        cur.execute("SELECT pg_database_size(current_database()) / (1024.0 * 1024.0);")
        size_mb = float(cur.fetchone()[0])

        cur.execute("SELECT current_database(), current_user;")
        db_name, user = cur.fetchone()

        cur.close()
        conn.close()

        return {
            "tables": tables,
            "table_count": table_count,
            "size_mb": size_mb,
            "db_name": db_name,
            "user": user
        }

    def verify_target_is_empty_or_clean(self) -> bool:
        """
        HARD BLOCK SAFEGUARD:
        If target database contains any existing tables, this function 
        HARD-STOPS and refuses to continue. No prompts, no accidents allowed.
        """
        summary = self.get_database_summary()

        if summary["table_count"] > 0:
            parsed = urlparse(self.db_url)
            host_display = parsed.hostname or "target-database"

            table_preview = ", ".join([f"{t[0]}.{t[1]}" for t in summary["tables"][:5]])
            if summary["table_count"] > 5:
                table_preview += f" ... (+{summary['table_count'] - 5} more)"

            console.print(Panel(
                f"[bold red]⛔ HARD BLOCK: TARGET DATABASE IS NOT EMPTY! EXECUTION HALTED ⛔[/bold red]\n\n"
                f"• Target Host: [cyan]{host_display}[/cyan]\n"
                f"• Database Name: [cyan]{summary['db_name']}[/cyan]\n"
                f"• Existing Tables Found: [bold red]{summary['table_count']} tables[/bold red] ({summary['size_mb']:.2f} MB)\n"
                f"• Sample Tables: [yellow]{table_preview}[/yellow]\n\n"
                f"[bold white]Reason for Aborting:[/bold white]\n"
                f"To protect you against accidental production data loss, the migration tool\n"
                f"[bold red]REFUSES TO PROCEED[/bold red] if the destination database contains existing data.\n\n"
                f"[bold green]How to proceed safely:[/bold green]\n"
                f"1. Provide a completely fresh/empty database on your new provider, OR\n"
                f"2. Check if your [cyan]SOURCE[/cyan] and [cyan]TARGET[/cyan] URLs were accidentally swapped!",
                title="🛡️ Zero-Tolerance Data Protection Barrier",
                border_style="red"
            ))
            return False

        # If target has 0 tables, reset schema cleanly for clean restoration
        try:
            conn = psycopg2.connect(self.db_url)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
            cur.execute("CREATE SCHEMA public;")
            cur.execute("GRANT ALL ON SCHEMA public TO public;")
            cur.close()
            conn.close()
            console.print("[bold green]✔ Target database verified empty and public schema initialized cleanly.[/bold green]")
            return True
        except Exception as e:
            console.print(f"[bold red]❌ Failed to initialize fresh public schema on target:[/bold red] {e}")
            return False
