"""Lane A profiler: per-note ingest_note cost vs corpus size (pre-repair baseline).

Mirrors the online-mutation operating point: one ingest_note() per doc
(R01 publish + facet repair/map + sync drain + filing generation), timed
per note. Also cProfiles one 20-note window at N~200 for attribution.

Usage: PYTHONPATH=<worktree>/src python3 profile_ingest.py [--notes N]
Outputs: run-dir JSON + console summary. DB lives in /tmp (scratch).
"""
from __future__ import annotations

import cProfile
import json
import pstats
import sys
import tempfile
import time
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(RUN_DIR.parents[3] / "src"))

from trace_lite.api.app import ingest_note  # noqa: E402
from trace_lite.filing.engine import FilingEngine  # noqa: E402
from trace_lite.filing.holon import HolonStore  # noqa: E402
from trace_lite.filing.taxonomy import Taxonomy  # noqa: E402
from trace_lite.store.database import Database  # noqa: E402


def make_note(i: int) -> str:
    # ~1KB note with tags + prose (facet hints + extractor load, S6-like).
    tags = " ".join(f"#tag{(i + k) % 12}" for k in range(3))
    para = (
        f"# Note {i}\n\n{tags}\n\n"
        "Batching improves throughput under low contention while WAL mode "
        "allows one writer with concurrent readers. "
        "Throughput matches the unbatched path when contention rises. "
    )
    return para * 6


def build_stack(db_path: Path):
    db = Database(db_path, check_same_thread=False)
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    HolonStore(db.conn)  # lifespan parity: grouping DDL before serving
    return db, taxonomy, engine


def main() -> None:
    total = int(sys.argv[sys.argv.index("--notes") + 1]) if "--notes" in sys.argv else 400
    tmp = Path(tempfile.mkdtemp(prefix="lanea-prof-"))
    db, taxonomy, engine = build_stack(tmp / "prof.db")
    per_note: list[float] = []
    t0 = time.perf_counter()
    prof = cProfile.Profile()
    prof_window = (180, 200) if total >= 200 else (max(0, total - 20), total)
    for i in range(total):
        if i == prof_window[0]:
            prof.enable()
        s = time.perf_counter()
        ingest_note(db, taxonomy, engine, f"notes/doc{i:05d}.md", make_note(i))
        per_note.append(time.perf_counter() - s)
        if i + 1 == prof_window[1]:
            prof.disable()
    wall = time.perf_counter() - t0
    prof_path = RUN_DIR / "cprofile_window.txt"
    with open(prof_path, "w") as fh:
        ps = pstats.Stats(prof, stream=fh).sort_stats("cumulative")
        ps.print_stats(40)
    # windowed means
    wins = {}
    step = 50
    for lo in range(0, total, step):
        seg = per_note[lo:lo + step]
        wins[f"{lo}-{lo + len(seg) - 1}"] = {
            "mean_ms": round(sum(seg) / len(seg) * 1000, 2),
            "max_ms": round(max(seg) * 1000, 2),
        }
    out = {
        "notes": total,
        "wall_s": round(wall, 2),
        "notes_per_s": round(total / wall, 2),
        "windows": wins,
        "db_dir": str(tmp),
        "cprofile": str(prof_path),
    }
    with open(RUN_DIR / "profile_baseline.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out, indent=2))
    db.close()


if __name__ == "__main__":
    main()
