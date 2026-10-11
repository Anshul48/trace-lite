"""Direct in-process mutation profile vs the 2k regime (corrective pass).

cProfile cannot see TestClient's app thread, so this profiles ingest_note
directly (same pipeline stages as POST /api/ingest minus HTTP mgmt):
R01 publish txn, facet repair, synchronous projection drain, publication.
DB: copy of the qualified 2k run artifact (mutprobe_2k/serve.db).
Writes: direct_profile.json + console top-25 cumulative.
"""
from __future__ import annotations

import cProfile
import json
import pstats
import sys
import time
from io import StringIO
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parent
REPO = RUN_DIR.parents[3]
sys.path.insert(0, str(REPO / "src"))

from trace_lite.api.app import ingest_note  # noqa: E402
from trace_lite.filing.engine import FilingEngine  # noqa: E402
from trace_lite.filing.holon import HolonStore  # noqa: E402
from trace_lite.filing.taxonomy import Taxonomy  # noqa: E402
from trace_lite.store.database import Database  # noqa: E402


def main() -> None:
    db = Database(RUN_DIR / "serve.db", check_same_thread=False)
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    HolonStore(db.conn)
    per_note = []
    prof = cProfile.Profile()
    for i in range(3):
        s = time.perf_counter()
        prof.enable()
        aid = ingest_note(db, taxonomy, engine, f"directprobe-{i}.md",
                          f"# Direct {i}\n\ndirectvane{i:04d} profile probe "
                          f"directvane{i:04d} under backlog.\n")
        prof.disable()
        per_note.append({"note": i, "atom_id": aid,
                         "ms": round((time.perf_counter() - s) * 1000, 1)})
    sio = StringIO()
    pstats.Stats(prof, stream=sio).sort_stats(
        "cumulative").print_stats(25)
    print(sio.getvalue())
    (RUN_DIR / "direct_profile.json").write_text(
        json.dumps({"per_note": per_note}, indent=2), encoding="utf-8")
    print(json.dumps({"per_note": per_note}))


if __name__ == "__main__":
    main()
