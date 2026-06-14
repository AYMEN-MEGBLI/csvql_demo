# csvql Makefile
# Usage: make build | make test | make bench | make clean

.PHONY: build test bench clean lint install dev

# ── Build C extension ──────────────────────────────────────────────────
build:
	pip install -e . --no-build-isolation

build-inplace:
	python setup.py build_ext --inplace

# ── Install dev dependencies ───────────────────────────────────────────
dev:
	pip install -e ".[dev]"

# ── Tests ──────────────────────────────────────────────────────────────
test:
	pytest tests/ -v --tb=short

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

# ── Benchmarks ────────────────────────────────────────────────────────
bench:
	python benchmarks/bench_vs_pandas.py

bench-full:
	pytest benchmarks/ --benchmark-json=bench_results.json
	python benchmarks/bench_report.py bench_results.json

# ── Generate large test file ───────────────────────────────────────────
gen-data:
	python tests/data/generate.py

# ── Code quality ───────────────────────────────────────────────────────
lint:
	ruff check csvql/ tests/
	mypy csvql/

fmt:
	black csvql/ tests/ benchmarks/

# ── Clean ─────────────────────────────────────────────────────────────
clean:
	find . -name "*.so"      -delete
	find . -name "*.o"       -delete
	find . -name "*.pyc"     -delete
	find . -name "__pycache__" -type d -exec rm -rf {} +
	rm -rf build/ dist/ *.egg-info/ .mypy_cache/ .ruff_cache/

# ── CLI quick test ────────────────────────────────────────────────────
demo:
	@echo "Running demo query on tests/data/sales.csv ..."
	python -c "
from csvql import query
r = query(\"SELECT category, SUM(amount) AS total FROM 'tests/data/sales.csv' GROUP BY category ORDER BY total DESC\")
r.print_table()
"