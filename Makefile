.PHONY: test build smoke integration policies sync qa

test:
	python -m pytest -q tests --ignore=tests/e2e

policies:
	python scripts/ingest-policies.py

sync:
	python deployment/agent-manager/scripts/prepare-apps.py

build:
	docker compose up -d --build

smoke:
	bash scripts/smoke-test.sh

integration:
	python scripts/integration-test.py

qa: test sync
	python -m compileall -q agents banking_common mock_core demo_portal mcp_server
	node --check demo_portal/static/app.js

.PHONY: local scenarios score config
local:
	bash scripts/run-local.sh

scenarios:
	python scripts/run-executive-scenarios.py --report executive-scenario-report.json

score:
	python scripts/evaluate-scenarios.py executive-scenario-report.json

config:
	python scripts/check-managed-config.py --mode portal --env-file deployment/agent-manager/.env.portal.local

.PHONY: verify verify-full
verify:
	bash scripts/verify-executive.sh
verify-full:
	bash scripts/verify-executive.sh --full
