#!/usr/bin/env bash
set -euo pipefail
sample_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
docker build --tag stable-row-identity-review "$sample_dir"
docker run --rm --network none --cpus 4 --memory 8g stable-row-identity-review
