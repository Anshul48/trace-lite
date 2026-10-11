"""Lane A: edit-phase cost + fold-gate scaling (post-fix evidence).

Phase 1: ingest 400 notes (as profile_ingest.py).
Phase 2: edit 50 of them (changed payload) via ingest_note; time each edit.
Phase 3: time single fold-adds on synthetic holons of size ~500/1000/2000
         (digest-gate scaling bound).

Outputs JSON to the run dir. DBs in /tmp (scratch).
"""
from __future__ import annotations

import json
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


def make_note(i: int, rev: int = 0) -> str:
    tags = " ".join(f"#tag{(i + k) % 12}" for k in range(3))
    para = (
        f"# Note {i} rev{rev}\n\n{tags}\n\n"
        "Batching improves throughput under low contention while WAL mode "
        "allows one writer with concurrent readers. "
        f"Revision marker {rev} for note {i}. "
    )
    return para * 6


def build_stack(db_path: Path):
    db = Database(db_path, check_same_thread=False)
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    HolonStore(db.conn)
    return db, taxonomy, engine


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="lanea-edit-"))
    db, taxonomy, engine = build_stack(tmp / "edit.db")
    for i in range(400):
        ingest_note(db, taxonomy, engine, f"notes/doc{i:05d}.md", make_note(i))
    edit_times: list[float] = []
    for i in range(0, 400, 8):
        s = time.perf_counter()
        ingest_note(db, taxonomy, engine, f"notes/doc{i:05d}.md",
                    make_note(i, rev=1))
        edit_times.append(time.perf_counter() - s)
    # correctness spot checks after edits
    n_atoms = db.conn.execute("SELECT COUNT(*) FROM atom").fetchone()[0]
    n_cur = db.conn.execute(
        "SELECT COUNT(*) FROM source_revision WHERE status = 'current'"
    ).fetchone()[0]
    new_held = db.conn.execute(
        "SELECT COUNT(*) FROM holon_members m JOIN holon_state s"
        " ON s.holon_id = m.holon_id JOIN atom a ON a.id = m.atom_id"
        " WHERE s.kind = 'thematic_cluster' AND a.text LIKE '%rev1%'"
    ).fetchone()[0]
    pending = db.conn.execute(
        "SELECT COUNT(*) FROM projection_job WHERE state IN ('pending','claimed')"
    ).fetchone()[0]
    drift_ok = HolonStore(db.conn).check_drift(
        db.conn.execute("SELECT holon_id FROM holon_state"
                        " WHERE kind = 'thematic_cluster'").fetchone()[0]
    )["ok"]
    db.close()

    # Phase 3: fold-add latency vs holon size (synthetic, direct API).
    tmp2 = Path(tempfile.mkdtemp(prefix="lanea-gate-"))
    db2 = Database(tmp2 / "gate.db", check_same_thread=False)
    store = HolonStore(db2.conn)
    aids = [db2.insert_atom(f"g-{i}.md", f"gate note {i} alpha beta gamma {i}")
            for i in range(2010)]
    hid = store.create_holon(aids[:2], kind="thematic_cluster")
    gate: dict[str, float] = {}
    for size in (500, 1000, 2000):
        while len(store.get_holon(hid).atom_ids) < size - 5:
            nxt = len(store.get_holon(hid).atom_ids)
            store.add_members(hid, [aids[nxt]])
        samples = []
        for _ in range(5):
            nxt = len(store.get_holon(hid).atom_ids)
            s = time.perf_counter()
            store.add_members(hid, [aids[nxt]])
            samples.append((time.perf_counter() - s) * 1000.0)
        gate[str(size)] = round(sum(samples) / len(samples), 2)
    db2.close()

    out = {
        "edits": len(edit_times),
        "edit_mean_ms": round(sum(edit_times) / len(edit_times) * 1000, 2),
        "edit_max_ms": round(max(edit_times) * 1000, 2),
        "atoms": n_atoms,
        "current_revisions": n_cur,
        "edited_atoms_grouped": new_held,
        "pending_jobs": pending,
        "corpus_drift_ok": bool(drift_ok),
        "fold_add_ms_by_size": gate,
    }
    with open(RUN_DIR / "profile_edit_POSTFIX.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
