"""Install Python dependencies into the current runtime. Does not create a virtualenv."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    )


if __name__ == "__main__":
    main()
