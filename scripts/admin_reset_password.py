#!/usr/bin/env python3
"""Reset an admin user's password directly in the admin database.

Usage:
    python scripts/admin_reset_password.py [--username USERNAME] [--db-path PATH]

Works even when the web application is down.
"""

import argparse
import getpass
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
from admin.database import AdminDB


def main():
    parser = argparse.ArgumentParser(description="Reset admin user password")
    parser.add_argument("--username", default="admin", help="Username to reset (default: admin)")
    parser.add_argument("--db-path", default=os.getenv("ADMIN_DB_PATH", "/opt/ai-assistant/data/admin.db"),
                        help="Path to admin.db")
    args = parser.parse_args()

    db = AdminDB(args.db_path)
    db.initialize()

    if not db.available:
        print("ERROR: Could not open database at", args.db_path)
        sys.exit(1)

    user = db.get_user_by_username(args.username)
    if not user:
        print(f"ERROR: User '{args.username}' not found in database.")
        print("Available users:", ", ".join(u["username"] for u in db.list_users()))
        db.close()
        sys.exit(1)

    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("ERROR: Passwords do not match.")
        db.close()
        sys.exit(1)

    if len(password) < 8:
        print("ERROR: Password must be at least 8 characters.")
        db.close()
        sys.exit(1)

    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    db.update_user_password(user["id"], pw_hash)
    db.close()

    print(f"Password updated for '{args.username}'. Active sessions will be invalidated.")


if __name__ == "__main__":
    main()
