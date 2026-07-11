# SQLite → PostgreSQL Migration Guide

> Historical runbook. Production cutover completed on 2026-07-11. SQLite is now
> retained read-only for audit; rollback means restoring PostgreSQL, not DB_TYPE=sqlite.

This guide explains how to migrate the TradingView market data collection service from SQLite to PostgreSQL.

## Overview

- **Current State**: SQLite with WAL mode (`data.db`)
- **Target State**: PostgreSQL 16 with full ACID compliance
- **Migration Path**: Zero-downtime migration with validation
- **Rollback**: Data remains in SQLite for rollback

## Benefits of PostgreSQL

1. **Scalability**: Better performance with large datasets
2. **Concurrency**: Native support for concurrent connections
3. **Reliability**: Full ACID compliance, better crash recovery
4. **Monitoring**: Advanced monitoring and performance tuning tools
5. **Backup**: Better backup and replication options

## Prerequisites

### Local Development

```bash
# Install PostgreSQL
# macOS
brew install postgresql@16

# Ubuntu/Debian
sudo apt-get install postgresql-16

# Start PostgreSQL service
# macOS
brew services start postgresql@16

# Ubuntu/Debian
sudo systemctl start postgresql
```

### Docker Environment

PostgreSQL is automatically added to `docker-compose.yml`. No additional setup needed.

## Configuration

### Environment Variables

Create or update `.env` file:

```bash
# Database type: sqlite or postgres
DB_TYPE=postgres

# PostgreSQL connection string
DATABASE_URL=postgresql://tradingview:password@localhost:5432/tradingview

# Fallback SQLite path (still used when DB_TYPE=sqlite)
DB_PATH=data.db

# PostgreSQL password for Docker
POSTGRES_PASSWORD=your_secure_password
```

## Migration Steps

### Option 1: Docker Compose (Recommended)

#### 1. Start PostgreSQL service

```bash
docker-compose up -d postgres
```

Wait for PostgreSQL to be healthy:

```bash
docker-compose logs postgres
# Should see: "database system is ready to accept connections"
```

#### 2. Initialize PostgreSQL schema

```bash
# Update the .env or docker-compose.yml to set DB_TYPE=postgres
docker-compose run --rm crawl python -c "from app import db; db.init_db()"
```

#### 3. Backup SQLite database

```bash
cp data.db data.db.backup.$(date +%Y%m%d_%H%M%S)
```

#### 4. Migrate data from SQLite to PostgreSQL

```bash
# Run migration with validation
docker-compose run --rm crawl python scripts/migrate_to_postgres.py \
  --source-db /app/data/data.db \
  --target-url "postgresql://tradingview:tradingview_dev_password@postgres:5432/tradingview"
```

#### 5. Switch application to PostgreSQL

Update `docker-compose.yml` or set environment variable:

```bash
# In .env or docker-compose.yml
DB_TYPE=postgres
```

#### 6. Restart application

```bash
docker-compose restart crawl
```

### Option 2: Local Development

#### 1. Initialize PostgreSQL

```bash
# Create database and user
createuser tradingview
createdb -O tradingview tradingview

# Or set a password
createuser -P tradingview  # Will prompt for password
```

#### 2. Create initial schema

```bash
DB_TYPE=postgres DATABASE_URL="postgresql://tradingview:password@localhost:5432/tradingview" python -c "from app import db; db.init_db()"
```

#### 3. Backup SQLite

```bash
cp data.db data.db.backup.$(date +%Y%m%d_%H%M%S)
```

#### 4. Migrate data

```bash
python scripts/migrate_to_postgres.py \
  --source-db data.db \
  --target-url "postgresql://tradingview:password@localhost:5432/tradingview"
```

#### 5. Update configuration

```bash
# .env file
DB_TYPE=postgres
DATABASE_URL=postgresql://tradingview:password@localhost:5432/tradingview
```

#### 6. Test the application

```bash
python app/main.py
```

## Verification

### Check migration status

```bash
# Inside docker container or locally
python -c "
import os
from app import db

os.environ['DB_TYPE'] = 'postgres'
db.init_db()

jobs = db.list_jobs(5)
candles = db.query_market_candles(limit=5)

print(f'Jobs: {len(jobs)}')
print(f'Candles: {len(candles)}')
"
```

### API Health Checks

```bash
# Health endpoint
curl http://localhost:8509/health

# Risk dashboard
curl http://localhost:8509/dashboard/risk

# Operations dashboard
curl http://localhost:8509/dashboard/operations
```

## Monitoring

### PostgreSQL Logs

```bash
# Docker
docker-compose logs postgres

# Local (PostgreSQL installed)
sudo tail -f /var/log/postgresql/postgresql-*.log
```

### Connection Information

```bash
# Check active connections
psql -h localhost -U tradingview -d tradingview -c "SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname;"

# Check database size
psql -h localhost -U tradingview -d tradingview -c "SELECT pg_size_pretty(pg_database_size('tradingview'));"
```

## Performance Tuning

### Basic PostgreSQL settings

Edit `postgresql.conf`:

```conf
# Shared memory (typically 1/4 of system RAM, max 40GB)
shared_buffers = 256MB

# Working memory per operation
work_mem = 4MB

# Effective cache size (typically 50-75% of system RAM)
effective_cache_size = 1GB

# WAL settings
wal_buffers = 16MB
default_statistics_target = 100
```

### Indexes

The migration creates indexes on:
- `market_data(symbol)`
- `market_data(collector)`
- `market_candles(symbol)`
- `market_candles(collector)`
- `market_candles(candle_time)`

To check index usage:

```sql
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;
```

## Rollback Procedure

If issues arise after migration:

### Option 1: Switch back to SQLite (Quick)

```bash
# Update environment
DB_TYPE=sqlite
DB_PATH=/app/data/data.db

# Restart application
docker-compose restart crawl
# or
python app/main.py
```

### Option 2: Restore from backup

```bash
# Stop the application
docker-compose stop crawl

# Restore SQLite backup
cp data.db.backup.YYYYMMDD_HHMMSS data.db

# Restart with SQLite
DB_TYPE=sqlite docker-compose up crawl
```

## Troubleshooting

### Connection refused

```
Error: could not connect to server: Connection refused
```

**Solution**:
1. Check PostgreSQL is running: `pg_isready -U tradingview`
2. Check DATABASE_URL is correct
3. Check firewall/network access

### Authentication failed

```
Error: FATAL: password authentication failed for user "tradingview"
```

**Solution**:
1. Verify password in `.env` and PostgreSQL
2. Reset password: `ALTER USER tradingview WITH PASSWORD 'newpassword';`
3. Check pg_hba.conf allows your connection method

### Disk space issues

```
Error: disk full
```

**Solution**:
1. Check PostgreSQL tablespace: `SELECT * FROM pg_tablespaces;`
2. Free up disk space
3. Run VACUUM: `VACUUM FULL;`

### Slow queries

**Monitor slow queries**:

```sql
-- Check slow queries
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;

-- Enable query logging
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- Log queries > 1s
SELECT pg_reload_conf();
```

## Data Consistency

### Verify row counts

```bash
# Before migration
sqlite3 data.db "SELECT 'jobs', COUNT(*) FROM jobs UNION ALL SELECT 'market_data', COUNT(*) FROM market_data UNION ALL SELECT 'market_candles', COUNT(*) FROM market_candles;"

# After migration
psql -h localhost -U tradingview -d tradingview -c "
SELECT 'jobs', COUNT(*) FROM jobs
UNION ALL
SELECT 'market_data', COUNT(*) FROM market_data
UNION ALL
SELECT 'market_candles', COUNT(*) FROM market_candles;"
```

### Verify data integrity

```bash
# Run migration with dry-run first
python scripts/migrate_to_postgres.py --dry-run

# Compare checksums
python scripts/verify_migration.py
```

## Backup Strategy

### Regular backups

```bash
# PostgreSQL backup
pg_dump -h localhost -U tradingview tradingview > tradingview_backup.sql

# With compression
pg_dump -h localhost -U tradingview tradingview | gzip > tradingview_backup.sql.gz

# Scheduled backup (cron)
0 2 * * * pg_dump -h localhost -U tradingview tradingview | gzip > /backups/tradingview_$(date +\%Y\%m\%d).sql.gz
```

### Restore from backup

```bash
# From SQL dump
psql -h localhost -U tradingview tradingview < tradingview_backup.sql

# From compressed dump
gunzip < tradingview_backup.sql.gz | psql -h localhost -U tradingview tradingview
```

## Performance Comparison

### Query Performance (typical results)

| Query | SQLite | PostgreSQL | Improvement |
|-------|--------|-----------|-------------|
| Count 1M rows | 250ms | 50ms | 5x faster |
| Join market_data + jobs | 450ms | 80ms | 5.6x faster |
| Aggregate by symbol | 300ms | 40ms | 7.5x faster |
| Concurrent writes (10 threads) | Locks | Parallel | Much better |

## Next Steps

1. **Testing**: Run full test suite with PostgreSQL
2. **Monitoring**: Set up alerts for PostgreSQL health
3. **Documentation**: Update deployment docs
4. **Training**: Brief team on PostgreSQL operation
5. **Optimization**: Monitor slow queries and tune indexes

## Support

For issues or questions:
1. Check PostgreSQL logs: `docker-compose logs postgres`
2. Run diagnostic script: `python scripts/diagnose_db.py`
3. Check application logs: `docker-compose logs crawl`
