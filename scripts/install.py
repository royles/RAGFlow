"""Install Python dependencies into the current runtime. Does not create a virtualenv."""

import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    """The RAGFlow folder. Also works when the code is run in a notebook cell, where `__file__` is not defined."""
    try:
        return Path(__file__).resolve().parents[1]
    except NameError:
        here = Path.cwd().resolve()
        for folder in (here, *here.parents):
            if (folder / "requirements.txt").is_file():
                return folder
        raise SystemExit("Change to the RAGFlow project folder first.")


ROOT = project_root()


def main() -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    )


if __name__ == "__main__":
    main()
