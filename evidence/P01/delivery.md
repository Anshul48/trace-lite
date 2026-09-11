# P01 Delivery — Clean Foundation Reset & Modern Packaging

- Packet: `docs/builds/filing-cabinet/packets/P01-clean-foundation-reset.md` (rev 2026-09-11.2)
- Candidate commit: `8f2032b` ("chore(reset): initialize clean production-grade foundation v0.2.0")
- Archive commit: `ff67b58` ("chore(archive): save uncommitted work prior to legacy burial")
- Archive branch: `archive/v1-legacy-scaffold` (exists locally; remote push skipped — no reachable origin)
- Archive tag: `v1.0-scaffold-archived`

## Actions executed
1. Verified `bury_and_reset.sh` `OBSOLETE_ITEMS` does NOT list `obsidian-plugin/` (CRIT-01 safe) and
   only wipes `src/trace_lite`, `tests`, `docs/dev`, `docs/program` — `docs/builds/filing-cabinet/` untouched.
2. Ran `./bury_and_reset.sh` — completed, clean working tree (`git status --short` empty).
3. Purged: `web-ui/`, `benchmarks/`, `scripts/`, `get_models.py`, `uv.lock`, `src/trace_lite.egg-info`,
   `.github/workflows/{obsidian-release,benchmark}.yml`, legacy `src/trace_lite/*`, legacy `tests/*`.
4. Fresh foundation written: `pyproject.toml` (hatchling, py>=3.11, pydantic2/typer/rich, `tl` entrypoint),
   `README.md`, `src/trace_lite/{__init__,cli}.py`, `tests/{__init__,test_scaffold}.py`.

## Preserved
- `obsidian-plugin/` intact (incl. `main.ts` targeting `http://127.0.0.1:8420`).
- `docs/builds/filing-cabinet/` records intact.

## Verification (builder-run)
- `.venv/bin/python -m pytest tests/test_scaffold.py -v` → **2 passed**.
- `python -m trace_lite.cli version` → `trace-lite 0.2.0`, exit 0.
