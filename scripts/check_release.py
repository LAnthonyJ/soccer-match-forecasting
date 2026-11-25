"""Inspect the files Git will distribute. Never print possible credentials."""
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
listing = subprocess.check_output(["git","ls-files","-z"],cwd=root)
paths = [Path(s.decode("utf-8")) for s in listing.split(b"\0") if s]
secret = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|[?&]apiKey=[a-z0-9]{32,})",re.I)
for path in paths:
    if path.suffix in {".db",".sqlite3",".pem",".pkl"} or path.name.startswith(".env") or "node_modules" in path.parts:
        raise SystemExit(f"Private artifact in release: {path}")
    if path.suffix not in {".png",".gz"} and secret.search((root/path).read_bytes()):
        raise SystemExit(f"Credential-like data in {path}")
print(f"Checked {len(paths)} tracked files.")
