.PHONY: setup test lint check format deploy logs clean

# Variables
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:
	@echo "Installing dependencies..."
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

test:
	@echo "Running tests..."
	$(PYTHON) -m pytest tests/

lint:
	@echo "Running ruff linter..."
	$(PYTHON) -m ruff check .

format:
	@echo "Formatting code with ruff..."
	$(PYTHON) -m ruff format .

check:
	@echo "Running type checks..."
	$(PYTHON) -m mypy src/ app.py robot_worker.py

deploy:
	@echo "Deploying via SFTP script..."
	$(PYTHON) sftp_deploy.py

logs:
	@echo "Fetching logs..."
	$(PYTHON) archive_scripts/get_logs.py

clean:
	@echo "Cleaning cache files..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
