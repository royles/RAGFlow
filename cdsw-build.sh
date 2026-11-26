#!/usr/bin/env bash
# Cloudera AI engine hook. Installs into the project runtime; no virtualenv.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
