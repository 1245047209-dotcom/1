"""Windows GUI entry point, with explicit Tcl initialization for the Tk backend."""
from panel_startup import main


def prepare_tcl():
    import ctypes
    from pathlib import Path
    import sys

    tcl = ctypes.CDLL(str(Path(sys.base_prefix) / "DLLs" / "tcl86t.dll"))
    tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
    tcl.Tcl_FindExecutable.restype = None
    tcl.Tcl_FindExecutable(sys.executable.encode("utf-8"))


if __name__ == "__main__":
    main(prepare_tcl=prepare_tcl)
