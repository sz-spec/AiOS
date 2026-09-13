#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
tools/migrate_compliance_to_sqlcipher.py — one-shot migration utility.

Use this to convert an existing plain-SQLite compliance store
(VOS_PROFILE=community/enterprise) into the encrypted SQLCipher store
that VOS_PROFILE=fortress requires.

Usage:
    VOS3_COMPLIANCE_KEY=<passphrase> \\
        python3 tools/migrate_compliance_to_sqlcipher.py \\
            --src /tmp/vos_compliance.db \\
            --dst /tmp/vos_compliance.encrypted.db

Behaviour:
    - Reads every row from the source plain-SQLite DB.
    - Opens the destination as SQLCipher with VOS3_COMPLIANCE_KEY.
    - Writes the rows verbatim (no transformation).
    - Atomically renames dst into place if --replace is given.

Safety:
    - The source DB is never written to or deleted.
    - On any error, the destination is removed so you can re-run cleanly.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VOS3 compliance store → SQLCipher migration")
    ap.add_argument("--src", required=True, help="Plain-SQLite source DB path")
    ap.add_argument("--dst", required=True, help="SQLCipher destination DB path")
    ap.add_argument(
        "--replace",
        action="store_true",
        help="Atomically rename dst over src after migration succeeds.",
    )
    args = ap.parse_args(argv)

    src = Path(args.src)
    dst = Path(args.dst)

    if not src.exists():
        print(f"ERROR: source DB {src} not found", file=sys.stderr)
        return 1

    key = os.environ.get("VOS3_COMPLIANCE_KEY")
    if not key:
        print("ERROR: VOS3_COMPLIANCE_KEY env var must be set", file=sys.stderr)
        return 1

    try:
        import sqlcipher3
    except ImportError:
        print(
            "ERROR: sqlcipher3-binary not installed.\n"
            "       Run: pip install sqlcipher3-binary==0.5.4",
            file=sys.stderr,
        )
        return 1

    if dst.exists():
        print(f"ERROR: destination {dst} already exists; refusing to clobber", file=sys.stderr)
        return 1

    src_conn = sqlite3.connect(str(src), isolation_level=None)
    dst_conn = None
    try:
        dst_conn = sqlcipher3.connect(str(dst), isolation_level=None)
        dst_conn.execute(f"PRAGMA key = '{key}';")
        dst_conn.execute("PRAGMA cipher_page_size = 4096;")
        dst_conn.execute("PRAGMA kdf_iter = 256000;")
        dst_conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512;")
        dst_conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")

        # Discover tables (compliance_events + any indices).
        rows = src_conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for _type, name, sql in rows:
            if sql:
                dst_conn.execute(sql)
        for _type, name, _sql in rows:
            if _type != "table":
                continue
            data_rows = src_conn.execute(f"SELECT * FROM {name}").fetchall()
            if not data_rows:
                continue
            placeholders = ",".join("?" * len(data_rows[0]))
            dst_conn.executemany(
                f"INSERT INTO {name} VALUES ({placeholders})", data_rows
            )
            print(f"  migrated {len(data_rows)} rows from {name}")

        if args.replace:
            backup = src.with_suffix(src.suffix + ".pre-sqlcipher.bak")
            os.replace(src, backup)
            os.replace(dst, src)
            print(f"  replaced {src} (original moved to {backup})")
        else:
            print(f"  migration complete: {dst}")
        return 0
    except Exception as e:
        if dst_conn is not None:
            dst_conn.close()
        if dst.exists():
            dst.unlink()
        print(f"ERROR: migration failed — {e}", file=sys.stderr)
        return 1
    finally:
        src_conn.close()
        if dst_conn is not None:
            try:
                dst_conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
