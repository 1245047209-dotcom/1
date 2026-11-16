"""Windows GUI entry point, with explicit Tcl initialization."""
import ctypes
import sys
from pathlib import Path

tcl = ctypes.CDLL(str(Path(sys.base_prefix) / "DLLs" / "tcl86t.dll"))
tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
tcl.Tcl_FindExecutable.restype = None
tcl.Tcl_FindExecutable(sys.executable.encode("utf-8"))

from my_devices import main

main()
