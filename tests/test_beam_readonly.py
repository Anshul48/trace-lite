"""P01 contract: FacetedBeam query path issues zero writes (A4 read-path-write fix).

Reader discipline: candidate_ids/search may read (SELECT) but must never
UPDATE/INSERT/DELETE. Stale-facet DB cleanup belongs to the writer/maintenance
path (clear_stale_centroids per batch); the query path keeps the RAM-only pop.
"""

from __future__ import annotations

import sqlite3

from trace_lite.filing.engine import FilingEngine, pack_vector, text_vector
from trace_lite.filing.taxonomy import Taxonomy
from trace_lite.router.beam import FacetedBeam
from trace_lite.store.database import Database


def _setup(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    return db, taxonomy, engine


def _seed_live(db, engine, taxonomy, topic="quokka", n=4):
    fid = taxonomy.create_facet("Topics", topic)
    for i in range(n):
        aid = db.insert_atom(
            doc_id=f"{topic}-{i}.md",
            text=f"{topic} field note {i} with stable vocabulary alpha beta gamma",
        )
        engine.assign_facets(aid, [fid])
    engine.refresh_centroid(fid)
    return fid


def _inject_stale(db, engine, taxonomy, name="stale-facet"):
    """A facet with RAM centroid + DB blob but zero members."""
    fid = taxonomy.create_facet("Topics", name)
    vec = text_vector("zzz stale facet vocabulary")
    assert any(vec), "test bug: stale vector must be nonzero"
    engine._centroids[fid] = vec
    db.conn.execute(
        "UPDATE facets SET centroid_blob = ? WHERE facet_id = ?",
        (pack_vector(vec), fid),
    )
    db.conn.commit()
    assert engine.query_facets([fid], match_all=False) == []
    return fid


class _WriteWatcher:
    def __init__(self, conn: sqlite3.Connection):
        self.writes: list[str] = []
        conn.set_trace_callback(self._cb)

    def _cb(self, sql: str):
        if sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            self.writes.append(sql)

    def close(self, conn: sqlite3.Connection):
        conn.set_trace_callback(None)


def _blob(db, fid):
    row = db.conn.execute(
        "SELECT centroid_blob FROM facets WHERE facet_id = ?", (fid,)
    ).fetchone()
    return row[0] if row else None


def test_candidate_ids_issues_no_writes(tmp_path):
    db, taxonomy, engine = _setup(tmp_path)
    _seed_live(db, engine, taxonomy)
    stale = _inject_stale(db, engine, taxonomy)

    beam = FacetedBeam(db.conn, engine)
    watcher = _WriteWatcher(db.conn)
    try:
        cands = beam.candidate_ids("quokka field note", beam_width=5)
    finally:
        watcher.close(db.conn)

    assert watcher.writes == [], f"query path wrote: {watcher.writes}"
    assert _blob(db, stale) is not None, "DB blob must survive the query path"
    assert stale not in engine._centroids, "RAM pop must still exclude the stale facet"
    assert cands, "beam must still serve live facets"


def test_search_issues_no_writes(tmp_path):
    db, taxonomy, engine = _setup(tmp_path)
    _seed_live(db, engine, taxonomy)
    stale = _inject_stale(db, engine, taxonomy)

    beam = FacetedBeam(db.conn, engine)
    watcher = _WriteWatcher(db.conn)
    try:
        results = beam.search("quokka field note", limit=5, beam_width=5)
    finally:
        watcher.close(db.conn)

    assert watcher.writes == [], f"query path wrote: {watcher.writes}"
    assert _blob(db, stale) is not None, "DB blob must survive the query path"
    assert stale not in engine._centroids, "RAM pop must still exclude the stale facet"
    assert results, "search must still serve live facets"
