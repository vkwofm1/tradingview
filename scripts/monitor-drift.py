#!/usr/bin/env python3
"""Entrypoint for Paperclip issue state drift monitoring routine."""

import json
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.paperclip_drift_monitor import run_drift_monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

if __name__ == "__main__":
    api_url = os.environ.get("PAPERCLIP_API_URL")
    api_key = os.environ.get("PAPERCLIP_API_KEY")
    company_id = os.environ.get("PAPERCLIP_COMPANY_ID")
    run_id = os.environ.get("PAPERCLIP_RUN_ID")

    if not all([api_url, api_key, company_id]):
        print("Missing required environment variables:")
        print(f"  PAPERCLIP_API_URL: {bool(api_url)}")
        print(f"  PAPERCLIP_API_KEY: {bool(api_key)}")
        print(f"  PAPERCLIP_COMPANY_ID: {bool(company_id)}")
        sys.exit(1)

    result = run_drift_monitor(api_url, api_key, company_id, run_id)
    print(json.dumps(result, indent=2))
