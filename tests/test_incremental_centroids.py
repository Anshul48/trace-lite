"""R06 durable tests: O(k*d) incremental centroids + bounded invalidation.

C6-1: running accumulators agree with the exact ``refresh_centroid`` oracle
within 1e-5 across insert/edit/delete/weight sequences, with no full
rescans after seeding (seed-scan counter stays flat).
C6-2: zero-member facets hold no centroid (blob/RAM/beam) — 0 ghost slots.
C6-4: premise edits cascade ``stale`` over the dependency reverse index;
budget-limited repair returns ``incomplete``; full drain restores ``active``
with full-rebuild equivalence.
"""

from __future__ import annotations

import sqlite3

import pytest

from trace_lite.filing.engine import (
    CENTROID_TOL,
    FilingEngine,
    InvalidationBudgetError,
    unpack_vector,
)
from trace_lite.filing.holon import HolonStore
from trace_lite.filing.taxonomy import Taxonomy
from trace_lite.router.beam import FacetedBeam
from trace_lite.store.database import Database

TOPICS = ("quokka", "wombat", "quoll")


def _setup(tmp_path, name="t.db"):
    db = Database(str(tmp_path / name))
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    fids = {t: taxonomy.create_facet("Topics", t) for t in TOPICS}
    return db, taxonomy, engine, fids


def _seed_corpus(db, engine, fids, per_facet=10):
    """Deterministic corpus; returns {facet: [(atom_id, text)]}."""
    corpus = {}
    for topic, fid in fids.items():
        rows = []
        for i in range(per_facet):
            text = f"{topic} field note {i} with stable vocabulary alpha beta gamma"
            aid = db.insert_atom(doc_id=f"{topic}-{i}.md", text=text)
            engine.assign_facets(aid, [fid])
            rows.append((aid, text))
        corpus[topic] = rows
    return corpus


def _exact_blob(engine, fid):
    """Independent oracle: full recompute via the untouched refresh path."""
    return engine.refresh_centroid(fid)


def _assert_parity(engine, fid, tol=CENTROID_TOL):
    inc_row = engine.conn.execute(
        "SELECT centroid_blob FROM facets WHERE facet_id = ?", (fid,)
    ).fetchone()
    inc = unpack_vector(bytes(inc_row[0])) if inc_row[0] is not None else None
    exact = _exact_blob(engine, fid)
    if exact is None:
        assert inc is None, f"facet {fid}: incremental blob present, oracle empty"
        return
    assert inc is not None, f"facet {fid}: oracle present, incremental blob missing"
    diff = max(abs(a - b) for a, b in zip(inc, exact))
    assert diff < tol, f"facet {fid}: parity drift {diff:.3g} >= {tol}"


def _edit_atom_text(engine, conn, atom_id, new_text):
    """In-place premise edit: atom row + FTS + accumulator fold."""
    old = conn.execute("SELECT text FROM atom WHERE id = ?", (atom_id,)).fetchone()[0]
    conn.execute("UPDATE atom SET text = ? WHERE id = ?", (new_text, atom_id))
    conn.execute(
        "INSERT INTO fts_atoms(fts_atoms, rowid, text) VALUES('delete', ?, ?)",
        (atom_id, old),
    )
    conn.execute("INSERT INTO fts_atoms(rowid, text) VALUES (?, ?)", (atom_id, new_text))
    conn.commit()
    return engine.note_text_change(atom_id, str(old), new_text)


# -- C6-1 incremental parity -------------------------------------------

def test_incremental_parity_over_edit_sequence(tmp_path):
    db, _, engine, fids = _setup(tmp_path)
    corpus = _seed_corpus(db, engine, fids)
    for fid in fids.values():
        _assert_parity(engine, fid)

    qfid, wfid = fids["quokka"], fids["wombat"]
    # Edit: in-place premise text change on three atoms.
    for aid, _ in corpus["quokka"][:3]:
        _edit_atom_text(engine, db.conn, aid, "quokka revised premise about delta epsilon zeta")
    # Delete: detach + atom removal on two atoms.
    for aid, _ in corpus["wombat"][:2]:
        engine.detach_atom(aid)
        db.delete_atom(aid)
    # Add: fresh member to a third facet.
    aid = db.insert_atom(doc_id="quoll-new.md", text="quoll field note newcomer alpha beta")
    engine.assign_facets(aid, [fids["quoll"]])
    # Weight change: confidence update on an existing membership.
    engine.assign_facets(corpus["quokka"][5][0], [qfid], confidence=0.5)
    _ = (qfid, wfid)
    for fid in fids.values():
        _assert_parity(engine, fid)
        report = engine.check_centroid_drift(fid)
        assert report["ok"], report


def test_no_full_rescans_after_seeding(tmp_path):
    db, _, engine, fids = _setup(tmp_path)
    corpus = _seed_corpus(db, engine, fids)
    for fid in fids.values():
        _assert_parity(engine, fid)  # oracle refresh, not a seed scan
    baseline = engine._accum_seed_scans
    assert baseline > 0, "expected seed scans during initial assignment"
    for round_ in range(10):
        for topic, rows in corpus.items():
            aid, _ = rows[round_ % len(rows)]
            _edit_atom_text(engine, db.conn, aid, f"{topic} edit round {round_} kappa lambda")
    for fid in fids.values():
        _assert_parity(engine, fid)
    assert engine._accum_seed_scans == baseline, (
        f"edits triggered {engine._accum_seed_scans - baseline} full rescans"
    )


def test_weight_change_keeps_exact_parity(tmp_path):
    db, _, engine, fids = _setup(tmp_path)
    corpus = _seed_corpus(db, engine, fids, per_facet=4)
    fid = fids["quokka"]
    before = bytes(engine.conn.execute(
        "SELECT centroid_blob FROM facets WHERE facet_id = ?", (fid,)).fetchone()[0])
    engine.assign_facets(corpus["quokka"][0][0], [fid], confidence=0.25)
    after = bytes(engine.conn.execute(
        "SELECT centroid_blob FROM facets WHERE facet_id = ?", (fid,)).fetchone()[0])
    assert before == after, "unweighted centroid must not move on confidence change"
    _assert_parity(engine, fid)


def test_drift_hook_detects_corruption_and_reseed_heals(tmp_path):
    _, _, engine, fids = _setup(tmp_path)
    fid = fids["quokka"]
    conn = engine.conn
    aid = conn.execute(
        "INSERT INTO atom (doc_id, content_hash, start_byte, end_byte, text)"
        " VALUES ('d.md', zeroblob(32), 0, 4, 'quokka drift probe')"
    ).lastrowid
    conn.execute("INSERT INTO fts_atoms(rowid, text) VALUES (?, ?)", (aid, "quokka drift probe"))
    conn.commit()
    engine.assign_facets(aid, [fid])
    assert engine.check_centroid_drift(fid)["ok"]
    # Corrupt the running sum behind the engine's back.
    engine._accum[fid]["sum"][0] += 5.0
    bad = engine.check_centroid_drift(fid)
    assert not bad["ok"], "drift hook must fire on corrupted accumulators"
    assert bad["max_abs_diff"] > CENTROID_TOL
    engine.reseed_accum(fid)
    assert engine.check_centroid_drift(fid)["ok"]


def test_no_schema_change_from_engine_ops(tmp_path):
    db, _, engine, fids = _setup(tmp_path)
    before_tables = {
        r[0] for r in db.conn.execute("SELECT name FROM sqlite_master").fetchall()
    }
    before_version = db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    corpus = _seed_corpus(db, engine, fids, per_facet=3)
    for fid in fids.values():
        engine.check_centroid_drift(fid)
        engine.ghost_facet_ids()
    for aid, _ in corpus["quokka"][:2]:
        _edit_atom_text(engine, db.conn, aid, "quokka schema probe edit")
    after_tables = {
        r[0] for r in db.conn.execute("SELECT name FROM sqlite_master").fetchall()
    }
    after_version = db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert after_tables == before_tables, after_tables ^ before_tables
    assert after_version == before_version


# -- C6-2 ghost facets ---------------------------------------------------

def test_ghost_facets_hold_no_beam_slots(tmp_path):
    db, taxonomy, engine, fids = _setup(tmp_path)
    corpus = _seed_corpus(db, engine, fids, per_facet=4)
    ghost_fid = taxonomy.create_facet("Topics", "ghost-topic")
    aids = []
    for i in range(3):
        aid = db.insert_atom(doc_id=f"ghost-{i}.md", text=f"ghost topic filler {i} zzz")
        engine.assign_facets(aid, [ghost_fid])
        aids.append(aid)
    assert engine.facet_centroid(ghost_fid) is not None
    # Delete every member through the incremental path.
    for aid in aids:
        engine.detach_atom(aid)
        db.delete_atom(aid)
    blob = db.conn.execute(
        "SELECT centroid_blob FROM facets WHERE facet_id = ?", (ghost_fid,)
    ).fetchone()[0]
    assert blob is None, "empty facet must clear its centroid blob"
    assert ghost_fid not in engine._centroids
    assert engine.ghost_facet_ids() == []
    for fid in fids.values():
        _assert_parity(engine, fid)
    # Beam over the live corpus: ghost facet contributes zero candidates.
    beam = FacetedBeam(db.conn, engine)
    cands = beam.candidate_ids("quokka field note", beam_width=5)
    assert cands, "beam must still serve live facets"
    assert engine.ghost_facet_ids() == []


def test_injected_ghost_is_audited_and_cleared(tmp_path):
    db, taxonomy, engine, fids = _setup(tmp_path)
    _seed_corpus(db, engine, fids, per_facet=3)
    ghost_fid = taxonomy.create_facet("Topics", "injected-ghost")
    # Simulate a stale writer: blob present, zero members, stale RAM entry.
    db.conn.execute(
        "UPDATE facets SET centroid_blob = ? WHERE facet_id = ?",
        (b"\x00" * 1024, ghost_fid),
    )
    db.conn.commit()
    engine._centroids[ghost_fid] = [0.0] * 128
    assert engine.ghost_facet_ids() == [ghost_fid]
    cleared = engine.clear_stale_centroids()
    assert ghost_fid in cleared
    assert engine.ghost_facet_ids() == []


# -- C6-4 bounded invalidation + repair ----------------------------------

def _holon_fixture(db):
    """Container + 2-level synthesis chain + containment parent + bystander."""
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
    hc = store.create_holon([p1, p2], kind="thematic_cluster", derivation="r06-fixture")
    s1 = store.create_holon([q1, q2], kind="derived_synthesis",
                            derivation="r06-fixture", derived_from=[hc])
    s2 = store.create_holon([q3], kind="derived_synthesis",
                            derivation="r06-fixture", derived_from=[s1])
    p0 = store.create_holon([q4, q1], kind="thematic_cluster", derivation="r06-fixture")
    store.add_containment(p0, s1)
    u = store.create_holon([u1, u2], kind="thematic_cluster", derivation="r06-fixture")
    # Premise edge: synthesis s1 directly requires the premise atom.
    db.conn.execute(
        "INSERT INTO dependency (derived_kind, derived_id, requires_kind, requires_id,"
        " dependency_kind, created_at) VALUES ('holon', ?, 'atom', ?, 'derived_from', ?)",
        (s1, str(p1), "2026-09-12T00:00:00+00:00"),
    )
    db.conn.commit()
    return store, engine, {"p1": p1, "hc": hc, "s1": s1, "s2": s2, "p0": p0, "u": u}


def test_premise_edit_cascades_stale(tmp_path):
    db = Database(str(tmp_path / "inv.db"))
    store, engine, ids = _holon_fixture(db)
    _edit_atom_text(engine, db.conn, ids["p1"], "REVISED premise claim about quokka foraging omega")
    report = engine.invalidate_dependents([("atom", str(ids["p1"]))])
    assert sorted(report["invalidated"]) == sorted([ids["hc"], ids["s1"], ids["s2"], ids["p0"]])
    assert report["closure_size"] >= 5  # requires + 4 holons (+ traversed rows)
    for hid in (ids["hc"], ids["s1"], ids["s2"], ids["p0"]):
        assert store.get_holon(hid).status == "stale", hid
    assert store.get_holon(ids["u"]).status == "active"
    # Active-only readers hide stale syntheses (never stale-as-supported).
    assert {h.holon_id for h in store.holons_of_kind("derived_synthesis")} == set()
    assert {h.holon_id for h in store.holons_of_kind("thematic_cluster")} == {ids["u"]}


def test_invalidation_exclude_and_budget(tmp_path):
    db = Database(str(tmp_path / "inv2.db"))
    _, engine, ids = _holon_fixture(db)
    _edit_atom_text(engine, db.conn, ids["p1"], "REVISED premise omega")
    # The edited container stays active when excluded, but traversal continues.
    report = engine.invalidate_dependents([("atom", str(ids["p1"]))], exclude={ids["hc"]})
    assert ids["hc"] not in report["invalidated"]
    assert ids["s1"] in report["invalidated"] and ids["s2"] in report["invalidated"]
    # Budget exhaustion rolls back: nothing is half-marked.
    with pytest.raises(InvalidationBudgetError):
        engine.invalidate_dependents([("atom", str(ids["p1"]))], budget=0)
    conn_statuses = {
        r[0]: r[1] for r in db.conn.execute(
            "SELECT holon_id, status FROM holon_state").fetchall()
    }
    assert conn_statuses[ids["hc"]] == "active"
    assert conn_statuses[ids["s2"]] == "stale"


def test_bounded_repair_incomplete_then_complete(tmp_path):
    db = Database(str(tmp_path / "rep.db"))
    store, engine, ids = _holon_fixture(db)
    before_versions = {
        hid: len(store.get_version_history(hid)) for hid in ids.values() if hid != ids["p1"]
    }
    _edit_atom_text(engine, db.conn, ids["p1"], "REVISED premise omega")
    stale = engine.invalidate_dependents([("atom", str(ids["p1"]))])["invalidated"]
    assert len(stale) == 4
    zero = engine.repair_stale_holons(stale, budget=0)
    assert zero["status"] == "incomplete" and zero["repaired"] == []
    assert sorted(zero["deferred"]) == sorted(stale)
    partial = engine.repair_stale_holons(stale, budget=2)
    assert partial["status"] == "incomplete" and len(partial["repaired"]) == 2
    assert len(partial["deferred"]) == 2
    final = engine.repair_stale_holons(stale, budget=100)
    assert final["status"] == "complete" and final["deferred"] == []
    assert final["touched_objects"] > 0 and final["touched_bytes"] > 0
    assert final["elapsed_s"] >= 0.0
    for hid in stale:
        assert store.get_holon(hid).status == "active", hid
        assert len(store.get_version_history(hid)) == before_versions[hid] + 2, hid
    # Full-rebuild oracle: stored rows equal exact recompute.
    equiv = engine.verify_rebuild_equivalence(stale)
    assert equiv["ok"], equiv["mismatches"]
    # R06 repair heals accumulators after text edits (R05 has no text hook).
    for hid in stale:
        assert store.check_drift(hid)["ok"], hid
    assert store.check_holon_integrity(ids["s2"])["ok"]


def test_repair_unknown_holon_raises(tmp_path):
    db = Database(str(tmp_path / "rep2.db"))
    _, engine, _ = _holon_fixture(db)
    with pytest.raises(KeyError):
        engine.repair_stale_holons(["no-such-holon"], budget=10)


def test_holon_tables_untouched_shape(tmp_path):
    """R06 writes rows, never columns: frozen R05 tables keep their shape."""
    db = Database(str(tmp_path / "shape.db"))
    _, _, _ = _holon_fixture(db)
    cols = {
        r[1] for r in db.conn.execute("PRAGMA table_info(holon_state)").fetchall()
    }
    assert cols == {"holon_id", "kind", "status", "descriptor", "derivation",
                    "vector_sum", "weight_sum", "member_count",
                    "representatives_json", "member_digest", "updated_at"}
    assert isinstance(db.conn.execute("SELECT COUNT(*) FROM holon_version").fetchone()[0], int)
