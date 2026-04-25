#!/usr/bin/env python3
"""Create an API key directly in the admin database.

Usage:
    python scripts/admin_create_api_key.py <name> [--scope SCOPE] [--db-path PATH]

Prints the full API key to stdout (shown only once).
"""

import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin.database import AdminDB


def main():
    parser = argparse.ArgumentParser(description="Create admin API key")
    parser.add_argument("name", help="Descriptive name for the key")
    parser.add_argument("--scope", default="", help="Key scope (e.g. 'read,write')")
    parser.add_argument("--db-path", default=os.getenv("ADMIN_DB_PATH", "/opt/ai-assistant/data/admin.db"),
                        help="Path to admin.db")
    args = parser.parse_args()

    db = AdminDB(args.db_path)
    db.initialize()

    if not db.available:
        print("ERROR: Could not open database at", args.db_path)
        sys.exit(1)

    raw_key, key_id = db.create_api_key(args.name, args.scope, "cli")
    db.close()

    print(f"API key created (id={key_id}):")
    print(f"  Name:  {args.name}")
    print(f"  Scope: {args.scope or '(none)'}")
    print(f"  Key:   {raw_key}")
    print()
    print("IMPORTANT: Save this key now. It cannot be retrieved later.")


if __name__ == "__main__":
    main()
