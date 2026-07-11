# SQLite Database Diagnosis and Recovery Guide

> Legacy archive guide. The production database is PostgreSQL. Never mount the
> archived SQLite file writable or use this document to restore it as runtime SoT.

This guide explains how to diagnose and recover the SQLite database used by the market data collection service after corruption or issues.

## Database Location and Configuration

- **Database Path (local)**: `data.db` (configurable via `DB_PATH` environment variable)
- **Database Path (Docker)**: `/app/data/data.db`
- **Docker Volume**: `crawl-data`
- **Database Type**: SQLite3 with WAL (Write-Ahead Logging)

## Database Schema

The database contains three main tables:

1. **jobs**: Tracks data collection jobs
   - id, collector, status, created_at, finished_at, result_count, error

2. **market_data**: Raw market data collected from sources
   - id, job_id, collector, symbol, payload, collected_at

3. **market_candles**: 1-minute candle data
   - id, job_id, collector, symbol, interval, candle_time, payload, collected_at

## Quick Recovery Steps

### For Local Development

```bash
# Method 1: Run Python diagnostic script
python scripts/diagnose_db.py

# Method 2: Run shell diagnostic script
bash scripts/diagnose_and_recover.sh
```

### For Docker Container

```bash
# Enter the running container
docker exec -it <container_name> bash

# Run the recovery script inside the container
bash scripts/diagnose_and_recover.sh

# Or run the Python diagnostic script
python scripts/diagnose_db.py
```

### For Kubernetes Pod

#### Option 1: Create a diagnostic job

```bash
kubectl create job db-recovery --image=ghcr.io/vkwofm1/tradingview:latest \
  -n default \
  --dry-run=client -o yaml | \
  kubectl set env - DB_PATH=/app/data/data.db | \
  kubectl apply -f -
```

#### Option 2: Run commands in the existing pod

```bash
# Find the pod name
POD_NAME=$(kubectl get pods -l app=tradingview -o jsonpath='{.items[0].metadata.name}')

# Run diagnosis
kubectl exec $POD_NAME -- bash scripts/diagnose_and_recover.sh

# Or run Python script
kubectl exec $POD_NAME -- python scripts/diagnose_db.py
```

#### Option 3: Create a debug pod with shared volume

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: db-recovery-debug
  namespace: default
spec:
  containers:
  - name: debug
    image: ghcr.io/vkwofm1/tradingview:latest
    command: ["/bin/bash", "-c", "bash scripts/diagnose_and_recover.sh && sleep 3600"]
    volumeMounts:
    - name: crawl-data
      mountPath: /app/data
    env:
    - name: DB_PATH
      value: /app/data/data.db
  volumes:
  - name: crawl-data
    persistentVolumeClaim:
      claimName: crawl-data  # Your PVC name
  restartPolicy: Never
```

Apply with: `kubectl apply -f recovery-pod.yaml`

## Diagnosis Steps Explained

### 1. File Status Check
- Verifies the database file exists
- Checks file size (should be > 0 KB)
- Confirms read/write permissions
- Shows last modification time

### 2. Integrity Check
```bash
sqlite3 /app/data/data.db "PRAGMA integrity_check;"
```
- Returns `ok` if database is healthy
- Returns specific error messages if corrupted

### 3. Schema Verification
- Checks if all required tables exist
- Counts records in each table
- Verifies table structure

### 4. Recovery Methods (in order)

**Method 1: VACUUM**
```bash
sqlite3 /app/data/data.db "VACUUM;"
```
- Defragments and repairs minor issues
- Least destructive
- May recover corrupted databases

**Method 2: Reinitialization**
- Creates backup of corrupted file
- Deletes the corrupted database
- Initializes fresh schema
- Data loss: All existing data is lost, but service can resume

## Manual Recovery Commands

### Check database status
```bash
# Quick check
sqlite3 /app/data/data.db "SELECT COUNT(*) FROM jobs; SELECT COUNT(*) FROM market_data; SELECT COUNT(*) FROM market_candles;"

# Full integrity check
sqlite3 /app/data/data.db "PRAGMA integrity_check;"

# List all tables
sqlite3 /app/data/data.db ".tables"

# Database file info
file /app/data/data.db
ls -lh /app/data/data.db
```

### Attempt recovery
```bash
# Backup first
cp /app/data/data.db /app/data/data.db.backup.$(date +%Y%m%d_%H%M%S)

# Try VACUUM
sqlite3 /app/data/data.db "VACUUM;"

# Check if recovered
sqlite3 /app/data/data.db "PRAGMA integrity_check;"
```

### Reinitialize if recovery fails
```bash
# Backup the corrupted file
mv /app/data/data.db /app/data/data.db.corrupted

# Python reinitialization
python -c "from app import db; db.init_db()"
```

## Restart Service After Recovery

### Docker Compose
```bash
docker-compose restart crawl
```

### Kubernetes
```bash
# Rolling restart
kubectl rollout restart deployment tradingview -n default

# Or delete the pod to force recreation
kubectl delete pod <pod_name> -n default
```

## Monitoring After Recovery

### Check application health
```bash
# Docker
docker-compose logs -f crawl

# Kubernetes
kubectl logs -f deployment/tradingview -n default

# Health check endpoint
curl http://localhost:8509/health
```

### Verify data collection resumption
```bash
# Check if new jobs are being created
curl http://localhost:8509/api/jobs | jq '.[-5:] | .[] | {id, collector, status, created_at}'
```

## Preventive Measures

1. **Regular Backups**: Implement automated backups of the database
2. **Health Checks**: Monitor database integrity regularly
3. **Monitoring**: Log and alert on database errors
4. **Graceful Shutdowns**: Ensure proper database closure on container stops
5. **WAL Mode**: Database already uses WAL mode for crash recovery

## WAL (Write-Ahead Logging) Mode

The database is configured with WAL mode for robustness:
```python
conn.execute("PRAGMA journal_mode=WAL")
```

This creates:
- `data.db` - Main database
- `data.db-wal` - Write-ahead log
- `data.db-shm` - Shared memory file

All three files are needed for proper operation. Do not delete the `-wal` or `-shm` files manually.

## Troubleshooting

### Database locked error
```
Error: database is locked
```
**Solution**: Wait for any running operations to complete, or restart the service

### Disk full error
```
Error: disk I/O error
```
**Solution**: Free up disk space and check file permissions

### Cannot open database
```
Error: file is not a database
```
**Solution**: Database file is corrupted - reinitialize using the recovery script

### Permission denied
```
Error: unable to open database file
```
**Solution**: Check file permissions (should be readable and writable by the application user)

## Getting Help

If automatic recovery fails:
1. Check the recovery script output for specific errors
2. Review application logs for related errors
3. Verify disk space and permissions
4. Create a backup of the corrupted file for investigation
5. Reinitialize the database using the provided scripts
