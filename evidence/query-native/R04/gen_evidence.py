"""R04 evidence generator (retained for re-verification; see delivery.md).

Builds a scratch SQLite file under /tmp, publishes the R00 DEV fixtures plus
the R04 condition probes, segments via the frozen R02 API, extracts with the
structural extractor, persists via migration-003 tables, and writes the four
criterion artifacts into this directory:

- relation_fidelity.json (C4-1)
- provenance_check.log  (C4-2)
- condition_test.json   (C4-3)
- fallback_test.log     (C4-4)

Usage from repo root: .venv/bin/python evidence/query-native/R04/gen_evidence.py
Exit code 0 on success; raises on any criterion failure.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

OUT = Path(__file__).resolve().parent

FIXTURES: list[tuple[str, str]] = [
    ("cond-a", "Batching improves throughput by 30% under low contention"
               " (fewer syscalls per item)."),
    ("cond-b", "Under high contention, the batching benefit disappears;"
               " throughput matches the unbatched path."),
    ("cond-c", "Batching restores throughput benefit under high contention."),
    ("neg-a", "WAL mode allows one writer with concurrent readers."),
    ("neg-b", "The legacy rollback-journal mode does NOT allow concurrent"
              " readers during a write."),
    ("amb-a", "Chen (storage team) reports checkpoint latency p99 of 40ms"
              " on NVMe."),
    ("amb-b", "Chen (query team) reports checkpoint latency is not the"
              " bottleneck; parse time dominates."),
    ("unless-probe", "Batching helps unless queues are saturated."),
    ("only-probe", "Only under low contention does batching improve throughput."),
    ("off-a", "SQLite WAL checkpoint frames durability truncate oversight valve."),
]

# (case, source, field, expected): the C4-1 role-accuracy suite.
EXPECTATIONS: list[tuple[str, str, str, object]] = [
    ("cond-a improves", "cond-a", "subject", "Batching"),
    ("cond-a improves", "cond-a", "predicate", "improves"),
    ("cond-a improves", "cond-a", "object", "throughput by 30%"),
    ("cond-a improves", "cond-a", "polarity", "positive"),
    ("cond-a improves", "cond-a", "condition", "under low contention"),
    ("cond-a improves", "cond-a", "units", "30%"),
    ("cond-a qualifier", "cond-a", "qualifier", "fewer syscalls per item"),
    ("cond-b disappears", "cond-b", "subject", "the batching benefit"),
    ("cond-b disappears", "cond-b", "predicate", "disappears"),
    ("cond-b disappears", "cond-b", "polarity", "negative"),
    ("cond-b disappears", "cond-b", "condition", "Under high contention"),
    ("cond-b matches", "cond-b", "predicate", "matches"),
    ("cond-b matches", "cond-b", "subject", "throughput"),
    ("cond-b matches", "cond-b", "object", "the unbatched path"),
    ("cond-c restores", "cond-c", "predicate", "restores"),
    ("cond-c restores", "cond-c", "condition", "under high contention"),
    ("neg-a allows", "neg-a", "polarity", "positive"),
    ("neg-a allows", "neg-a", "object", "one writer with concurrent readers"),
    ("neg-b denies", "neg-b", "polarity", "negative"),
    ("neg-b denies", "neg-b", "temporal", "during a write"),
    ("unless exception", "unless-probe", "condition", "unless queues are saturated"),
    ("only exception", "only-probe", "subject", "batching"),
    ("only exception", "only-probe", "condition", "Only under low contention"),
    ("amb-a scope", "amb-a", "units", "40ms"),
    ("amb-a scope", "amb-a", "version_scope", "on NVMe"),
]


def main() -> None:
    from trace_lite import source as S
    from trace_lite.graph import (
        check_integrity,
        create_extraction_run,
        extract_spans,
        persist_graph,
    )
    from trace_lite.source import views as V
    from trace_lite.store import Database

    tmp = Path(tempfile.mkdtemp(prefix="r04-evidence-"))
    db_path = tmp / "r04.db"
    db = Database(db_path)
    lines: list[str] = []
    try:
        runs: dict[str, str] = {}
        span_of_source: dict[str, list[dict]] = {}
        for source_id, text in FIXTURES:
            rev = S.publish(db, source_id, text)
            view = V.create_segmentation_view(db.conn, rev.revision_id)
            assert view["status"] == "complete" and view["span_ids"], source_id
            spans = V.get_spans(db.conn, view["view_id"])
            result = extract_spans(spans, run_id=f"ev-{source_id}")
            rid = create_extraction_run(db.conn, rev.revision_id,
                                        view["view_id"], run_id=f"ev-{source_id}",
                                        status=result.status)
            persist_graph(db.conn, result, run_id=rid,
                          revision_id=rev.revision_id)
            runs[source_id] = rid
            span_of_source[source_id] = spans

        # ---- C4-1 relation fidelity ----
        checks: list[dict] = []
        for case, source_id, field, expected in EXPECTATIONS:
            rid = runs[source_id]
            rows = db.conn.execute(
                "SELECT * FROM assertion WHERE run_id = ?", (rid,)).fetchall()
            if field == "qualifier":
                got = [a["target_text"] for a in db.conn.execute(
                    "SELECT aa.* FROM assertion_argument aa JOIN assertion x"
                    " ON x.assertion_id = aa.assertion_id"
                    " WHERE x.run_id = ? AND aa.role = 'qualifier'", (rid,))]
                passed = expected in got
                actual = got
            else:
                column = {"subject": "subject_text", "predicate": "predicate",
                          "object": "object_text", "polarity": "polarity",
                          "condition": "condition_text", "units": "units",
                          "temporal": "temporal_scope",
                          "version_scope": "version_scope"}[field]
                actual = sorted({r[column] for r in rows if r[column]})
                passed = expected in actual
            checks.append({"case": case, "field": field, "expected": expected,
                           "actual": actual, "passed": passed})
        # No invented mechanisms anywhere in the corpus graphs.
        forbidden = ("cache", "bouncing")
        surfaces = " ".join(
            (r["predicate"] or "") + " " + (r["subject_text"] or "") + " "
            + (r["object_text"] or "")
            for rid in runs.values() for r in db.conn.execute(
                "SELECT * FROM assertion WHERE run_id = ?", (rid,)).fetchall()
        ).lower()
        invented = [w for w in forbidden if w in surfaces]
        # Opposing pair preserved, never merged (cond-a positive vs cond-b neg).
        pos = db.conn.execute(
            "SELECT COUNT(*) FROM assertion WHERE run_id = ?"
            " AND polarity = 'positive'", (runs["cond-a"],)).fetchone()[0]
        neg = db.conn.execute(
            "SELECT COUNT(*) FROM assertion WHERE run_id = ?"
            " AND polarity = 'negative'", (runs["cond-b"],)).fetchone()[0]
        opposing_ok = pos == 1 and neg == 1
        # Ambiguity preserved: two distinct qualified Chens, no canonical merge.
        chen_keys = {r["canonical_key"] for r in db.conn.execute(
            "SELECT * FROM entity_mention WHERE surface LIKE 'Chen (%'"
            " AND role = 'subject'")}
        ambiguity_ok = len(chen_keys) == 2 and None not in chen_keys
        passed_n = sum(1 for c in checks if c["passed"])
        fidelity = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "role_checks": checks,
            "role_accuracy": passed_n / len(checks),
            "passed": passed_n,
            "total": len(checks),
            "invented_mechanism_terms": invented,
            "opposing_pair_preserved": opposing_ok,
            "ambiguity_preserved": ambiguity_ok,
            "criterion": "C4-1",
        }
        (OUT / "relation_fidelity.json").write_text(
            json.dumps(fidelity, indent=2) + "\n", encoding="utf-8")
        assert fidelity["role_accuracy"] == 1.0, "C4-1 role accuracy < 100%"
        assert not invented and opposing_ok and ambiguity_ok

        # ---- C4-2 provenance anchoring ----
        total_edges = 0
        total_resolved = 0
        orphan_total = 0
        for source_id, rid in runs.items():
            report = check_integrity(db.conn, rid)
            edges = db.conn.execute(
                "SELECT * FROM relation WHERE run_id = ?", (rid,)).fetchall()
            resolved = 0
            for edge in edges:
                V.excerpt_span(db.conn, edge["span_id"])
                resolved += 1
            total_edges += len(edges)
            total_resolved += resolved
            orphan_total += len(report["orphans"]) + len(report["unanchored"])
            lines.append(
                f"{source_id}: run={rid[:8]} assertions={report['assertions']}"
                f" relations={report['relations']} resolved={resolved}/"
                f"{len(edges)} orphans={report['orphans']} "
                f"unanchored={report['unanchored']} ok={report['ok']}")
            assert report["ok"] is True, source_id
        lines.append(f"TOTAL: {total_resolved}/{total_edges} relations resolve;"
                     f" {orphan_total} orphan/unanchored edges")
        assert total_edges > 0 and total_resolved == total_edges
        assert orphan_total == 0
        (OUT / "provenance_check.log").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

        # ---- C4-3 condition retention ----
        rid = runs["cond-c"]
        frame = db.conn.execute(
            "SELECT * FROM assertion WHERE run_id = ?"
            " AND predicate = 'restores'", (rid,)).fetchone()
        quals = db.conn.execute(
            "SELECT * FROM relation WHERE run_id = ? AND rel_type = 'qualifies'",
            (rid,)).fetchall()
        condition_test = {
            "criterion": "C4-3",
            "probe": "cond-c",
            "input": dict(FIXTURES)["cond-c"],
            "assertion": dict(frame),
            "qualifier_edges": [dict(q) for q in quals],
            "qualifier_edge_exists": any(
                q["subject_text"] == "under high contention"
                and q["object_assertion_id"] == frame["assertion_id"]
                for q in quals),
            "condition_retained": frame["condition_text"]
            == "under high contention",
        }
        (OUT / "condition_test.json").write_text(
            json.dumps(condition_test, indent=2) + "\n", encoding="utf-8")
        assert condition_test["qualifier_edge_exists"]
        assert condition_test["condition_retained"]

        # ---- C4-4 extraction fallback ----
        hostile = [None, 42, b"bytes", {"text": 1, "span_id": "x"},
                   {"span_id": "lonely"}, {"span_id": "ok",
                                            "text": "Batching improves throughput."},
                   {"span_id": "big", "text": "w " * 100000},
                   {"span_id": "uni", "text": "héllo 🚀 improves things ☃ \ud800"},
                   {"span_id": "paren", "text": "((((( improves (((("}]
        unhandled = 0
        flog = []
        try:
            hostile_result = extract_spans(hostile, run_id="ev-hostile")
            flog.append(f"battery: status={hostile_result.status} "
                        f"seen={hostile_result.spans_seen} "
                        f"skipped={hostile_result.spans_skipped} "
                        f"assertions={len(hostile_result.assertions)} "
                        f"warnings={len(hostile_result.warnings)}")
        except Exception as exc:  # noqa: BLE001 — the assertion under test
            unhandled += 1
            flog.append(f"UNHANDLED: {exc!r}")
        try:
            empty = extract_spans([], run_id="ev-empty")
            flog.append(f"empty: status={empty.status} "
                        f"assertions={len(empty.assertions)}")
        except Exception as exc:  # noqa: BLE001
            unhandled += 1
            flog.append(f"UNHANDLED(empty): {exc!r}")
        flog.append(f"unhandled_exceptions={unhandled}")
        (OUT / "fallback_test.log").write_text("\n".join(flog) + "\n",
                                               encoding="utf-8")
        assert unhandled == 0, "C4-4: unhandled exception in fallback battery"
        assert hostile_result.status == "partial"
    finally:
        db.close()
    print(f"R04 evidence written to {OUT} (scratch db: {db_path})")


if __name__ == "__main__":
    main()
