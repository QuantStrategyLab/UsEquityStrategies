"""R3 paths use local defaults or explicit installation-specific overrides."""

import os
from pathlib import Path
import subprocess
import sys


def test_path_defaults_and_overrides(tmp_path):
    root = Path(__file__).resolve().parents[2]
    variables = {
        "UES_R3_OUTPUT_ROOT": "DEFAULT_OUTPUT_ROOT",
        "UES_R3_CONTRACT_PATH": "CONTRACT_PATH",
        "UES_R3_WORKER_PROMPT_PATH": "WORKER_PROMPT_PATH",
        "UES_R3_PRIVATE_ROOT": "PRIVATE_ROOT",
    }
    environment = {"PATH": os.environ["PATH"], "PYTHONPATH": str(root / "src")}
    defaults = "\n".join([
        "from us_equity_strategies.research import r3_joint_evidence as r3",
        *[f"assert not r3.{attribute}.is_absolute()" for attribute in variables.values()],
    ])
    subprocess.run([sys.executable, "-c", defaults], env=environment, check=True)
    environment.update({name: str(tmp_path / name.lower()) for name in variables})
    configured = "\n".join([
        "import os",
        "from pathlib import Path",
        "from us_equity_strategies.research import r3_joint_evidence as r3",
        *[f"assert r3.{attribute} == Path(os.environ[{name!r}])" for name, attribute in variables.items()],
    ])
    subprocess.run([sys.executable, "-c", configured], env=environment, check=True)
