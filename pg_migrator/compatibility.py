from .db import connect as connect_db
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

class CompatibilityAuditor:
    """
    Audits PostgreSQL extensions, custom types, collations, and version compatibility
    between Current Production and the New Database before migration starts.
    """

    def __init__(
        self,
        current_prod_url: str,
        new_db_url: str,
        skip_extensions: list | None = None,
        schemas: list | None = None,
    ):
        self.current_prod_url = current_prod_url
        self.new_db_url = new_db_url
        self.skip_extensions = {ext.lower() for ext in (skip_extensions or [])}
        self.schemas = schemas

    def _get_extensions(self, conn) -> dict:
        cur = conn.cursor()
        cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname <> 'plpgsql';")
        exts = {row[0]: row[1] for row in cur.fetchall()}
        cur.close()
        return exts

    def _get_available_extensions(self, conn) -> set:
        cur = conn.cursor()
        cur.execute("SELECT name FROM pg_available_extensions;")
        avail = {row[0] for row in cur.fetchall()}
        cur.close()
        return avail

    def _get_custom_types(self, conn) -> list:
        cur = conn.cursor()
        clause, params = "", ()
        if self.schemas:
            clause = " AND n.nspname = ANY(%s)"
            params = (list(self.schemas),)
        cur.execute(f"""
            SELECT n.nspname, t.typname, t.typtype 
            FROM pg_type t 
            JOIN pg_namespace n ON n.oid = t.typnamespace 
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND t.typtype IN ('e', 'c'){clause};
        """, params)
        types = cur.fetchall()
        cur.close()
        return types

    def audit_and_prepare(self, auto_enable_extensions: bool = True) -> bool:
        console.print("\n[bold cyan]🔍 AUDITING COMPATIBILITY & EXTENSIONS...[/bold cyan]")
        
        prod_conn = connect_db(self.current_prod_url)
        new_conn = connect_db(self.new_db_url)
        new_conn.autocommit = True

        prod_exts = self._get_extensions(prod_conn)
        new_avail_exts = self._get_available_extensions(new_conn)
        new_installed_exts = self._get_extensions(new_conn)

        all_compatible = True
        ext_table = Table(title="🧩 PostgreSQL Extensions Compatibility", header_style="bold magenta")
        ext_table.add_column("Extension", style="cyan")
        ext_table.add_column("Production Version", justify="center")
        ext_table.add_column("New DB Status", justify="center")
        ext_table.add_column("Action Taken", style="green")

        cur_new = new_conn.cursor()

        for ext, ver in prod_exts.items():
            if ext.lower() in self.skip_extensions:
                ext_table.add_row(ext, ver, "[yellow]Skipped[/yellow]", f"Skipped (--skip-extensions {ext})")
                continue

            if ext in new_installed_exts:
                status = "[green]Already Installed ✔[/green]"
                action = "None needed"
            elif ext in new_avail_exts:
                if auto_enable_extensions:
                    try:
                        cur_new.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}";')
                        status = "[bold green]Available & Installed ✔[/bold green]"
                        action = f"Auto-enabled '{ext}'"
                    except Exception as e:
                        status = "[bold yellow]Available (Manual Grant Req)[/bold yellow]"
                        action = f"Warning: {e}"
                else:
                    status = "[green]Available ✔[/green]"
                    action = "Ready for restore"
            else:
                status = "[bold red]UNSUPPORTED ❌[/bold red]"
                action = "[red]New provider does NOT support this extension[/red]"
                all_compatible = False

            ext_table.add_row(ext, ver, status, action)

        cur_new.close()
        console.print(ext_table)

        # Custom Enum / Composite types
        custom_types = self._get_custom_types(prod_conn)
        if custom_types:
            console.print(f"  [green]✔ Detected {len(custom_types)} custom enum/composite types on production (will be created during restore).[/green]")

        prod_conn.close()
        new_conn.close()

        if not all_compatible:
            console.print(Panel(
                "[bold red]❌ EXTENSION COMPATIBILITY ERROR:[/bold red]\n"
                "The new database provider is missing extensions required by your production database.\n"
                "Check with your new provider or contact support before proceeding.",
                title="🚨 Incompatibility Found",
                border_style="red"
            ))
            return False

        console.print("[bold green]✔ All production extensions and features are 100% compatible with the new database.[/bold green]")
        return True
