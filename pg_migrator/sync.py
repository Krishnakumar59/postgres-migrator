import time
import psycopg2.extensions
import psycopg2.extras
from .db import connect as connect_db
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


def _json_columns(description) -> set:
    """Indexes of json/jsonb columns, which psycopg2 hands back as dict/list."""
    json_cols = set()
    for i, desc in enumerate(description):
        caster = psycopg2.extensions.string_types.get(desc.type_code)
        if caster is not None and caster.name.startswith("JSON"):
            json_cols.add(i)
    return json_cols


class DeltaSyncEngine:
    """
    Incremental / Delta Catch-Up Sync Engine.
    Identifies missing/new rows created on Production and syncs ONLY the delta
    without re-dumping or re-copying the entire database from scratch.
    """

    def __init__(self, current_prod_url: str, new_db_url: str):
        self.current_prod_url = current_prod_url
        self.new_db_url = new_db_url

    def _get_tables_with_pk(self, conn) -> list:
        cur = conn.cursor()
        cur.execute("""
            SELECT tc.table_schema, tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY tc.table_name;
        """)
        tables = cur.fetchall()
        cur.close()
        return tables

    def _get_foreign_keys(self, conn) -> dict:
        cur = conn.cursor()
        cur.execute("""
            SELECT child_ns.nspname, child.relname, parent_ns.nspname, parent.relname
            FROM pg_constraint con
            JOIN pg_class child ON child.oid = con.conrelid
            JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
            JOIN pg_class parent ON parent.oid = con.confrelid
            JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
            WHERE con.contype = 'f';
        """)
        deps = {}
        for child_schema, child, parent_schema, parent in cur.fetchall():
            deps.setdefault((child_schema, child), set()).add((parent_schema, parent))
        cur.close()
        return deps

    def _sort_by_dependencies(self, conn, tables: list) -> list:
        """Return tables with referenced tables first, so FK inserts don't fail."""
        deps = self._get_foreign_keys(conn)
        pending = {(schema, table): (schema, table, pk) for schema, table, pk in tables}
        ordered = []
        while pending:
            ready = sorted(
                name for name in pending
                if not (deps.get(name, set()) & pending.keys())
            )
            if not ready:  # circular references - fall back to a stable order
                ready = sorted(pending)
            for name in ready:
                ordered.append(pending.pop(name))
        return ordered

    def sync_missing_data(self, batch_size: int = 1000) -> dict:
        console.print(Panel.fit(
            "[bold cyan]🔄 DELTA / INCREMENTAL CATCH-UP SYNC[/bold cyan]\n"
            "[dim]Detecting and syncing ONLY missing/new records without full re-dump[/dim]",
            border_style="cyan"
        ))

        prod_conn = connect_db(self.current_prod_url)
        new_conn = connect_db(self.new_db_url)
        new_conn.autocommit = True

        tables_with_pk = self._get_tables_with_pk(prod_conn)
        tables_with_pk = self._sort_by_dependencies(prod_conn, tables_with_pk)
        sync_results = []
        total_rows_synced = 0

        for schema, table, pk_col in tables_with_pk:
            full_table = f'"{schema}"."{table}"'
            
            cur_prod = prod_conn.cursor()
            cur_new = new_conn.cursor()

            # 1. Get counts
            cur_prod.execute(f"SELECT count(*) FROM {full_table};")
            prod_count = cur_prod.fetchone()[0]

            cur_new.execute(f"SELECT count(*) FROM {full_table};")
            new_count = cur_new.fetchone()[0]

            diff = prod_count - new_count

            if diff <= 0:
                # Up to date
                cur_prod.close()
                cur_new.close()
                continue

            console.print(f"  ⚡ Table [cyan]{schema}.{table}[/cyan]: Missing [bold yellow]{diff:,} rows[/bold yellow]. Syncing delta...")

            # 2. Find max PK on new DB
            cur_new.execute(f'SELECT coalesce(max("{pk_col}"), 0) FROM {full_table};')
            max_new_pk = cur_new.fetchone()[0]

            # 3. Stream missing rows from Production
            cur_prod.execute(f"""
                SELECT * FROM {full_table} 
                WHERE "{pk_col}" > %s 
                ORDER BY "{pk_col}" ASC;
            """, (max_new_pk,))

            columns = [desc[0] for desc in cur_prod.description]
            col_names = ', '.join([f'"{c}"' for c in columns])
            placeholders = ', '.join(['%s'] * len(columns))
            json_cols = _json_columns(cur_prod.description)
            
            insert_query = f"""
                INSERT INTO {full_table} ({col_names}) 
                VALUES ({placeholders})
                ON CONFLICT ("{pk_col}") DO NOTHING;
            """

            synced_for_table = 0
            while True:
                rows = cur_prod.fetchmany(batch_size)
                if not rows:
                    break
                if json_cols:
                    rows = [
                        tuple(
                            psycopg2.extras.Json(value)
                            if i in json_cols and value is not None
                            else value
                            for i, value in enumerate(row)
                        )
                        for row in rows
                    ]
                psycopg2.extras.execute_batch(cur_new, insert_query, rows)
                synced_for_table += len(rows)

            # 4. Resync auto-increment sequences for this table
            try:
                cur_new.execute(f"""
                    SELECT pg_get_serial_sequence('{schema}.{table}', '{pk_col}');
                """)
                seq_name = cur_new.fetchone()[0]
                if seq_name:
                    cur_new.execute(f"""
                        SELECT setval('{seq_name}', coalesce(max("{pk_col}"), 1)) FROM {full_table};
                    """)
            except Exception:
                pass

            cur_prod.close()
            cur_new.close()

            total_rows_synced += synced_for_table
            sync_results.append({
                "table": f"{schema}.{table}",
                "missing": diff,
                "synced": synced_for_table
            })
            console.print(f"    [green]✔ Synced {synced_for_table:,} missing rows into {schema}.{table} & aligned sequences.[/green]")

        prod_conn.close()
        new_conn.close()

        if total_rows_synced > 0:
            console.print(Panel(
                f"[bold green]🎉 DELTA CATCH-UP SYNC COMPLETED SUCCESSFULLY[/bold green]\n\n"
                f"• Tables Updated: [cyan]{len(sync_results)} tables[/cyan]\n"
                f"• Total Delta Records Synced: [bold green]{total_rows_synced:,} rows[/bold green]\n"
                f"• Sequences Resynchronized.",
                title="✔ Delta Sync Success",
                border_style="green"
            ))
        else:
            console.print("[green]✔ All tables are already fully up to date. Zero missing rows.[/green]")

        return {
            "tables_synced": sync_results,
            "total_rows_synced": total_rows_synced
        }
