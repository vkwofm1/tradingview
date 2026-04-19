# PostgreSQL Migration Quick Start

## TL;DR - 5-Minute Setup

### Option 1: Docker (Recommended)

```bash
# 1. Start PostgreSQL
docker-compose up -d postgres
sleep 5

# 2. Initialize schema
docker-compose run --rm crawl python -c "from app import db; db.init_db()"

# 3. Backup SQLite (optional but recommended)
cp data.db data.db.backup.$(date +%Y%m%d_%H%M%S)

# 4. Migrate data
docker-compose run --rm crawl python scripts/migrate_to_postgres.py \
  --source-db /app/data/data.db \
  --target-url "postgresql://tradingview:tradingview_dev_password@postgres:5432/tradingview"

# 5. Verify migration
docker-compose run --rm crawl python scripts/verify_migration.py \
  --source-db /app/data/data.db \
  --target-url "postgresql://tradingview:tradingview_dev_password@postgres:5432/tradingview"

# 6. Switch to PostgreSQL in docker-compose.yml or .env:
# DB_TYPE=postgres

# 7. Restart application
docker-compose restart crawl

# 8. Check health
curl http://localhost:8509/health
```

### Option 2: Local Development

```bash
# 1. Install PostgreSQL (macOS: brew install postgresql@16)
# 2. Create database: createdb -O tradingview tradingview
# 3. Set environment:
export DB_TYPE=postgres
export DATABASE_URL="postgresql://tradingview:password@localhost:5432/tradingview"

# 4. Initialize schema
python -c "from app import db; db.init_db()"

# 5. Migrate data
python scripts/migrate_to_postgres.py

# 6. Verify
python scripts/verify_migration.py

# 7. Run app
python app/main.py
```

## Verification

After migration, verify success:

```bash
# Check application is healthy
curl http://localhost:8509/health

# Check database health
curl http://localhost:8509/dashboard/risk | jq '.overview'

# Check recent jobs
curl http://localhost:8509/jobs | jq '.[-3:] | .[].status'
```

## Quick Rollback

If issues occur:

```bash
# Revert to SQLite immediately
DB_TYPE=sqlite docker-compose restart crawl

# Restore from backup if needed
cp data.db.backup.YYYYMMDD_HHMMSS data.db
docker-compose restart crawl
```

## Key Files

| File | Purpose |
|------|---------|
| `docs/POSTGRES_MIGRATION.md` | Detailed migration guide |
| `scripts/migrate_to_postgres.py` | Data migration tool |
| `scripts/verify_migration.py` | Verification tool |
| `app/db.py` | Database abstraction layer |
| `docker-compose.yml` | Docker setup with PostgreSQL |

## Environment Variables

```bash
# Required for PostgreSQL
DB_TYPE=postgres
DATABASE_URL=postgresql://tradingview:password@localhost:5432/tradingview

# Optional for Docker
POSTGRES_PASSWORD=your_password
```

## Troubleshooting

### Can't connect to PostgreSQL
```bash
# Check if PostgreSQL is running
docker-compose logs postgres

# Verify connection string
docker-compose run --rm crawl python -c "import psycopg; psycopg.connect('postgresql://...')"
```

### Migration failed
```bash
# Check what went wrong
docker-compose logs crawl

# Try dry-run to preview
docker-compose run --rm crawl python scripts/migrate_to_postgres.py --dry-run

# Check SQLite database integrity
sqlite3 data.db "PRAGMA integrity_check;"
```

### Data mismatch after migration
```bash
# Verify row counts
docker-compose run --rm crawl python scripts/verify_migration.py

# Details in verification output will help diagnose issues
```

## Performance Check

Before/after performance:

```bash
# Test query performance
# Risk dashboard query
curl http://localhost:8509/dashboard/risk

# Check response time in curl output
```

## Next Steps

1. ✅ Completed: Infrastructure setup
2. ⏭️ Next: Run migration in your environment
3. ⏭️ Next: Verify all APIs work correctly
4. ⏭️ Next: Set up monitoring (optional)

## Support

For issues:
1. Check `docs/POSTGRES_MIGRATION.md` troubleshooting section
2. Review `docker-compose logs` output
3. Run verification scripts to identify issues
4. Refer to PostgreSQL documentation if needed

---

**Last Updated**: April 19, 2026  
**Status**: Ready for deployment
