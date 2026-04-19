#!/bin/bash
# Database Diagnosis and Recovery Script
# This script runs diagnosis and recovery steps for the SQLite database
# Used in Kubernetes environments to recover corrupted databases

set -e

DB_PATH="${DB_PATH:-/app/data/data.db}"
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Starting database diagnosis and recovery"
echo "Database path: $DB_PATH"

# Change to app directory
cd "$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)")"

# Step 1: File status check
echo ""
echo "=== STEP 1: File Status Check ==="
if [ -f "$DB_PATH" ]; then
    SIZE_BYTES=$(stat -c%s "$DB_PATH")
    SIZE_MB=$((SIZE_BYTES / 1024 / 1024))
    MODIFIED=$(stat -c%y "$DB_PATH")
    echo "✓ File exists"
    echo "  Size: ${SIZE_MB}MB (${SIZE_BYTES} bytes)"
    echo "  Modified: $MODIFIED"
    echo "  Readable: $(test -r "$DB_PATH" && echo 'Yes' || echo 'No')"
    echo "  Writable: $(test -w "$DB_PATH" && echo 'Yes' || echo 'No')"
else
    echo "✗ Database file does not exist at $DB_PATH"
fi

# Step 2: Integrity check
echo ""
echo "=== STEP 2: Integrity Check ==="
if [ -f "$DB_PATH" ]; then
    # Check if file is locked or in use
    if sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
        echo "✓ Database integrity check passed"
        INTEGRITY_OK=1
    else
        INTEGRITY_RESULT=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>&1 || echo "ERROR")
        echo "✗ Database integrity check failed:"
        echo "  $INTEGRITY_RESULT"
        INTEGRITY_OK=0
    fi
else
    echo "⚠ Cannot check integrity - file does not exist"
    INTEGRITY_OK=0
fi

# Step 3: Schema check
echo ""
echo "=== STEP 3: Database Schema Check ==="
if [ -f "$DB_PATH" ]; then
    TABLES=$(sqlite3 "$DB_PATH" "SELECT GROUP_CONCAT(name) FROM sqlite_master WHERE type='table';" 2>/dev/null)
    echo "Tables: $TABLES"

    if echo "$TABLES" | grep -q "jobs"; then
        JOB_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM jobs;" 2>/dev/null)
        echo "✓ jobs table exists ($JOB_COUNT records)"
    else
        echo "✗ jobs table missing"
    fi

    if echo "$TABLES" | grep -q "market_data"; then
        MD_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM market_data;" 2>/dev/null)
        echo "✓ market_data table exists ($MD_COUNT records)"
    else
        echo "✗ market_data table missing"
    fi

    if echo "$TABLES" | grep -q "market_candles"; then
        MC_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM market_candles;" 2>/dev/null)
        echo "✓ market_candles table exists ($MC_COUNT records)"
    else
        echo "✗ market_candles table missing"
    fi
fi

# Step 4: Recovery attempt
echo ""
echo "=== STEP 4: Recovery Attempt ==="
if [ $INTEGRITY_OK -ne 1 ] && [ -f "$DB_PATH" ]; then
    echo "Attempting recovery with VACUUM..."
    if sqlite3 "$DB_PATH" "VACUUM;" 2>/dev/null; then
        echo "✓ VACUUM completed successfully"
        # Re-check integrity
        if sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
            echo "✓ Database recovered successfully"
            exit 0
        else
            echo "⚠ VACUUM completed but integrity check still fails"
        fi
    else
        echo "✗ VACUUM failed"
    fi

    # Try to reinitialize
    echo "Reinitializing database with schema..."
    # Backup corrupted file
    BACKUP_PATH="${DB_PATH}.backup.$(date -u +'%Y%m%d_%H%M%S')"
    cp "$DB_PATH" "$BACKUP_PATH"
    echo "Created backup: $BACKUP_PATH"

    # Remove corrupted file
    rm "$DB_PATH"
    echo "Removed corrupted database file"
fi

# Step 5: Initialize fresh database if needed
echo ""
echo "=== STEP 5: Database Initialization ==="
if [ ! -f "$DB_PATH" ]; then
    echo "Initializing fresh database..."
    python3 -c "
import sys
sys.path.insert(0, '.')
from app import db
db.init_db()
print('Database initialized successfully')
"
    if [ $? -eq 0 ]; then
        echo "✓ Database initialized"
    else
        echo "✗ Failed to initialize database"
        exit 1
    fi
fi

# Final validation
echo ""
echo "=== FINAL VALIDATION ==="
if [ -f "$DB_PATH" ]; then
    if sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
        echo "✓ Database is healthy and ready to use"
        exit 0
    else
        echo "✗ Database integrity check failed"
        exit 1
    fi
else
    echo "✗ Database file does not exist"
    exit 1
fi
