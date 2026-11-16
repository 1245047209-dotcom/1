"""Verify a prebuilt installer and prepare the assets for a GitHub Release."""
from pathlib import Path
import hashlib
import json
import re
import shutil

root = Path(__file__).resolve().parent.parent
meta = json.loads((root / "release-input" / "release.json").read_text(encoding="utf-8-sig"))
tag, name, digest = meta["tag"], meta["installer"], meta["sha256"]
if not re.fullmatch(r"v\d+(?:\.\d+){0,2}", tag) or name != f"Nexus-{tag}-Windows-x64-Setup.exe":
    raise SystemExit("Release tag/installer name mismatch")
path = root / "release-input" / name
with path.open("rb") as stream:
    actual = hashlib.file_digest(stream, "sha256").hexdigest()
if actual != digest:
    raise SystemExit("Installer SHA-256 verification failed")
output = root / "dist"
output.mkdir(exist_ok=True)
shutil.copy2(path, output / name)
(output / f"Nexus-{tag}-SHA256.txt").write_text(f"{actual}  {name}\n", encoding="ascii")
print(tag)
