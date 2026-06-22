"""
Supabase Client
===============
Provides a configured Supabase client for the jobs package.

Usage:
    from jobs.supabase_client import get_client
    client = get_client()
    client.table("scan_log").insert({...}).execute()

Environment variables required:
    SUPABASE_URL         - Project URL from Supabase dashboard
    SUPABASE_SERVICE_KEY - service_role key (secret, bypasses RLS)
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client


# Load .env from project root
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)


def get_client() -> Client:
    """
    Create and return a Supabase client using the service_role key.

    Raises:
        SystemExit: If required environment variables are missing.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not url:
        print("ERROR: SUPABASE_URL is not set.")
        print("Copy .env.example to .env and fill in your Supabase project URL.")
        sys.exit(1)

    if not key:
        print("ERROR: SUPABASE_SERVICE_KEY is not set.")
        print("Copy .env.example to .env and fill in your service_role key.")
        print("Find it at: Supabase Dashboard > Settings > API > service_role")
        sys.exit(1)

    return create_client(url, key)
