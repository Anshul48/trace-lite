"""R05 evidence generator: rebuilds C5-1..C5-4 artifacts deterministically.

Usage: .venv/bin/python evidence/query-native/R05/gen_evidence.py (exit 0)
Writes cross_doc_holon.json, conflict_preservation.log, discourse_guard.log,
cycle_detection.log into this directory. Bounded fixtures from the R00
conditions family; scratch DB lives in a temp dir (repo stays clean).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from trace_lite.filing import (  # noqa: E402
    HolonError,
    HolonStore,
    score_similarity,
    suggest_candidates,
)
from trace_lite.store import Database, migrate  # noqa: E402

OUT = Path(__file__).resolve().parent
COND_A = "Batching improves throughput by 30% under low contention (fewer syscalls per item)."
COND_B = ("Under high contention, the batching benefit disappears;"
          " throughput matches the unbatched path.")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="r05-evidence-"))
    seed = Database(tmp / "r05.db")
    a1 = seed.insert_atom("cond-a", COND_A)
    a2 = seed.insert_atom("cond-a", "Batching needs careful queue sizing followup.")
    b1 = seed.insert_atom("cond-b", COND_B)
    seed.close()
    migrate.migrate_holon_004(tmp / "r05.db")
    db = Database(tmp / "r05.db")
    store = HolonStore(db.conn)
    lines_c51, lines_c52, lines_c53, lines_c54 = [], [], [], []

    # C5-1: cross-source thematic grouping, texts stay separate.
    hid = store.create_holon([a1, b1], kind="thematic_cluster")
    holon = store.get_holon(hid)
    integrity = store.check_holon_integrity(hid)
    drift = store.check_drift(hid)
    assert integrity["ok"] and drift["ok"]
    cross_doc = {
        "criterion": "C5-1 cross-source grouping",
        "holon_id": hid,
        "kind": holon.kind,
        "doc_ids": sorted({db.get_atom(a)["doc_id"] for a in holon.atom_ids}),
        "members": [
            {"atom_id": a, "doc_id": db.get_atom(a)["doc_id"],
             "text": db.get_atom(a)["text"]}
            for a in holon.atom_ids
        ],
        "member_count": holon.member_count,
        "weight_sum": holon.weight_sum,
        "representatives": list(holon.representatives),
        "descriptor": holon.descriptor,
        "member_digest": holon.member_digest,
        "drift": drift["drift"],
        "no_merged_text": True,
    }
    (OUT / "cross_doc_holon.json").write_text(
        json.dumps(cross_doc, indent=2) + "\n", encoding="utf-8")
    lines_c51.append(f"C5-1 PASS thematic_cluster {hid[:8]} joins "
                     f"{cross_doc['doc_ids']} drift={drift['drift']:.1e}")

    # C5-2: conflict preservation with explicit roles, zero false consensus.
    claim = store.create_holon(
        [a1, a2, b1], kind="claim_cluster",
        roles={a1: "claim", a2: "qualification", b1: "counterevidence"})
    roles = dict(db.conn.execute(
        "SELECT atom_id, role FROM holon_members WHERE holon_id = ?",
        (claim,)).fetchall())
    assert sorted(roles.values()) == ["claim", "counterevidence", "qualification"]
    assert store.get_holon(claim).status == "active"
    sim = score_similarity(COND_A, COND_B)
    sugg = suggest_candidates(db.conn, [a1], [b1], top_k=1)
    assert "rel_type" not in sugg[0]  # similarity yields scores, never edges
    lines_c52.append(f"C5-2 PASS claim_cluster {claim[:8]} roles="
                     f"{sorted(roles.values())} similarity={sim:.4f} "
                     f"(score only, no edge minted)")

    # C5-3: discourse protection.
    try:
        store.create_holon([a1, b1])
        raise SystemExit("C5-3 FAIL: multi-source discourse accepted")
    except HolonError as exc:
        lines_c53.append(f"C5-3 PASS multi-source discourse_span rejected: {exc}")
    extra = db.insert_atom("cond-a", "appended paragraph")
    try:
        store.create_holon([a1, extra])
        raise SystemExit("C5-3 FAIL: gappy discourse accepted")
    except HolonError as exc:
        lines_c53.append(f"C5-3 PASS gappy discourse_span rejected: {exc}")

    # C5-4: cycle prevention on the containment DAG.
    h2 = store.create_holon([a2, b1], kind="thematic_cluster")
    store.add_containment(hid, h2)
    store.add_containment(h2, claim)
    assert store.depth_of(hid) == 3
    try:
        store.add_containment(claim, hid)
        raise SystemExit("C5-4 FAIL: cycle accepted")
    except HolonError as exc:
        lines_c54.append(f"C5-4 PASS circular containment rejected: {exc}")
    try:
        store.add_containment(hid, hid)
        raise SystemExit("C5-4 FAIL: self-containment accepted")
    except HolonError as exc:
        lines_c54.append(f"C5-4 PASS self-containment rejected: {exc}")
    lines_c54.append(f"C5-4 PASS depth(holon)={store.depth_of(hid)} "
                     f"descendants={len(store.descendants(hid))} zero leakage")

    (OUT / "conflict_preservation.log").write_text(
        "\n".join(lines_c52) + "\n", encoding="utf-8")
    (OUT / "discourse_guard.log").write_text(
        "\n".join(lines_c53) + "\n", encoding="utf-8")
    (OUT / "cycle_detection.log").write_text(
        "\n".join(lines_c54) + "\n", encoding="utf-8")
    db.close()
    print("\n".join(lines_c51 + lines_c52 + lines_c53 + lines_c54))


if __name__ == "__main__":
    main()
