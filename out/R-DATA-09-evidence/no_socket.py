"""Deny socket activity before importing test or product modules."""
import runpy
import sys
from pathlib import Path


def audit(event, args):
    if event.startswith("socket."):
        raise RuntimeError("R_DATA_09_SOCKET_FORBIDDEN:" + event)


sys.addaudithook(audit)
import ssl
sys.path.insert(0, str(Path.cwd()))
target = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_module(target, run_name="__main__")
