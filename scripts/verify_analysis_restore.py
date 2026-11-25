"""Compare two operator-owned D1 SQL exports offline without printing private records."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def inspect(path):
    with closing(sqlite3.connect(":memory:")) as db:
        # Restore only into memory. Keep external database attachment disabled.
        db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        db.executescript(path.read_text(encoding="utf-8-sig"))
        if db.execute("PRAGMA integrity_check").fetchall()!=[("ok",)]:
            raise ValueError("SQLite integrity check failed")
        rows = db.execute("""SELECT month,kind,COUNT(*),SUM(reserved),SUM(charged),
                             SUM(CASE WHEN status='pending' THEN charged ELSE 0 END)
                             FROM analysis_calls GROUP BY month,kind ORDER BY month,kind""")
        totals = [dict(zip(("month","kind","requests","reserved_microyuan","charged_microyuan","pending_microyuan"),r))
                  for r in rows]
        tables = {}
        for name,pk in (("analysis_calls","request_id"),("analysis_reports","cache_key")):
            digest = hashlib.sha256()
            count = 0
            for row in db.execute(f"SELECT * FROM {name} ORDER BY {pk}"):
                digest.update(json.dumps(row,ensure_ascii=True,separators=(",",":")).encode()+b"\n")
                count += 1
            tables[name]={"rows":count,"sha256":digest.hexdigest()}
        return {"tables":tables,"monthly_usage":totals}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_export",type=Path)
    parser.add_argument("restored_export",type=Path)
    args = parser.parse_args()
    source, restored = inspect(args.source_export), inspect(args.restored_export)
    print(json.dumps({"verified":source==restored,"source":source,"restored":restored}))
    if source!=restored:
        raise SystemExit(1)


if __name__=="__main__":
    main()
