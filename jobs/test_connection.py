"""
Test Supabase Connection
========================
Inserts a test row into the `scan_log` table, verifies it can be retrieved,
and then cleans up the test row.

Usage:
    python -m jobs.test_connection
    Or from the jobs directory:
    python test_connection.py
"""

import sys

# Support running as a module or directly
try:
    from jobs.supabase_client import get_client
except ImportError:
    from supabase_client import get_client

def main():
    print("Initializing Supabase client...")
    try:
        supabase = get_client()
    except Exception as e:
        print(f"Failed to initialize Supabase client: {e}")
        sys.exit(1)

    test_date = "1970-01-01"
    print(f"Inserting/upserting test row with scan_date={test_date} into 'scan_log'...")
    
    test_row = {
        "scan_date": test_date,
        "tickers_scanned": 100,
        "signals_generated": 5,
        "scan_duration_secs": 12.34,
        "status": "success",
        "error_message": "Test connection insert"
    }

    try:
        # Delete any existing test row to prevent unique constraint violation
        print("Preparing database: deleting any existing test row for 1970-01-01...")
        supabase.table("scan_log").delete().eq("scan_date", test_date).execute()
        
        # Insert test row
        print("Inserting test row...")
        insert_response = supabase.table("scan_log").insert(test_row).execute()
        print(f"Insert response data: {insert_response.data}")
        
        if not insert_response.data:
            print("ERROR: Insert succeeded but no data was returned.")
            sys.exit(1)
            
        print("Test row inserted successfully.")
        
        # Verify by selecting it back
        print("Retrieving inserted test row...")
        select_response = supabase.table("scan_log").select("*").eq("scan_date", test_date).execute()
        print(f"Retrieve response data: {select_response.data}")
        
        if select_response.data and select_response.data[0]["scan_date"] == test_date:
            print("\n==================================================================")
            print("SUCCESS: Python -> Supabase -> Database connection verified successfully!")
            print("==================================================================\n")
        else:
            print("ERROR: Test row could not be retrieved or data mismatch.")
            sys.exit(1)
            
        # Clean up
        print("Cleaning up test row...")
        supabase.table("scan_log").delete().eq("scan_date", test_date).execute()
        print("Cleanup completed successfully. Database is clean.")
        
    except Exception as e:
        print(f"Error communicating with Supabase database: {e}")
        print("Please check your .env credentials, database URL, and SQL schema setup.")
        sys.exit(1)

if __name__ == "__main__":
    main()
