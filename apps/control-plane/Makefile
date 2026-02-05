PYTHON ?= python3
PYTHONPATH ?= app

.PHONY: install fmt lint test migrate

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev,test]"

fmt:
	$(PYTHON) -m ruff format app tests

lint:
	$(PYTHON) -m ruff check app tests

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests

migrate:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m alembic upgrade head
