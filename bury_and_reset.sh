#!/usr/bin/env bash
# ==============================================================================
# bury_and_reset.sh
#
# Trace-Lite: Legacy Prototype Burial & Clean Foundation Reset Script
#
# This script:
# 1. Commits any pending legacy changes.
# 2. Archives the legacy v1 prototype state into branch `archive/v1-legacy-scaffold`.
# 3. Tags the archive commit as `v1.0-scaffold-archived`.
# 4. Pushes the archive branch and tag to `origin` (if remote exists).
# 5. Checks out `main`.
# 6. Purges obsolete prototype files and LanceDB/RAPTOR legacy modules.
# 7. Initializes clean, production-grade foundations (pyproject.toml, README.md,
#    src/trace_lite, tests).
# 8. Commits the clean slate to `main`.
# ==============================================================================

set -euo pipefail

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
cd "$REPO_DIR"

echo -e "${BOLD}${BLUE}================================================================${NC}"
echo -e "${BOLD}${BLUE}  Trace-Lite: Bury Legacy Prototype & Initialize Clean Slate   ${NC}"
echo -e "${BOLD}${BLUE}================================================================${NC}"
echo -e "Repository root: ${REPO_DIR}\n"

# Verify git repository
if [ ! -d ".git" ]; then
    echo -e "${RED}Error: Not a git repository (${REPO_DIR}). Aborting.${NC}" >&2
    exit 1
fi

# Ensure local git identity is configured
git config user.name "Anshul"
git config user.email "anshul@example.com"

# Step 1: Commit any pending legacy changes
echo -e "${BOLD}[1/7] Checking for pending uncommitted changes...${NC}"
if [ -n "$(git status --porcelain)" ]; then
    echo -e "  Staging and committing pending uncommitted changes..."
    git add -A
    git commit -m "chore(archive): save uncommitted work prior to legacy burial" || true
    echo -e "  ${GREEN}✓ Legacy changes committed.${NC}"
else
    echo -e "  ${GREEN}✓ Working tree is clean.${NC}"
fi

# Step 2: Create / checkout archive branch
ARCHIVE_BRANCH="archive/v1-legacy-scaffold"
echo -e "\n${BOLD}[2/7] Archiving legacy state to branch '${ARCHIVE_BRANCH}'...${NC}"
if git show-ref --verify --quiet "refs/heads/${ARCHIVE_BRANCH}"; then
    echo -e "  Branch '${ARCHIVE_BRANCH}' already exists."
else
    git branch "${ARCHIVE_BRANCH}"
    echo -e "  ${GREEN}✓ Created branch '${ARCHIVE_BRANCH}'.${NC}"
fi

# Step 3: Tag legacy snapshot
ARCHIVE_TAG="v1.0-scaffold-archived"
echo -e "\n${BOLD}[3/7] Tagging legacy snapshot as '${ARCHIVE_TAG}'...${NC}"
if git rev-parse "${ARCHIVE_TAG}" >/dev/null 2>&1; then
    echo -e "  ${YELLOW}Tag '${ARCHIVE_TAG}' already exists.${NC}"
else
    git tag -a "${ARCHIVE_TAG}" -m "Archived v1 legacy prototype scaffold"
    echo -e "  ${GREEN}✓ Created tag '${ARCHIVE_TAG}'.${NC}"
fi

# Step 4: Push archive branch and tag to origin if remote exists
echo -e "\n${BOLD}[4/7] Pushing archive to remote 'origin' if reachable...${NC}"
if git remote | grep -q "^origin$"; then
    echo -e "  Attempting push to origin (${ARCHIVE_BRANCH} and ${ARCHIVE_TAG})..."
    git push origin "${ARCHIVE_BRANCH}" || echo -e "  ${YELLOW}Notice: Failed to push branch to origin. Continuing.${NC}"
    git push origin "${ARCHIVE_TAG}" || echo -e "  ${YELLOW}Notice: Failed to push tag to origin. Continuing.${NC}"
else
    echo -e "  ${YELLOW}No remote 'origin' configured. Skipping push.${NC}"
fi

# Step 5: Switch back to main branch
echo -e "\n${BOLD}[5/7] Ensuring HEAD is on main branch...${NC}"
git checkout main

# Step 6: Purge obsolete legacy prototype files
echo -e "\n${BOLD}[6/7] Removing obsolete prototype files...${NC}"
OBSOLETE_ITEMS=(
    "web-ui"
    "benchmarks"
    "scripts"
    "get_models.py"
    "uv.lock"
    "src/trace_lite.egg-info"
    ".github/workflows/obsidian-release.yml"
    ".github/workflows/benchmark.yml"
)

for item in "${OBSOLETE_ITEMS[@]}"; do
    if [ -e "$item" ]; then
        echo -e "  Removing $item..."
        git rm -rf --ignore-unmatch "$item" 2>/dev/null || true
        rm -rf "$item"
    fi
done

# Wipe old modules inside src/trace_lite and tests
rm -rf src/trace_lite tests docs/dev docs/program
mkdir -p src/trace_lite tests docs
touch src/trace_lite/py.typed

# Clean caches
rm -rf .pytest_cache __pycache__ .coverage htmlcov .mypy_cache .ruff_cache

# Step 7: Write fresh, production-grade foundation files
echo -e "\n${BOLD}[7/7] Writing clean modern production foundation files...${NC}"

cat << 'EOF' > pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "trace-lite"
version = "0.2.0"
description = "High-performance single-node filing cabinet and local memory substrate"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = [
    "pydantic>=2.7.0",
    "rich>=13.7.0",
    "typer>=0.12.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.4.0",
    "mypy>=1.10.0",
]

[project.scripts]
tl = "trace_lite.cli:app"
trace-lite = "trace_lite.cli:app"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF

cat << 'EOF' > README.md
# trace-lite

A lightweight, single-node, high-performance filing cabinet and local memory substrate.

## Core Philosophy
- **Deterministic & Fast**: Zero LLM dependencies for core indexing, atomization, and storage.
- **Append-only Provenance**: Content-addressed atoms with exact source byte spans and SHA-256 hashes.
- **Single-Node Simplicity**: Pure SQLite foundation; no dual-storage desync, no distributed coordination tax.
- **Filing Cabinet Design**: Store each item once; organize into multi-dimensional collections and browsable views.

## Architecture
- `src/trace_lite/store/`: Append-only SQLite event ledger & atom storage.
- `src/trace_lite/filing/`: Local holons, multi-membership indexes, and topic collections.
- `src/trace_lite/retrieval/`: Fast hybrid lexical + sparse activation retrieval.

## Development
```bash
uv sync --all-extras
pytest
```
EOF

cat << 'EOF' > src/trace_lite/__init__.py
"""Trace-Lite: High-performance single-node filing cabinet and local memory substrate."""

__version__ = "0.2.0"
EOF

cat << 'EOF' > src/trace_lite/cli.py
"""CLI entrypoint for trace-lite."""

import typer
from rich.console import Console

app = typer.Typer(help="trace-lite: single-node filing cabinet and local memory substrate")
console = Console()


@app.command()
def status():
    """Display status of the local filing cabinet."""
    console.print("[bold green]trace-lite[/bold green] v0.2.0: Ready for development.")


@app.command()
def version():
    """Show version."""
    console.print("trace-lite 0.2.0")


if __name__ == "__main__":
    app()
EOF

cat << 'EOF' > tests/__init__.py
"""Tests for trace-lite."""
EOF

cat << 'EOF' > tests/test_scaffold.py
"""Basic scaffold verification test for trace-lite."""

from trace_lite import __version__
from trace_lite.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_version():
    assert __version__ == "0.2.0"


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.stdout
EOF

git add -A
git commit -m "chore(reset): initialize clean production-grade foundation v0.2.0" || true

echo -e "\n${BOLD}${GREEN}================================================================${NC}"
echo -e "${BOLD}${GREEN}  Trace-Lite Reset Complete! Ready for serious dev work.       ${NC}"
echo -e "${BOLD}${GREEN}================================================================${NC}\n"
