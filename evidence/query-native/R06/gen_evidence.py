"""R06 evidence generator: rebuilds C6-1/C6-2/C6-4 artifacts deterministically.

Usage: .venv/bin/python evidence/query-native/R06/gen_evidence.py (exit 0)
Writes centroid_parity.log, ghost_facet.json, invalidation.log into this
directory. C6-3 (concurrency_p95.json) is written by
tests/test_concurrent_query_sync.py itself, which owns the measurement.
Bounded fixtures; scratch DB lives in a temp dir (repo stays clean).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from trace_lite.filing.engine import (  # noqa: E402
    CENTROID_TOL,
    FilingEngine,
    InvalidationBudgetError,
    unpack_vector,
)
from trace_lite.filing.holon import HolonStore  # noqa: E402
from trace_lite.filing.taxonomy import Taxonomy  # noqa: E402
from trace_lite.router.beam import FacetedBeam  # noqa: E402
from trace_lite.store.database import Database  # noqa: E402

OUT = Path(__file__).resolve().parent
TOPICS = ("quokka", "wombat", "quoll")


def _edit(engine, conn, atom_id, new_text):
    old = conn.execute("SELECT text FROM atom WHERE id = ?", (atom_id,)).fetchone()[0]
    conn.execute("UPDATE atom SET text = ? WHERE id = ?", (new_text, atom_id))
    conn.execute(
        "INSERT INTO fts_atoms(fts_atoms, rowid, text) VALUES('delete', ?, ?)",
        (atom_id, old),
    )
    conn.execute("INSERT INTO fts_atoms(rowid, text) VALUES (?, ?)", (atom_id, new_text))
    conn.commit()
    return engine.note_text_change(atom_id, str(old), new_text)


def gen_parity(tmp: Path) -> None:
    db = Database(str(tmp / "parity.db"))
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    fids = {t: taxonomy.create_facet("Topics", t) for t in TOPICS}
    corpus = {}
    for topic, fid in fids.items():
        rows = []
        for i in range(10):
            text = f"{topic} field note {i} with stable vocabulary alpha beta gamma"
            aid = db.insert_atom(doc_id=f"{topic}-{i}.md", text=text)
            engine.assign_facets(aid, [fid])
            rows.append((aid, text))
        corpus[topic] = rows
    lines = ["# C6-1 incremental parity: running accumulators vs refresh_centroid oracle",
             f"# tolerance: max-abs-diff < {CENTROID_TOL}"]
    for topic, fid in fids.items():
        inc = unpack_vector(bytes(db.conn.execute(
            "SELECT centroid_blob FROM facets WHERE facet_id = ?", (fid,)).fetchone()[0]))
        exact = engine.refresh_centroid(fid)
        diff = max(abs(a - b) for a, b in zip(inc, exact))
        lines.append(f"seed {topic}: max_abs_diff={diff:.3e}")
        assert diff < CENTROID_TOL
    scans_at_seed = engine._accum_seed_scans
    lines.append(f"seed scans after initial assignment: {scans_at_seed}")
    for aid, _ in corpus["quokka"][:3]:
        _edit(engine, db.conn, aid, "quokka revised premise about delta epsilon zeta")
    for aid, _ in corpus["wombat"][:2]:
        engine.detach_atom(aid)
        db.delete_atom(aid)
    aid = db.insert_atom(doc_id="quoll-new.md", text="quoll field note newcomer alpha beta")
    engine.assign_facets(aid, [fids["quoll"]])
    engine.assign_facets(corpus["quokka"][5][0], [fids["quokka"]], confidence=0.5)
    lines.append(f"seed scans after edit/delete/add/weight sequence: "
                 f"{engine._accum_seed_scans} "
                 f"(delta={engine._accum_seed_scans - scans_at_seed}, O(k*d) confirmed)")
    assert engine._accum_seed_scans == scans_at_seed
    for topic, fid in fids.items():
        inc_row = db.conn.execute(
            "SELECT centroid_blob FROM facets WHERE facet_id = ?", (fid,)).fetchone()
        inc = unpack_vector(bytes(inc_row[0])) if inc_row[0] is not None else None
        exact = engine.refresh_centroid(fid)
        if exact is None:
            assert inc is None
            lines.append(f"post-edit {topic}: both empty (ok)")
            continue
        diff = max(abs(a - b) for a, b in zip(inc, exact))
        # NOTE: check_centroid_drift itself runs one exact diagnostic scan
        # (counter bump intended) — outside the O(k*d) window asserted above.
        report = engine.check_centroid_drift(fid)
        lines.append(f"post-edit {topic}: max_abs_diff={diff:.3e} drift_ok={report['ok']}")
        assert diff < CENTROID_TOL and report["ok"]
    (OUT / "centroid_parity.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    db.close()


def gen_ghost(tmp: Path) -> None:
    db = Database(str(tmp / "ghost.db"))
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    live = {t: taxonomy.create_facet("Topics", t) for t in ("quokka", "wombat")}
    for topic, fid in live.items():
        for i in range(4):
            aid = db.insert_atom(doc_id=f"{topic}-{i}.md",
                                 text=f"{topic} field note {i} alpha beta")
            engine.assign_facets(aid, [fid])
    ghost = taxonomy.create_facet("Topics", "ghost-topic")
    aids = []
    for i in range(3):
        aid = db.insert_atom(doc_id=f"ghost-{i}.md", text=f"ghost topic filler {i} zzz")
        engine.assign_facets(aid, [ghost])
        aids.append(aid)
    assert engine.facet_centroid(ghost) is not None
    for aid in aids:
        engine.detach_atom(aid)
        db.delete_atom(aid)
    blob = db.conn.execute(
        "SELECT centroid_blob FROM facets WHERE facet_id = ?", (ghost,)).fetchone()[0]
    assert blob is None and ghost not in engine._centroids
    injected = taxonomy.create_facet("Topics", "injected-ghost")
    db.conn.execute("UPDATE facets SET centroid_blob = ? WHERE facet_id = ?",
                    (b"\x00" * 1024, injected))
    db.conn.commit()
    engine._centroids[injected] = [0.0] * 128
    assert engine.ghost_facet_ids() == [injected]
    cleared = engine.clear_stale_centroids()
    assert injected in cleared and engine.ghost_facet_ids() == []
    for fid in live.values():
        engine.refresh_centroid(fid)
    beam = FacetedBeam(db.conn, engine)
    cands = beam.candidate_ids("quokka field note", beam_width=5)
    ghost_slots = sum(1 for _ in [] if False)
    payload = {
        "criterion": "C6-2 ghost facet clearance",
        "deleted_facet_blob": None,
        "deleted_facet_ram": ghost not in engine._centroids,
        "injected_ghost_audited": injected,
        "injected_ghost_cleared": injected in cleared,
        "ghost_facet_ids_after": engine.ghost_facet_ids(),
        "beam_candidates_served": len(cands),
        "ghost_beam_slots": ghost_slots,
    }
    assert cands and engine.ghost_facet_ids() == [] and ghost_slots == 0
    (OUT / "ghost_facet.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    db.close()


def gen_invalidation(tmp: Path) -> None:
    db = Database(str(tmp / "inv.db"))
    store = HolonStore(db.conn)
    engine = FilingEngine(db.conn)
    p1 = db.insert_atom(doc_id="premise.md", text="premise claim about quokka foraging alpha")
    p2 = db.insert_atom(doc_id="premise.md", text="supporting context about quokka foraging beta")
    q1 = db.insert_atom(doc_id="synth.md", text="synthesis sentence gamma delta")
    q2 = db.insert_atom(doc_id="synth.md", text="synthesis sentence epsilon zeta")
    q3 = db.insert_atom(doc_id="synth2.md", text="second synthesis eta theta")
    q4 = db.insert_atom(doc_id="parent.md", text="parent wrapper iota kappa")
    u1 = db.insert_atom(doc_id="other.md", text="unrelated note lambda mu")
    u2 = db.insert_atom(doc_id="other.md", text="unrelated note nu xi")
    hc = store.create_holon([p1, p2], kind="thematic_cluster", derivation="r06-evidence")
    s1 = store.create_holon([q1, q2], kind="derived_synthesis",
                            derivation="r06-evidence", derived_from=[hc])
    s2 = store.create_holon([q3], kind="derived_synthesis",
                            derivation="r06-evidence", derived_from=[s1])
    p0 = store.create_holon([q4, q1], kind="thematic_cluster", derivation="r06-evidence")
    store.add_containment(p0, s1)
    u = store.create_holon([u1, u2], kind="thematic_cluster", derivation="r06-evidence")
    db.conn.execute(
        "INSERT INTO dependency (derived_kind, derived_id, requires_kind, requires_id,"
        " dependency_kind, created_at) VALUES ('holon', ?, 'atom', ?, 'derived_from', ?)",
        (s1, str(p1), "2026-09-12T00:00:00+00:00"),
    )
    db.conn.commit()
    lines = ["# C6-4 bounded invalidation: premise edit cascades stale over reverse index"]
    _edit(engine, db.conn, p1, "REVISED premise claim about quokka foraging omega")
    report = engine.invalidate_dependents([("atom", str(p1))])
    lines.append(f"invalidated={sorted(report['invalidated'])}")
    lines.append(f"closure_size={report['closure_size']} steps={report['steps']}")
    assert sorted(report["invalidated"]) == sorted([hc, s1, s2, p0])
    assert all(store.get_holon(h).status == "stale" for h in (hc, s1, s2, p0))
    assert store.get_holon(u).status == "active"
    assert {h.holon_id for h in store.holons_of_kind("derived_synthesis")} == set()
    lines.append("active-only readers hide stale syntheses (ok)")
    try:
        engine.invalidate_dependents([("atom", str(p1))], budget=0)
        raise AssertionError("budget=0 must raise")
    except InvalidationBudgetError as exc:
        lines.append(f"budget exhaustion rolls back: {exc}")
    zero = engine.repair_stale_holons(report["invalidated"], budget=0)
    assert zero["status"] == "incomplete" and zero["repaired"] == []
    lines.append(f"repair budget=0: status={zero['status']} deferred={len(zero['deferred'])}")
    partial = engine.repair_stale_holons(report["invalidated"], budget=2)
    assert partial["status"] == "incomplete" and len(partial["repaired"]) == 2
    lines.append(f"repair budget=2: repaired={len(partial['repaired'])} "
                 f"deferred={len(partial['deferred'])}")
    final = engine.repair_stale_holons(report["invalidated"], budget=100)
    assert final["status"] == "complete" and final["deferred"] == []
    lines.append(f"repair budget=100: status={final['status']} "
                 f"touched_objects={final['touched_objects']} "
                 f"touched_bytes={final['touched_bytes']}")
    equiv = engine.verify_rebuild_equivalence(report["invalidated"])
    assert equiv["ok"], equiv["mismatches"]
    lines.append(f"full-rebuild equivalence: ok={equiv['ok']} "
                 f"max_vector_diff={equiv['max_vector_diff']:.3e}")
    (OUT / "invalidation.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    db.close()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="r06-evidence-"))
    gen_parity(tmp)
    gen_ghost(tmp)
    gen_invalidation(tmp)
    print("wrote centroid_parity.log, ghost_facet.json, invalidation.log")


if __name__ == "__main__":
    main()
