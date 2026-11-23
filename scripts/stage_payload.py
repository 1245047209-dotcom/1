"""Build a clean payload from pinned baseline assets plus reviewed source."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

root, baseline, payload = map(lambda value: Path(value).resolve(), sys.argv[1:4])
overlay = root / "app"
if payload.exists():
    raise SystemExit("Payload must be a fresh directory")
payload.mkdir(parents=True)
for item in baseline.iterdir():
    if item.is_dir() and item.name in ("assets", "runtime", "tools"):
        shutil.copytree(item, payload/item.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.log"))
shutil.copytree(overlay, payload, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.log", "*.lock", "*.tmp"))
if len(sys.argv) == 5:
    frontend = payload / "native" / "NexusNative.exe"
    frontend.parent.mkdir(exist_ok=True)
    shutil.copy2(sys.argv[4], frontend)
version = (payload/"VERSION").read_text(encoding="utf-8-sig").strip()
if version != json.loads((root/"release-input/release.json").read_text(encoding="utf-8"))["tag"]:
    raise SystemExit("Payload VERSION mismatch")
# All root JSON files are runtime user state. Catalog JSON beneath assets is software data.
if list(payload.glob("*.json")):
    raise SystemExit("Personal/runtime JSON must not enter the release")
files = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
         for path in sorted(payload.rglob("*")) if path.is_file() and "runtime" not in path.relative_to(payload).parts}
(payload/"SOURCE_MANIFEST.json").write_text(json.dumps({"version": version, "files": files}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(f"Prepared {version}: {len(files)} source/resource files plus runtime")
