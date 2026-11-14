import os
import sys
import time
import asyncio
import json
import statistics
import asyncpg
from pg_migrator.db import asyncpg_kwargs, connect as connect_db
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from tabulate import tabulate

load_dotenv()

TARGET_DB_URL = os.getenv("NEW_DATABASE_URL")
if not TARGET_DB_URL:
    print("Error: NEW_DATABASE_URL environment variable not found in .env")
    sys.exit(1)

def get_db_info():
    print("=" * 60)
    print("1. DATABASE INFORMATION & CONFIGURATION DISCOVERY")
    print("=" * 60)
    conn = connect_db(TARGET_DB_URL)
    cur = conn.cursor()
    
    cur.execute("SELECT version();")
    version = cur.fetchone()[0]
    print(f"PostgreSQL Version: {version}\n")

    settings_to_check = [
        "max_connections",
        "shared_buffers",
        "effective_cache_size",
        "maintenance_work_mem",
        "work_mem",
        "wal_buffers",
        "checkpoint_completion_target",
        "max_parallel_workers",
        "max_parallel_workers_per_gather",
        "random_page_cost",
        "effective_io_concurrency",
        "synchronous_commit"
    ]
    
    config_data = {}
    table_rows = []
    for setting in settings_to_check:
        try:
            cur.execute(f"SHOW {setting};")
            val = cur.fetchone()[0]
            config_data[setting] = val
            table_rows.append([setting, val])
        except Exception as e:
            config_data[setting] = f"Error: {e}"
            table_rows.append([setting, f"Error: {e}"])
            
    print(tabulate(table_rows, headers=["Parameter", "Configured Value"], tablefmt="grid"))
    
    cur.execute("SELECT pg_database_size(current_database()) / (1024.0 * 1024.0) as size_mb;")
    db_size = float(cur.fetchone()[0])
    print(f"\nCurrent Database Size: {db_size:.2f} MB")
    
    cur.execute("SELECT count(*) FROM pg_stat_activity;")
    active_conn = cur.fetchone()[0]
    print(f"Current Active Connections: {active_conn}")
    
    cur.close()
    conn.close()
    return config_data, version, db_size

def test_connection_and_rtt(n_samples=40):
    print("\n" + "=" * 60)
    print("2. NETWORK RTT & CONNECTION OVERHEAD BENCHMARK")
    print("=" * 60)
    
    conn_times = []
    print(f"Measuring raw TCP/SSL connection establishment time ({n_samples // 2} samples)...")
    for _ in range(n_samples // 2):
        t0 = time.perf_counter()
        conn = connect_db(TARGET_DB_URL)
        t1 = time.perf_counter()
        conn_times.append((t1 - t0) * 1000)
        conn.close()
        
    conn = connect_db(TARGET_DB_URL)
    cur = conn.cursor()
    query_times = []
    print(f"Measuring single-query RTT (SELECT 1) over established connection ({n_samples} samples)...")
    for _ in range(n_samples):
        t0 = time.perf_counter()
        cur.execute("SELECT 1;")
        cur.fetchone()
        t1 = time.perf_counter()
        query_times.append((t1 - t0) * 1000)
    cur.close()
    conn.close()
    
    rtt_stats = {
        "conn_mean_ms": statistics.mean(conn_times),
        "conn_p50_ms": statistics.median(conn_times),
        "conn_p95_ms": sorted(conn_times)[int(len(conn_times) * 0.95)],
        "rtt_mean_ms": statistics.mean(query_times),
        "rtt_p50_ms": statistics.median(query_times),
        "rtt_p95_ms": sorted(query_times)[int(len(query_times) * 0.95)],
        "rtt_p99_ms": sorted(query_times)[int(len(query_times) * 0.99)],
        "rtt_min_ms": min(query_times),
        "rtt_max_ms": max(query_times),
    }
    
    table = [
        ["Connection Handshake (SSL)", f"{rtt_stats['conn_mean_ms']:.2f} ms", f"{rtt_stats['conn_p50_ms']:.2f} ms", f"{rtt_stats['conn_p95_ms']:.2f} ms", "N/A"],
        ["Query RTT (SELECT 1)", f"{rtt_stats['rtt_mean_ms']:.2f} ms", f"{rtt_stats['rtt_p50_ms']:.2f} ms", f"{rtt_stats['rtt_p95_ms']:.2f} ms", f"{rtt_stats['rtt_p99_ms']:.2f} ms"]
    ]
    print(tabulate(table, headers=["Metric", "Mean", "Median (p50)", "p95", "p99"], tablefmt="grid"))
    return rtt_stats

def prepare_tables(scale_rows=100000):
    print("\n" + "=" * 60)
    print(f"3. PREPARING BENCHMARK SCHEMA & DATA ({scale_rows:,} rows)")
    print("=" * 60)
    conn = connect_db(TARGET_DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    cur.execute("DROP TABLE IF EXISTS _benchmark_oltp;")
    cur.execute("DROP TABLE IF EXISTS _benchmark_analytics;")
    
    print("Creating benchmark tables with indexes...")
    cur.execute("""
        CREATE TABLE _benchmark_oltp (
            id SERIAL PRIMARY KEY,
            account_id INT NOT NULL,
            balance NUMERIC(12, 2) NOT NULL,
            payload TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX idx_oltp_account ON _benchmark_oltp(account_id);
    """)
    
    cur.execute("""
        CREATE TABLE _benchmark_analytics (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            score FLOAT NOT NULL,
            metadata JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX idx_analytics_user ON _benchmark_analytics(user_id);
        CREATE INDEX idx_analytics_event ON _benchmark_analytics(event_type);
        CREATE INDEX idx_analytics_metadata_gin ON _benchmark_analytics USING gin(metadata);
    """)
    
    print("Populating initial OLTP seed data...")
    t0 = time.perf_counter()
    cur.execute(f"""
        INSERT INTO _benchmark_oltp (account_id, balance, payload)
        SELECT 
            (random() * 100000)::int,
            (random() * 10000)::numeric(12,2),
            repeat(md5(random()::text), 3)
        FROM generate_series(1, {scale_rows});
    """)
    t1 = time.perf_counter()
    print(f"Inserted {scale_rows:,} seed rows in {t1 - t0:.2f}s ({scale_rows / (t1 - t0):.1f} rows/s)")
    
    cur.close()
    conn.close()

async def benchmark_bulk_ingestion(batch_sizes=[100, 1000, 5000, 10000], total_per_test=50000):
    print("\n" + "=" * 60)
    print("4. BULK INGESTION THROUGHPUT & SCALING CAPACITY")
    print("=" * 60)
    
    pool = await asyncpg.create_pool(TARGET_DB_URL, **asyncpg_kwargs(TARGET_DB_URL), min_size=2, max_size=4)
    ingestion_results = []
    
    for batch_size in batch_sizes:
        records = [
            (
                i % 10000,
                f"event_type_{i % 10}",
                float(i * 1.5 % 100),
                json.dumps({"browser": f"chrome_{i%5}", "os": f"linux_{i%3}", "country": "US" if i % 2 == 0 else "EU", "session_id": f"sess_{i}"})
            )
            for i in range(batch_size)
        ]
        
        num_batches = total_per_test // batch_size
        total_rows = num_batches * batch_size
        
        t0 = time.perf_counter()
        async with pool.acquire() as conn:
            for _ in range(num_batches):
                await conn.executemany(
                    """
                    INSERT INTO _benchmark_analytics (user_id, event_type, score, metadata)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    records
                )
        t1 = time.perf_counter()
        elapsed = t1 - t0
        rate = total_rows / elapsed
        ingestion_results.append({
            "method": f"Multi-Row Batch ({batch_size:,}/batch)",
            "batch_size": batch_size,
            "total_rows": total_rows,
            "elapsed_s": round(elapsed, 2),
            "rows_per_sec": round(rate, 1),
            "est_mb_per_sec": round((rate * 250) / (1024 * 1024), 2)
        })
        print(f"Batch Size {batch_size:>5}: Ingested {total_rows:,} rows in {elapsed:.2f}s -> {rate:,.1f} rows/sec")
    
    # Fast COPY protocol
    print("\nTesting PostgreSQL Binary COPY protocol ingestion...")
    copy_records = [
        (
            i % 10000,
            f"copy_event_{i % 10}",
            float(i * 1.5 % 100),
            json.dumps({"browser": f"firefox_{i%5}", "tag": "bulk_copy"})
        )
        for i in range(100000)
    ]
    t0 = time.perf_counter()
    async with pool.acquire() as conn:
        await conn.copy_records_to_table(
            '_benchmark_analytics',
            records=copy_records,
            columns=['user_id', 'event_type', 'score', 'metadata']
        )
    t1 = time.perf_counter()
    copy_rate = 100000 / (t1 - t0)
    ingestion_results.append({
        "method": "Fast Binary COPY (100k rows)",
        "batch_size": 100000,
        "total_rows": 100000,
        "elapsed_s": round(t1 - t0, 2),
        "rows_per_sec": round(copy_rate, 1),
        "est_mb_per_sec": round((copy_rate * 250) / (1024 * 1024), 2)
    })
    print(f"COPY Ingestion: 100,000 rows in {t1 - t0:.2f}s -> {copy_rate:,.1f} rows/sec")

    tab_data = [[r["method"], f"{r['total_rows']:,}", f"{r['elapsed_s']:.2f} s", f"{r['rows_per_sec']:,.1f} rows/s", f"{r['est_mb_per_sec']:.2f} MB/s"] for r in ingestion_results]
    print("\n" + tabulate(tab_data, headers=["Method", "Total Rows", "Elapsed", "Throughput", "Bandwidth (est)"], tablefmt="grid"))
    await pool.close()
    return ingestion_results

async def worker_oltp(pool, workload, duration_secs, latencies, stop_event):
    import random
    while not stop_event.is_set():
        acc_id = random.randint(1, 100000)
        t0 = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                if workload == "read_only":
                    await conn.fetchrow(
                        "SELECT id, account_id, balance, payload FROM _benchmark_oltp WHERE account_id = $1 LIMIT 1;",
                        acc_id
                    )
                elif workload == "write_only":
                    await conn.execute(
                        "UPDATE _benchmark_oltp SET balance = balance + $1, updated_at = NOW() WHERE account_id = $2;",
                        random.uniform(-50, 50), acc_id
                    )
                elif workload == "mixed_oltp":
                    async with conn.transaction():
                        row = await conn.fetchrow("SELECT balance FROM _benchmark_oltp WHERE account_id = $1 FOR UPDATE LIMIT 1;", acc_id)
                        if row:
                            await conn.execute("UPDATE _benchmark_oltp SET balance = balance + 10 WHERE account_id = $1;", acc_id)
                        else:
                            await conn.execute("INSERT INTO _benchmark_oltp (account_id, balance, payload) VALUES ($1, 100.0, 'new');", acc_id)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        except Exception:
            pass

async def run_concurrency_suite(concurrency_levels=[1, 2, 4, 8, 12, 16], duration_per_test=6):
    print("\n" + "=" * 60)
    print("5. CONCURRENCY & TRANSACTION THROUGHPUT (OLTP)")
    print("=" * 60)
    
    results = {}
    workloads = ["read_only", "write_only", "mixed_oltp"]
    
    for wl in workloads:
        print(f"\n--- Running Workload: {wl.upper()} ---")
        wl_results = []
        for c in concurrency_levels:
            try:
                pool = await asyncpg.create_pool(TARGET_DB_URL, **asyncpg_kwargs(TARGET_DB_URL), min_size=c, max_size=c, command_timeout=10)
            except Exception as e:
                print(f"Concurrency {c:>2} | Skipped (Could not establish pool: {e})")
                continue

            latencies = []
            stop_event = asyncio.Event()
            
            tasks = [
                asyncio.create_task(worker_oltp(pool, wl, duration_per_test, latencies, stop_event))
                for _ in range(c)
            ]
            
            await asyncio.sleep(duration_per_test)
            stop_event.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await pool.close()
            
            if latencies:
                total_tx = len(latencies)
                tps = total_tx / duration_per_test
                mean_lat = statistics.mean(latencies)
                p50 = statistics.median(latencies)
                p95 = sorted(latencies)[int(len(latencies) * 0.95)]
                p99 = sorted(latencies)[int(len(latencies) * 0.99)]
            else:
                total_tx, tps, mean_lat, p50, p95, p99 = 0, 0, 0, 0, 0, 0
                
            wl_results.append({
                "concurrency": c,
                "tps": round(tps, 1),
                "total_tx": total_tx,
                "mean_ms": round(mean_lat, 2),
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2)
            })
            print(f"Concurrency {c:>2} | TPS: {tps:>8.1f} | Latency avg: {mean_lat:>6.2f}ms | p50: {p50:>6.2f}ms | p95: {p95:>6.2f}ms | p99: {p99:>6.2f}ms")
            
        results[wl] = wl_results
        
    return results

async def benchmark_complex_queries():
    print("\n" + "=" * 60)
    print("6. COMPLEX QUERIES & INDEX PERFORMANCE BENCHMARK")
    print("=" * 60)
    
    conn = await asyncpg.connect(TARGET_DB_URL, **asyncpg_kwargs(TARGET_DB_URL))
    
    queries = [
        (
            "Point Index Scan (B-Tree)",
            "SELECT * FROM _benchmark_analytics WHERE user_id = 4200 LIMIT 10;"
        ),
        (
            "Aggregation & Grouping (150k+ rows)",
            "SELECT event_type, count(*), avg(score), min(score), max(score) FROM _benchmark_analytics GROUP BY event_type;"
        ),
        (
            "JSONB GIN Index Query (Metadata filter)",
            "SELECT count(*) FROM _benchmark_analytics WHERE metadata @> '{\"country\": \"US\"}';"
        ),
        (
            "JSONB Top-K Search with Sorting",
            "SELECT id, user_id, score, metadata->>'browser' FROM _benchmark_analytics WHERE score > 50.0 ORDER BY score DESC LIMIT 100;"
        ),
        (
            "Relational JOIN (OLTP x Analytics)",
            """
            SELECT a.event_type, count(o.id), avg(o.balance)
            FROM _benchmark_analytics a
            JOIN _benchmark_oltp o ON o.account_id = a.user_id
            GROUP BY a.event_type;
            """
        ),
        (
            "Full Table Scan (Sequential scan + Agg)",
            "SELECT sum(balance), avg(balance), stddev(balance) FROM _benchmark_oltp;"
        )
    ]
    
    query_bench = []
    for name, sql in queries:
        latencies = []
        for _ in range(10):
            t0 = time.perf_counter()
            await conn.fetch(sql)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
            
        mean_ms = statistics.mean(latencies)
        p50_ms = statistics.median(latencies)
        min_ms = min(latencies)
        max_ms = max(latencies)
        
        query_bench.append({
            "name": name,
            "mean_ms": round(mean_ms, 2),
            "p50_ms": round(p50_ms, 2),
            "min_ms": round(min_ms, 2),
            "max_ms": round(max_ms, 2)
        })
        print(f"{name:<42} -> Avg: {mean_ms:>7.2f} ms (p50: {p50_ms:>7.2f} ms, min: {min_ms:>7.2f} ms)")
        
    tab_data = [[q["name"], f"{q['mean_ms']} ms", f"{q['p50_ms']} ms", f"{q['min_ms']} ms", f"{q['max_ms']} ms"] for q in query_bench]
    print("\n" + tabulate(tab_data, headers=["Query Type", "Avg Latency", "p50 Latency", "Min Latency", "Max Latency"], tablefmt="grid"))
    await conn.close()
    return query_bench

async def test_max_connections_limit():
    print("\n" + "=" * 60)
    print("7. CONNECTION POOL / MAX CONCURRENCY STRESS TEST")
    print("=" * 60)
    connections = []
    max_tested = 30
    success_count = 0
    failure_reason = None
    
    print(f"Testing client connection saturation up to {max_tested} simultaneous connections...")
    try:
        for i in range(1, max_tested + 1):
            try:
                c = await asyncpg.connect(TARGET_DB_URL, **asyncpg_kwargs(TARGET_DB_URL), timeout=4)
                connections.append(c)
                success_count += 1
                if i % 5 == 0 or i == max_tested:
                    print(f"  Successfully opened {i} concurrent connections...")
            except Exception as e:
                failure_reason = str(e)
                print(f"  Reached connection limit / error at connection #{i}: {e}")
                break
    finally:
        print(f"Closing {len(connections)} test connections...")
        for c in connections:
            try:
                await c.close()
            except Exception:
                pass
            
    print(f"\nMax sustained concurrent connections available for client: {success_count}")
    return {"max_sustained": success_count, "failure_reason": failure_reason}

def generate_charts(concurrency_results, ingestion_results):
    print("\nGenerating benchmark visual charts...")
    plt.figure(figsize=(14, 5))
    
    # 1. TPS vs Concurrency
    plt.subplot(1, 2, 1)
    for wl, data in concurrency_results.items():
        c_vals = [d["concurrency"] for d in data]
        tps_vals = [d["tps"] for d in data]
        label_name = wl.replace("_", " ").title()
        plt.plot(c_vals, tps_vals, marker='o', linewidth=2, label=label_name)
    plt.title("PostgreSQL Throughput (TPS) vs Concurrency", fontsize=12, fontweight='bold')
    plt.xlabel("Concurrent Clients", fontsize=10)
    plt.ylabel("Transactions Per Second (TPS)", fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.legend()
    
    # 2. Latency vs Concurrency
    plt.subplot(1, 2, 2)
    for wl, data in concurrency_results.items():
        c_vals = [d["concurrency"] for d in data]
        lat_vals = [d["mean_ms"] for d in data]
        label_name = wl.replace("_", " ").title()
        plt.plot(c_vals, lat_vals, marker='s', linewidth=2, label=label_name)
    plt.title("Transaction Latency (ms) vs Concurrency", fontsize=12, fontweight='bold')
    plt.xlabel("Concurrent Clients", fontsize=10)
    plt.ylabel("Average Latency (ms)", fontsize=10)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.legend()
    
    plt.tight_layout()
    plt.savefig("benchmark_concurrency.png", dpi=150)
    plt.close()
    
    # Ingestion chart
    plt.figure(figsize=(8, 4.5))
    methods = [r["method"] for r in ingestion_results]
    rates = [r["rows_per_sec"] for r in ingestion_results]
    colors = ['#4C72B0', '#55A868', '#C44E52', '#8172B2', '#CCB974']
    
    bars = plt.barh(methods, rates, color=colors[:len(methods)])
    plt.title("Bulk Ingestion Throughput (Rows/Second)", fontsize=12, fontweight='bold')
    plt.xlabel("Rows per Second", fontsize=10)
    for bar in bars:
        width = bar.get_width()
        plt.text(width + max(rates)*0.01, bar.get_y() + bar.get_height()/2, f'{width:,.0f} r/s', ha='left', va='center', fontsize=9)
    plt.xlim(0, max(rates) * 1.2)
    plt.grid(axis='x', linestyle='--', alpha=0.6)
    plt.tight_layout()
    plt.savefig("benchmark_ingestion.png", dpi=150)
    plt.close()
    print("Charts saved as benchmark_concurrency.png and benchmark_ingestion.png")

def cleanup():
    print("\n" + "=" * 60)
    print("8. CLEANUP")
    print("=" * 60)
    conn = connect_db(TARGET_DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    print("Dropping benchmark tables (_benchmark_oltp, _benchmark_analytics)...")
    cur.execute("DROP TABLE IF EXISTS _benchmark_oltp;")
    cur.execute("DROP TABLE IF EXISTS _benchmark_analytics;")
    cur.close()
    conn.close()
    print("Database is completely cleaned and ready.")

async def main():
    print("STARTING COMPREHENSIVE POSTGRESQL BENCHMARK SUITE")
    print("Target: NEW DATABASE (NEW_DATABASE_URL)")
    print("=" * 60)
    
    config_data, version, db_size = get_db_info()
    rtt_stats = test_connection_and_rtt()
    prepare_tables(scale_rows=100000)
    ingestion_results = await benchmark_bulk_ingestion()
    concurrency_results = await run_concurrency_suite()
    query_bench = await benchmark_complex_queries()
    max_conn_sustained = await test_max_connections_limit()
    
    generate_charts(concurrency_results, ingestion_results)
    
    all_results = {
        "version": version,
        "config": config_data,
        "initial_db_size_mb": db_size,
        "rtt_stats": rtt_stats,
        "ingestion_results": ingestion_results,
        "concurrency_results": concurrency_results,
        "query_bench": query_bench,
        "max_conn_sustained": max_conn_sustained
    }
    with open("benchmark_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("\nRaw benchmark results written to benchmark_results.json")
    
    cleanup()
    print("\n============================================================")
    print("BENCHMARK COMPLETED SUCCESSFULLY!")
    print("============================================================")

if __name__ == "__main__":
    asyncio.run(main())
