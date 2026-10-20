#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo 'Python 3.11+ is required.'; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
echo 'Read deploy/README.md before installing the panel service. No service or firewall was changed.'
