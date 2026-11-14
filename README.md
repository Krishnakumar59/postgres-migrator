<div align="center">

# 🐘 Postgres Migrator & Benchmark Suite
### *Zero-Downtime, Disaster-Proof PostgreSQL Provider Migration & Benchmarking Toolkit*

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-12%20--%2017-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Safety: Guaranteed](https://img.shields.io/badge/Safety-Disaster--Proof-brightgreen.svg)](#-zero-tolerance-safety-guardrails)
[![Repository](https://img.shields.io/badge/GitHub-Krishnakumar59%2Fpostgres--migrator-181717.svg?logo=github)](https://github.com/Krishnakumar59/postgres-migrator)

**Effortlessly migrate live PostgreSQL databases across any cloud provider (AWS RDS, Google Cloud SQL, Supabase, Neon, DigitalOcean, Aiven, Hetzner, Railway, self-hosted) with multi-channel alerts (Discord/Slack/Telegram), smart auto-sync detection, anti-corruption verification, atomic snapshots, and 1-click emergency rollback.**

---

</div>

## 📌 Table of Contents

- [Overview & Why Postgres Migrator?](#-overview--why-postgres-migrator)
- [Architecture & End-to-End Workflow](#-architecture--end-to-end-workflow)
- [Core Features](#-core-features)
  - [🛡️ Zero-Tolerance Safety Guardrails](#️-zero-tolerance-safety-guardrails)
  - [💡 Smart Idempotency & Sync Detection](#-smart-idempotency--sync-detection)
  - [🔄 Incremental Delta Catch-Up Sync](#-incremental-delta-catch-up-sync)
  - [🔔 Multi-Channel Notification Engine](#-multi-channel-notification-engine)
  - [⚙️ Extension & Compatibility Auditor](#️-extension--compatibility-auditor)
  - [🔍 Data Integrity & Sequence Auditor](#-data-integrity--sequence-auditor)
  - [⚡ Side-by-Side Performance Comparator](#-side-by-side-performance-comparator)
  - [🔄 Automated `.env` Switcher](#-automated-env-switcher)
  - [🚨 Disaster Recovery: 1-Click Rollback](#-disaster-recovery-1-click-rollback)
- [Quickstart Guide](#-quickstart-guide)
  - [1. Clone Repository](#1-clone-repository)
  - [2. Create Virtual Environment & Install](#2-create-virtual-environment--install)
  - [3. Configure Environment Variables](#3-configure-environment-variables)
- [CLI Command Reference](#-cli-command-reference)
  - [Migration CLI (`migrate.py` / `pg-migrator`)](#migration-cli-migratepy--pg-migrator)
  - [Emergency Rollback CLI (`rollback.py`)](#emergency-rollback-cli-rollbackpy)
  - [Benchmark Suite CLI (`run_benchmark.py`)](#benchmark-suite-cli-run_benchmarkpy)
- [Step-by-Step Production Cutover Runbook](#-step-by-step-production-cutover-runbook)
- [Python SDK / Programmatic API](#-python-sdk--programmatic-api)
- [PostgreSQL Benchmark Suite](#-postgresql-benchmark-suite)
- [Contributing & License](#-contributing--license)

---

## 🌟 Overview & Why Postgres Migrator?

Switching PostgreSQL cloud providers or upgrading major database versions is often fraught with risk: corrupted dumps, schema drift, missed sequences, long downtimes, and missing data written during cutover.

**Postgres Migrator** is designed from the ground up for site reliability engineers (SREs), DevOps teams, and database administrators who need **guaranteed data safety**, **idempotency**, and **zero surprises**.

```
┌─────────────────────────┐       Zero-Downtime Safe Pipeline       ┌─────────────────────────┐
│  Source Production DB   │ ──────────────────────────────────────► │  Target Destination DB  │
│ (AWS / Supabase / Neon) │       • Dual Pre-flight Backups         │ (GCP / Hetzner / Self)  │
│     [ READ-ONLY ]       │       • Anti-Corruption TOC Validation  │     [ VERIFIED ]        │
└─────────────────────────┘       • Incremental Delta Catch-Up      └─────────────────────────┘
                                  • Multi-Channel Real-time Alerts
```

---

## 🏗️ Architecture & End-to-End Workflow

```mermaid
flowchart TD
    A[Start Migration CLI] --> B[Pre-Flight Connection & Version Validation]
    B --> C{Databases Identical?}
    C -- Yes --> D[🎉 Skip Dump: Databases Already Synchronized!]
    C -- No --> E[Audit Extensions & Compatibility]
    E --> F[Create Dual Compressed Snapshots in ./backups/]
    F --> G[Execute pg_dump with Temporary Staging]
    G --> H[Validate Archive Header & TOC Dataset]
    H --> I[Clean/Verify Target & Run pg_restore with Multi-workers]
    I --> J[Run Incremental Delta Catch-Up Sync]
    J --> K[Audit Table Rows, Sequences & Checksums]
    K --> L[Run Side-by-Side Performance Comparison]
    L --> M[Optionally Switch Active .env Configuration]
    M --> N[Dispatch Discord / Slack / Telegram / Desktop Notifications]
    N --> O[Cutover Complete 🎉]
```

---

## 🚀 Core Features

### 🛡️ Zero-Tolerance Safety Guardrails

| Guardrail | Description | Protection Level |
| :--- | :--- | :--- |
| **🔒 Read-Only Source Access** | The source production database is strictly treated as read-only. Zero `DROP`, `TRUNCATE`, `ALTER`, or `DELETE` commands are ever executed against it. | 🟢 100% Non-destructive |
| **📦 Dual Pre-Flight Snapshots** | Takes independent, binary-compressed `.dump` snapshots of **both** source and target databases into `./backups/` *before* touching a single byte. | 🟢 Disaster-proof |
| **🧩 Anti-Corruption Validation** | Writes dumps to temporary staging files (`.dump.tmp`) and validates TOC headers via `pg_restore --list` before promoting to `.dump`. | 🟢 Corruption immune |
| **⛔ Target Overwrite Guard** | Refuses to restore onto an unexpectedly populated destination without explicit operator confirmation. | 🟢 Human error guard |
| **🔍 Exact Sequence Synchronization** | Automatically detects and aligns sequence counters (`nextval`/`currval`) across all tables to prevent primary key collisions post-cutover. | 🟢 Schema integrity |

---

### 💡 Smart Idempotency & Sync Detection

*What happens if a migration has already been executed or interrupted?*

Postgres Migrator automatically analyzes both databases during pre-flight:
1. **Already Synced**: If table lists, primary keys, and row counts match 100%, it **skips redundant dump/restore** operations, displays a summary panel, and notifies your team that target is ready.
2. **Partial Drift / New Rows**: If production received new records since the initial dump, it initiates an **Incremental Delta Catch-Up Sync** instead of failing or starting over.

```
╭───────────────────────────────────────────────────────────────────────────────────╮
│ 🎉 ALREADY FULLY SYNCHRONIZED! NO ACTION NEEDED 🎉                                │
│ • Both databases are 100% up-to-date and identical.                               │
│ • All 28 tables and 185,420 rows match perfectly.                                 │
│ • No re-dump or re-migration required.                                            │
│                                                                                   │
│ 👉 Production Cutover Action:                                                     │
│ Your New Database is already fully prepared and ready for traffic cutover!        │
╰───────────────────────────────────────────────────────────────────────────────────╯
```

---

### 🔄 Incremental Delta Catch-Up Sync

During large migrations that take time to transfer, production continues to receive writes. 
The **Delta Sync Engine** identifies and synchronizes **only missing/new primary key records**:
- Analyzes primary keys on source tables.
- Computes missing rows on destination.
- Batch-inserts delta records safely without duplicating data or triggering constraint violations.

```bash
# Run standalone delta catch-up anytime
python migrate.py --sync-missing
```

---

### 🔔 Multi-Channel Notification Engine

Stay up-to-date in real-time across your engineering stack:
- **Discord Webhooks**: Rich color-coded embed cards (🟢 Success, 🟡 Warning/Delta, 🔴 Critical/Halt).
- **Slack Webhooks**: Clean Block Kit messages formatted with table counts, elapsed timings, and host info.
- **Telegram Bot**: Instant mobile push alerts delivered to team channels or private chats.
- **Desktop Notifications**: Native OS popups (`notify-send` on Linux, `osascript` on macOS).

```bash
# Test all notification channels immediately
python migrate.py --test-notification
```

---

### ⚙️ Extension & Compatibility Auditor

Detect compatibility pitfalls between different PostgreSQL versions or hosted providers before attempting migration:
- Inspects installed extensions (`uuid-ossp`, `pgvector`, `postgis`, `pg_stat_statements`, `pgcrypto`, etc.) on source vs. available extensions on target.
- Automatically attempts enabling missing compatible extensions on target.
- Checks major PostgreSQL engine versions (e.g., PG 14 ➔ PG 16).

```bash
python migrate.py --audit-compatibility
```

---

### 🔍 Data Integrity & Sequence Auditor

Post-migration verification ensures complete parity:
- Compares table count and individual row counts across every schema.
- Verifies maximum ID values and sets sequence current values (`setval`) on target.
- Performs MD5 checksum spot-checks on core datasets.

```bash
python migrate.py --verify-only
```

---

### ⚡ Side-by-Side Performance Comparator

Compares latency, query execution times, and throughput of Old Provider vs. New Provider across real workload queries:

```bash
python migrate.py --compare-performance
```

---

### 🚨 Disaster Recovery: 1-Click Rollback

If you ever need to restore your database back to a pre-migration state:
- Select an existing snapshot from an interactive menu or pass the file directly.
- Fully atomic restoration via parallel `pg_restore`.

```bash
python rollback.py
# Or specify a snapshot directly:
python rollback.py --file "./backups/current_production_backup_20260904_140000.dump" --yes
```

---

## ⚡ Quickstart Guide

### 1. Clone Repository

```bash
git clone https://github.com/Krishnakumar59/postgres-migrator.git
cd postgres-migrator
```

### 2. Create Virtual Environment & Install

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Option A: Install dependencies via requirements.txt
pip install -r requirements.txt

# Option B: Install in editable mode (adds `pg-migrator` & `pg-migrate` CLI commands)
pip install -e .
```

### 3. Configure Environment Variables

Copy the template configuration and fill in your connection strings:

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Source database (READ-ONLY)
CURRENT_PRODUCTION_DB_URL=postgres://user:password@live-prod-db.example.com:5432/production_db?sslmode=require

# Target database (DESTINATION)
NEW_DATABASE_URL=postgres://user:password@new-provider-db.example.com:5432/new_db?sslmode=require

# Optional Multi-Channel Alerts
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```

> [!NOTE]
> Ensure PostgreSQL client tools (`pg_dump`, `pg_restore`, `psql`) are installed and available in your system `$PATH`.

---

## 💻 CLI Command Reference

### Migration CLI (`migrate.py` / `pg-migrator`)

| Command | Description |
| :--- | :--- |
| `python migrate.py` | Full safe migration pipeline (pre-flight checks, dual backups, dump, restore, verify, comparator). |
| `python migrate.py --dry-run` | Simulates entire migration without modifying or transferring data. |
| `python migrate.py --sync-missing` | Runs fast Incremental Delta Catch-Up sync for newly created rows. |
| `python migrate.py --audit-compatibility` | Audits version differences and extension availability between databases. |
| `python migrate.py --compare-performance` | Runs side-by-side performance benchmarks (Old vs New provider). |
| `python migrate.py --backup-both` | Takes binary snapshot dumps of **both** databases into `./backups/` only. |
| `python migrate.py --verify-only` | Verifies data parity (row counts, sequences, MD5 fingerprints) only. |
| `python migrate.py --test-notification` | Sends test alerts across Discord, Slack, Telegram, and Desktop notifications. |
| `python migrate.py --jobs 8` | Configures parallel worker threads for `pg_restore` (default: 4). |
| `python migrate.py -p <url> -n <url>` | Pass source (`--current-prod`) and target (`--new-db`) URLs via CLI flags. |

---

### Emergency Rollback CLI (`rollback.py`)

| Command | Description |
| :--- | :--- |
| `python rollback.py` | Launches interactive rollback manager to choose from local `./backups/`. |
| `python rollback.py --file <path>` | Restores a specific compressed `.dump` archive directly. |
| `python rollback.py --db <url>` | Target database URL for rollback (defaults to `CURRENT_PRODUCTION_DB_URL`). |
| `python rollback.py --yes` | Bypasses interactive confirmation prompts. |
| `python rollback.py --jobs 8` | Sets parallel worker count for `pg_restore`. |

---

### Benchmark Suite CLI (`run_benchmark.py`)

Deep diagnostic and stress-testing tool for the **new** PostgreSQL instance:
- Discovers PostgreSQL engine configuration parameters.
- Tests bulk ingestion write speeds with varied batch sizes.
- Measures concurrent query latency, TPS, and percentile distributions (p50, p95, p99).
- Generates high-resolution graphical visualizations and JSON reports.

The benchmark writes real test data and drops it afterwards, so it runs against
the new database only — never against current production.

```bash
# Uses NEW_DATABASE_URL from .env
python run_benchmark.py
```

Generated benchmark output artifacts:
- `benchmark_ingestion.png`: Chart of write speed vs. batch size.
- `benchmark_concurrency.png`: Chart of throughput vs. concurrent client connections.
- `benchmark_results.json`: Machine-readable benchmark metrics.

---

## 📋 Step-by-Step Production Cutover Runbook

```
┌────────────────────────────────────────────────────────────────────────┐
│                        PRODUCTION CUTOVER RUNBOOK                      │
└────────────────────────────────────────────────────────────────────────┘

 [1] Pre-Migration Sanity Checks
     $ python migrate.py --audit-compatibility
     $ python migrate.py --dry-run
     $ python migrate.py --test-notification

 [2] Initial Base Migration (Warm-Up Phase)
     $ python migrate.py
     (Creates dual backups, dumps source, restores target, audits parity)

 [3] Cutover Maintenance Window (Delta Catch-Up)
     - Put application in brief maintenance / read-only mode
     $ python migrate.py --sync-missing
     $ python migrate.py --verify-only

 [4] Switch Application Configuration & Go Live
     - Set CURRENT_PRODUCTION_DB_URL in .env to the new database URL
     - Restart application services
     - Verify application traffic on new provider 🎉
```

---

## 🐍 Python SDK / Programmatic API

You can import and integrate individual components directly into your own Python scripts, CI/CD pipelines, or deployment automations:

```python
from pg_migrator import (
    DatabaseMigrator,
    DeltaSyncEngine,
    PostgresVerifier,
    CompatibilityAuditor,
    PerformanceComparator,
    EmergencyRollback,
    MigrationNotifier
)

# 1. Dispatch custom notification alerts
notifier = MigrationNotifier()
notifier.notify(
    title="Migration Scheduled",
    message="Starting database migration to new cloud provider.",
    status="start",
    details={"Source": "AWS RDS", "Target": "GCP Cloud SQL"}
)

# 2. Audit extension and version compatibility
auditor = CompatibilityAuditor(
    current_prod_url="postgres://user:pass@source-host:5432/db",
    new_db_url="postgres://user:pass@target-host:5432/db"
)
is_compatible = auditor.audit_and_prepare()

# 3. Execute End-to-End Migration
migrator = DatabaseMigrator(
    current_production_db_url="postgres://user:pass@source-host:5432/db",
    new_database_url="postgres://user:pass@target-host:5432/db",
    backup_dir="./backups"
)
result = migrator.run_full_migration(parallel_jobs=4)

# 4. Incremental Catch-Up Sync
sync_engine = DeltaSyncEngine(
    current_prod_url="postgres://user:pass@source-host:5432/db",
    new_db_url="postgres://user:pass@target-host:5432/db"
)
sync_result = sync_engine.sync_missing_data(batch_size=1000)

# 5. Verify Parity
verifier = PostgresVerifier(
    current_prod_url="postgres://user:pass@source-host:5432/db",
    new_db_url="postgres://user:pass@target-host:5432/db"
)
audit_summary = verifier.verify_migration()
print("Verification passed:", audit_summary["success"])
```

---

## 📊 PostgreSQL Benchmark Suite

The toolkit includes an automated diagnostic suite for evaluating the new database before cutover. It reads `NEW_DATABASE_URL` from `.env`:

```bash
python run_benchmark.py
```

```
============================================================
1. DATABASE INFORMATION & CONFIGURATION DISCOVERY
============================================================
PostgreSQL Version: PostgreSQL 16.2 on x86_64-pc-linux-gnu

+----------------------------------+--------------------+
| Parameter                        | Configured Value   |
+==================================+====================+
| max_connections                  | 100                |
| shared_buffers                   | 128MB              |
| effective_cache_size             | 4GB                |
| maintenance_work_mem             | 64MB               |
| work_mem                         | 4MB                |
| random_page_cost                 | 1.1                |
+----------------------------------+--------------------+

============================================================
2. INGESTION & WRITE THROUGHPUT BENCHMARK
============================================================
[1/4] Testing Batch Size 100...   ->  14,285 rows/sec
[2/4] Testing Batch Size 500...   ->  41,666 rows/sec
[3/4] Testing Batch Size 1000...  ->  68,965 rows/sec
[4/4] Testing Batch Size 5000...  ->  112,359 rows/sec

============================================================
3. CONCURRENT READ / TRANSACTION THROUGHPUT
============================================================
Concurrency: 50 clients | Latency p50: 1.2ms | p95: 3.4ms | TPS: 3,420
```

---

## 🤝 Contributing & License

Contributions, issue reports, and feature suggestions are welcome! Feel free to check the [Issues page](https://github.com/Krishnakumar59/postgres-migrator/issues) or submit a Pull Request.

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for complete details.

<div align="center">
<sub>Built with 🐘 for reliable, zero-downtime PostgreSQL operations.</sub>
</div>
