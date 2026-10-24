"""Record bounded offline commands and their real exit status."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

out = Path(__file__).resolve().parent
root = out.parents[1]
label, *command = sys.argv[1:]
extra = {"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
         "PYTHONPATH": str(root / "src")}
started = datetime.now(timezone.utc).isoformat()
result = subprocess.run(command, cwd=root, env=os.environ | extra,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
(out / (label + ".log")).write_text(result.stdout)
(out / (label + ".command.json")).write_text(json.dumps({
    "cwd": str(root), "argv": command, "env": extra, "started": started,
    "finished": datetime.now(timezone.utc).isoformat(), "exit_code": result.returncode,
}, indent=2) + "\n")
print(result.stdout)
sys.exit(result.returncode)
