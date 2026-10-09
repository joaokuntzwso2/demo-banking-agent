#!/usr/bin/env bash
set -euo pipefail
curl --fail -X POST -H "X-Internal-Key: ${BANK_INTERNAL_KEY:-local-demo-internal-key}" http://localhost:8100/admin/reset
