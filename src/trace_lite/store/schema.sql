-- Trace-Lite canonical storage schema (single-node SQLite).
-- atom holds the single canonical copy of every text span; fts_atoms is an
-- external-content FTS5 index (content='atom') so indexed text is never duplicated.

CREATE TABLE IF NOT EXISTS atom (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        TEXT    NOT NULL,
    content_hash  BLOB    NOT NULL,
    start_byte    INTEGER NOT NULL,
    end_byte      INTEGER NOT NULL,
    text          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    stream_id    TEXT NOT NULL,
    stream_seq   INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS facets (
    facet_id       TEXT PRIMARY KEY,
    dimension      TEXT NOT NULL,
    name           TEXT NOT NULL,
    parent_facet_id TEXT REFERENCES facets(facet_id),
    path           TEXT NOT NULL,
    centroid_blob  BLOB
);

CREATE TABLE IF NOT EXISTS memberships (
    atom_id    INTEGER NOT NULL REFERENCES atom(id) ON DELETE CASCADE,
    facet_id   TEXT    NOT NULL REFERENCES facets(facet_id) ON DELETE CASCADE,
    confidence REAL    NOT NULL DEFAULT 1.0,
    PRIMARY KEY (atom_id, facet_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_atoms USING fts5(
    text,
    content='atom',
    content_rowid='id',
    tokenize='porter'
);

CREATE INDEX IF NOT EXISTS idx_atom_doc      ON atom(doc_id);
CREATE INDEX IF NOT EXISTS idx_atom_hash     ON atom(content_hash);
CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, stream_seq);
CREATE INDEX IF NOT EXISTS idx_facet_dim     ON facets(dimension, path);
CREATE INDEX IF NOT EXISTS idx_members_facet ON memberships(facet_id, atom_id);
