import time
import statistics
from .db import connect as connect_db
from rich.console import Console
from rich.table import Table

console = Console()

class PerformanceComparator:
    """
    Runs automated side-by-side benchmarks between Current Production and New Database
    to measure network RTT, SSL handshake, read latency, and query speed.
    """

    def __init__(self, current_prod_url: str, new_db_url: str):
        self.current_prod_url = current_prod_url
        self.new_db_url = new_db_url

    def _benchmark_instance(self, db_url: str, name: str, n_samples: int = 25) -> dict:
        console.print(f"  Benchmarking [cyan]{name}[/cyan] ({n_samples} query cycles)...")
        
        # 1. Connection Handshake Time
        conn_times = []
        for _ in range(8):
            t0 = time.perf_counter()
            conn = connect_db(db_url)
            t1 = time.perf_counter()
            conn_times.append((t1 - t0) * 1000)
            conn.close()

        # 2. Query RTT (SELECT 1)
        conn = connect_db(db_url)
        cur = conn.cursor()
        query_times = []
        for _ in range(n_samples):
            t0 = time.perf_counter()
            cur.execute("SELECT 1;")
            cur.fetchone()
            t1 = time.perf_counter()
            query_times.append((t1 - t0) * 1000)

        # 3. Server version and config
        cur.execute("SELECT version();")
        version = cur.fetchone()[0].split()[1]
        
        cur.close()
        conn.close()

        return {
            "version": version,
            "conn_p50_ms": statistics.median(conn_times),
            "rtt_p50_ms": statistics.median(query_times),
            "rtt_p95_ms": sorted(query_times)[int(len(query_times) * 0.95)],
            "rtt_min_ms": min(query_times),
            "est_single_tps": 1000.0 / statistics.median(query_times)
        }

    def compare(self) -> dict:
        console.print("\n[bold cyan]⚡ RUNNING SIDE-BY-SIDE PERFORMANCE COMPARISON...[/bold cyan]")
        
        prod_res = self._benchmark_instance(self.current_prod_url, "CURRENT PRODUCTION")
        new_res = self._benchmark_instance(self.new_db_url, "NEW DATABASE")

        table = Table(title="🏁 Provider Performance Comparison", header_style="bold magenta")
        table.add_column("Metric / Dimension", style="cyan")
        table.add_column("Current Production", justify="right", style="white")
        table.add_column("New Database", justify="right", style="white")
        table.add_column("Difference / Winner", justify="center")

        def format_diff(prod_val, new_val, lower_is_better=True, unit="ms"):
            if lower_is_better:
                diff = prod_val - new_val
                pct = (diff / prod_val) * 100 if prod_val else 0
                if diff > 0:
                    return f"[bold green]{pct:+.1f}% FASTER ✔ (New DB)[/bold green]"
                elif diff < 0:
                    return f"[bold yellow]{abs(pct):.1f}% Slower (Prod faster)[/bold yellow]"
                else:
                    return "Identical"
            else:
                diff = new_val - prod_val
                pct = (diff / prod_val) * 100 if prod_val else 0
                if diff > 0:
                    return f"[bold green]{pct:+.1f}% HIGHER ✔ (New DB)[/bold green]"
                elif diff < 0:
                    return f"[bold yellow]{abs(pct):.1f}% Lower[/bold yellow]"
                else:
                    return "Identical"

        table.add_row("PostgreSQL Version", f"v{prod_res['version']}", f"v{new_res['version']}", "N/A")
        table.add_row(
            "SSL Handshake (p50)",
            f"{prod_res['conn_p50_ms']:.1f} ms",
            f"{new_res['conn_p50_ms']:.1f} ms",
            format_diff(prod_res['conn_p50_ms'], new_res['conn_p50_ms'], lower_is_better=True)
        )
        table.add_row(
            "Query RTT Ping (p50)",
            f"{prod_res['rtt_p50_ms']:.1f} ms",
            f"{new_res['rtt_p50_ms']:.1f} ms",
            format_diff(prod_res['rtt_p50_ms'], new_res['rtt_p50_ms'], lower_is_better=True)
        )
        table.add_row(
            "Query Latency (p95)",
            f"{prod_res['rtt_p95_ms']:.1f} ms",
            f"{new_res['rtt_p95_ms']:.1f} ms",
            format_diff(prod_res['rtt_p95_ms'], new_res['rtt_p95_ms'], lower_is_better=True)
        )
        table.add_row(
            "Single-Conn Read Capacity (est)",
            f"{prod_res['est_single_tps']:.1f} QPS",
            f"{new_res['est_single_tps']:.1f} QPS",
            format_diff(prod_res['est_single_tps'], new_res['est_single_tps'], lower_is_better=False, unit="QPS")
        )

        console.print(table)
        return {"prod": prod_res, "new": new_res}
