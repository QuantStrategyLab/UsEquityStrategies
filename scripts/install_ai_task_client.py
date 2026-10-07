"""Install reviewed runner artifacts; never substitute an index package."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    specs = [("AI_SERVICE_CLIENT", "personal_ai_service-2.0.0-"),
             ("AI_SERVICE_QPK", "quant_platform_kit-1.0.0-")]
    paths = []
    for key, prefix in specs:
        path = Path(os.environ.get(key + "_WHEEL", ""))
        digest = os.environ.get(key + "_SHA256", "")
        if (not re.fullmatch("[0-9a-f]{64}", digest) or not path.is_file() or path.is_symlink()
                or not path.name.startswith(prefix) or path.suffix != ".whl"
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise SystemExit("approved AI task runtime artifact is unavailable")
        paths.append(str(path))
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", *paths], check=True)


if __name__ == "__main__":
    main()
