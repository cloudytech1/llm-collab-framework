.PHONY: fix lint typecheck test qa

# Auto-fix all fixable lint and formatting issues
fix:
	ruff check --fix orchestrator.py
	ruff format orchestrator.py

# Check for lint issues (no changes)
lint:
	ruff check orchestrator.py
	ruff format --check orchestrator.py

# Static type checking
typecheck:
	mypy orchestrator.py

# Run tests with coverage
test:
	pytest tests/ --cov=orchestrator --cov-report=term-missing

# Full QA pipeline: fix first, then check, then test
qa: fix lint typecheck test
