"""Package clean full source and independent installer/source checksums."""
from pathlib import Path
import hashlib
import sys
import zipfile

payload, output = map(lambda value: Path(value).resolve(), sys.argv[1:3])
output.mkdir(parents=True, exist_ok=True)
tag = (payload/"VERSION").read_text(encoding="utf-8-sig").strip()
installer = output/f"Nexus-{tag}-Windows-x64-Setup.exe"
if not installer.is_file():
    raise SystemExit("Installer is missing")
source = output/f"Nexus-{tag}-source.zip"
with zipfile.ZipFile(source,"x",zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for path in sorted(payload.rglob("*")):
        relative = path.relative_to(payload)
        if path.is_file() and "runtime" not in relative.parts and "__pycache__" not in relative.parts and path.suffix not in (".pyc",".pyo",".log",".lock"):
            if len(relative.parts)==1 and path.suffix==".json" and path.name!="SOURCE_MANIFEST.json":
                raise SystemExit("Unexpected runtime state in source")
            archive.write(path, f"Nexus-{tag}-source/"+relative.as_posix())
with zipfile.ZipFile(source) as archive:
    if archive.testzip() is not None:
        raise SystemExit("Source ZIP CRC check failed")
for artifact, name in ((installer,f"Nexus-{tag}-SHA256.txt"),(source,f"Nexus-{tag}-source.zip.sha256")):
    digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
    (output/name).write_text(f"{digest}  {artifact.name}\n",encoding="ascii")
    print(f"{artifact.name}: {artifact.stat().st_size} bytes; SHA256 {digest}")
