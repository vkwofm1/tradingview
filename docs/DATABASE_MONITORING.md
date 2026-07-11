# Database Monitoring Guide

> Production monitoring targets PostgreSQL. SQLite examples are retained only
> for isolated tests and validation of the read-only pre-cutover archive.

Complete monitoring guide for SQLite and PostgreSQL databases in the TradingView market data collection service.

## Monitoring Endpoints

The application exposes three database monitoring endpoints:

### 1. `/health/db` - Database Health Status

**Purpose**: Quick health check for the database

**Example**:
```bash
curl http://localhost:8509/health/db | jq
```

**SQLite Response**:
```json
{
  "status": "ok",
  "database": {
    "type": "sqlite",
    "healthy": true,
    "integrity_check": "ok",
    "file_size_bytes": 52428800,
    "file_size_mb": 50.0,
    "tables": {
      "jobs": 1240,
      "market_data": 52000,
      "market_candles": 125000
    },
    "total_records": 178240,
    "journal_mode": "wal",
    "timestamp": "2026-04-19T12:34:56.789Z"
  }
}
```

**PostgreSQL Response**:
```json
{
  "status": "ok",
  "database": {
    "type": "postgres",
    "healthy": true,
    "version": "PostgreSQL 16.0 ...",
    "user": "tradingview",
    "database": "tradingview",
    "tables": {
      "jobs": 1240,
      "market_data": 52000,
      "market_candles": 125000
    },
    "total_records": 178240,
    "database_size": "512 MB",
    "database_size_bytes": 536870912,
    "active_connections": 1,
    "slow_queries_detected": false,
    "timestamp": "2026-04-19T12:34:56.789Z"
  }
}
```

### 2. `/health/db/stats` - Detailed Database Statistics

**Purpose**: Get comprehensive database statistics for monitoring and optimization

**Example**:
```bash
curl http://localhost:8509/health/db/stats | jq
```

**PostgreSQL Stats** (includes):
- Table sizes breakdown
- Index usage statistics
- Cache hit ratio
- Database growth metrics

**Use Cases**:
- Monitor database growth over time
- Identify unused indexes
- Track cache performance
- Capacity planning

### 3. `/health/db/readiness` - Migration Readiness

**Purpose**: Check if database is ready for migration between SQLite and PostgreSQL

**Example**:
```bash
curl http://localhost:8509/health/db/readiness | jq
```

**Response**:
```json
{
  "ready": true,
  "database": "sqlite",
  "checks": {
    "integrity": true,
    "accessible": true,
    "records": 178240,
    "size_mb": 50.0
  },
  "recommendation": "SQLite healthy and ready for migration"
}
```

## Monitoring Metrics

### SQLite Metrics

| Metric | Normal Range | Warning | Critical |
|--------|--------------|---------|----------|
| File Size | < 100 MB | 100-500 MB | > 500 MB |
| Total Records | Growing | Stable | Declining |
| Integrity | "ok" | Any other | N/A |
| Journal Mode | "wal" | "truncate" | Error |

### PostgreSQL Metrics

| Metric | Normal Range | Warning | Critical |
|--------|--------------|---------|----------|
| Cache Hit Ratio | > 0.99 | 0.95-0.99 | < 0.95 |
| Active Connections | 1-5 | 5-10 | > 10 |
| Slow Queries | 0 | 1-5 | > 5 |
| Database Size | Growing | Stable | > 1 GB |

## Monitoring During Migration

### Pre-Migration Checklist

```bash
# 1. Check source database (SQLite) health
curl http://localhost:8509/health/db

# Expected: "healthy": true, "integrity_check": "ok"

# 2. Check migration readiness
curl http://localhost:8509/health/db/readiness

# Expected: "ready": true

# 3. Verify record counts
curl http://localhost:8509/health/db | jq '.database.tables'

# Record these counts for verification after migration
```

### During Migration

Monitor the migration script output:

```bash
python scripts/migrate_to_postgres.py
```

Key signs to watch:
- ✅ All tables migrated successfully
- ✅ No errors reported
- ✅ Row counts match between source and target
- ❌ Any timeout or connection errors

### Post-Migration Verification

```bash
# 1. Verify target database health
curl http://localhost:8509/health/db

# Expected: PostgreSQL healthy, same record counts

# 2. Run verification script
python scripts/verify_migration.py

# Expected: All checks pass, no data mismatches

# 3. Check API endpoints still work
curl http://localhost:8509/health/apis
curl http://localhost:8509/dashboard/risk | jq '.overview'
curl http://localhost:8509/jobs | jq '.[0]'

# 4. Monitor for 1-2 hours after switch
# - Check for any slow queries
# - Verify data collection continues
# - Monitor memory and CPU usage
```

## Automated Monitoring Setup

### Health Check Script

Create `scripts/health_check.sh` for regular monitoring:

```bash
#!/bin/bash

DB_HEALTH=$(curl -s http://localhost:8509/health/db)
READINESS=$(curl -s http://localhost:8509/health/db/readiness)

# Extract status
DB_STATUS=$(echo $DB_HEALTH | jq -r '.status')
READY=$(echo $READINESS | jq -r '.ready')

echo "Database Health: $DB_STATUS"
echo "Migration Ready: $READY"

# Alert if unhealthy
if [ "$DB_STATUS" != "ok" ]; then
    echo "⚠️  Database health warning!"
    echo $DB_HEALTH | jq
fi

if [ "$READY" != "true" ]; then
    echo "⚠️  Database not ready for migration!"
fi
```

### Continuous Monitoring with cron

```bash
# Add to crontab for every 5 minutes
*/5 * * * * /path/to/health_check.sh >> /var/log/db_health.log 2>&1
```

## Performance Monitoring

### Query Performance (PostgreSQL)

Enable slow query logging:

```sql
-- In PostgreSQL
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- Log queries > 1 second
ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';

-- Reload configuration
SELECT pg_reload_conf();
```

Check slow queries:

```sql
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat%'
ORDER BY mean_exec_time DESC
LIMIT 10;
```

### Cache Performance (PostgreSQL)

Monitor cache hit ratio:

```sql
SELECT sum(heap_blks_read) / (sum(heap_blks_read) + sum(heap_blks_hit)) as ratio
FROM pg_statio_user_tables;

-- Aim for > 0.99 (99%+ cache hits)
```

### Index Usage (PostgreSQL)

Identify unused indexes:

```sql
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0
ORDER BY idx_blks_read DESC;
```

## Alerting Rules

### For Development

Use simple threshold-based alerts:

```python
if db_health['database']['healthy'] == False:
    print("⚠️  Database health alert!")

if db_health['database']['total_records'] == 0:
    print("⚠️  No records in database!")
```

### For Production

Integrate with monitoring system (Prometheus, Datadog, etc.):

```yaml
# Prometheus alerts
- alert: DatabaseUnhealthy
  expr: database_health == 0
  for: 5m
  annotations:
    summary: "Database is unhealthy"

- alert: HighCacheHitRatio
  expr: cache_hit_ratio < 0.95
  for: 10m
  annotations:
    summary: "Low database cache hit ratio"
```

## Troubleshooting Based on Monitoring

### Scenario: SQLite Integrity Check Fails

```json
{
  "integrity_check": "Error: ...",
  "healthy": false
}
```

**Action**:
1. Stop the application
2. Run recovery: `python scripts/diagnose_and_recover.sh`
3. If still failing, restore from backup
4. Consider migrating to PostgreSQL

### Scenario: PostgreSQL Cache Hit Ratio Low

```json
{
  "cache_hit_ratio": 0.85
}
```

**Action**:
1. Increase `shared_buffers` in PostgreSQL config
2. Monitor index usage - add missing indexes
3. Consider partitioning large tables
4. Check for missing ANALYZE runs

### Scenario: Slow Queries After Migration

**Action**:
1. Check if all indexes were created: `curl http://localhost:8509/health/db/stats`
2. Run ANALYZE on all tables: `ANALYZE;`
3. Check query plans: `EXPLAIN ANALYZE SELECT ...;`
4. Create missing indexes based on query patterns

## Dashboarding

### Grafana Dashboard Example

Create panels for:

1. **Database Health Status**
   - PromQL: `database_health`
   - Type: Gauge

2. **Total Records Over Time**
   - PromQL: `database_total_records`
   - Type: Graph

3. **Database Size Trend**
   - PromQL: `database_size_bytes`
   - Type: Graph

4. **Cache Hit Ratio** (PostgreSQL)
   - PromQL: `database_cache_hit_ratio`
   - Type: Gauge

5. **Active Connections** (PostgreSQL)
   - PromQL: `database_active_connections`
   - Type: Graph

## Best Practices

1. **Regular Health Checks**: Monitor `/health/db` every 5-10 minutes
2. **Growth Tracking**: Check `/health/db/stats` daily to track growth
3. **Pre-Migration**: Always verify with `/health/db/readiness` before migration
4. **Post-Migration**: Monitor for 24 hours after switching databases
5. **Backup Before Migration**: Backup SQLite before switching to PostgreSQL
6. **Gradual Rollout**: Test migration in development/staging first
7. **Monitoring Setup**: Set up automated alerts before production migration
8. **Documentation**: Document your monitoring setup and alert thresholds

## References

- PostgreSQL Monitoring: https://www.postgresql.org/docs/16/monitoring.html
- SQLite Pragma: https://www.sqlite.org/pragma.html
- FastAPI Health Checks: https://fastapi.tiangolo.com/advanced/using-request-directly/

---

**Last Updated**: April 19, 2026  
**Status**: Complete - Ready for deployment
