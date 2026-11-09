#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /logs/verifier
chmod 700 /logs/verifier /tests
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json
# -Es: ignore environment and user site, but keep the script directory on
# sys.path so the controller can import its sibling trusted modules.
/opt/python/bin/python -Es /tests/controller.py
test "$(cat /logs/verifier/reward.txt)" = "1"
