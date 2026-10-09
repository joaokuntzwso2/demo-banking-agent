#!/usr/bin/env bash
set -euo pipefail
curl --fail -X POST -H "X-Internal-Key: ${BANK_RESET_KEY:-local-demo-reset-capability}" http://localhost:8100/admin/reset
