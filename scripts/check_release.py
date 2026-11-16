"""Run source regressions or verify an installed payload without hardware I/O."""
from pathlib import Path
import hashlib
import json
import sys
import unittest

payload = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(payload))
expected = json.loads((Path(__file__).resolve().parent.parent/"release-input/release.json").read_text(encoding="utf-8"))["tag"]
if (payload/"VERSION").read_text(encoding="utf-8-sig").strip() != expected:
    raise SystemExit("Installed VERSION mismatch")
if len(sys.argv) > 2 and sys.argv[2] == "--installed":
    manifest = json.loads((payload/"SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        if hashlib.sha256((payload/name).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Installed content differs: {name}")
    print("Installed source/resource hashes match the tested payload")
else:
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(payload/"tests")))
    if not result.wasSuccessful():
        raise SystemExit(1)
