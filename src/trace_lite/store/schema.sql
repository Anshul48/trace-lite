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

-- R01 migration 001 (query-native evidence and projection lifecycle).
-- Logical records per ARCHITECTURE §2; span/extraction/graph/holon tables are
-- owned by R02/R04/R05 and must NOT be added here.
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source (
    source_id           TEXT PRIMARY KEY,
    namespace           TEXT NOT NULL DEFAULT 'default',
    kind                TEXT NOT NULL DEFAULT 'doc',
    locator             TEXT NOT NULL DEFAULT '',
    current_revision_id TEXT,
    access_scope        TEXT NOT NULL DEFAULT 'default',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_revision (
    revision_id      TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES source(source_id),
    content_hash     BLOB NOT NULL,
    media            TEXT NOT NULL DEFAULT 'text/plain; charset=utf-8',
    encoding         TEXT NOT NULL DEFAULT 'utf-8',
    coordinate_space TEXT NOT NULL DEFAULT 'raw_utf8_bytes',
    observed_at      TEXT NOT NULL,
    authored_at      TEXT,
    valid_from       TEXT,
    supersedes       TEXT REFERENCES source_revision(revision_id),
    status           TEXT NOT NULL DEFAULT 'current',
    text             TEXT NOT NULL,
    byte_len         INTEGER NOT NULL,
    atom_id          INTEGER REFERENCES atom(id),
    namespace        TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key          TEXT PRIMARY KEY,
    payload_hash BLOB NOT NULL,
    revision_id  TEXT NOT NULL REFERENCES source_revision(revision_id),
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projection_job (
    job_id            TEXT PRIMARY KEY,
    idempotency_key   TEXT NOT NULL UNIQUE,
    input_revision_id TEXT NOT NULL REFERENCES source_revision(revision_id),
    job_type          TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'pending',
    attempt           INTEGER NOT NULL DEFAULT 0,
    lease_owner       TEXT,
    lease_expires_at  REAL,
    error             TEXT,
    output_generation TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projection_generation (
    generation_id TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    config_hash   TEXT NOT NULL,
    watermark     TEXT NOT NULL DEFAULT '',
    manifest_hash TEXT,
    active        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

-- NOTE: atom_id is a deliberately unenforced logical reference (no FOREIGN KEY):
-- the map records where each legacy atom went and must survive the atom row's
-- later removal by the replacement lexical delta. revision_id stays enforced.
CREATE TABLE IF NOT EXISTS legacy_atom_map (
    atom_id     INTEGER PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES source_revision(revision_id),
    start_char  INTEGER NOT NULL,
    end_char    INTEGER NOT NULL,
    text_hash   BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_revision_source ON source_revision(source_id, status);
CREATE INDEX IF NOT EXISTS idx_job_state       ON projection_job(state, created_at);
CREATE INDEX IF NOT EXISTS idx_generation_kind ON projection_generation(kind, active);

-- R02 migration 002 (query-native evidence spans + versioned segmentation views).
-- Logical records per ARCHITECTURE §2; evidence_span rows are immutable, and
-- re-segmentation mints a NEW segmentation_view row (plus new span rows) without
-- rewriting prior evidence. No extraction/graph/holon/embedding tables here
-- (owned by R03/R04/R05). No schema_version insert: the version-2 row is written
-- only by the explicit migrate_spans_002() step, so migration-001 callers keep
-- observing MAX(schema_version) == 1 until they opt into 002.
CREATE TABLE IF NOT EXISTS evidence_span (
    span_id           TEXT PRIMARY KEY,
    revision_id       TEXT NOT NULL REFERENCES source_revision(revision_id),
    view_id           TEXT NOT NULL REFERENCES segmentation_view(view_id),
    ordinal           INTEGER NOT NULL,
    coordinate_space  TEXT NOT NULL DEFAULT 'raw_utf8_bytes',
    start_byte        INTEGER NOT NULL,
    end_byte          INTEGER NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'paragraph',
    parent_ordinal    INTEGER,
    header            TEXT NOT NULL DEFAULT '',
    text_hash         BLOB NOT NULL,
    parser_version    TEXT NOT NULL DEFAULT '',
    segmenter_version TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'complete'
);

CREATE TABLE IF NOT EXISTS segmentation_view (
    view_id           TEXT PRIMARY KEY,
    revision_id       TEXT NOT NULL REFERENCES source_revision(revision_id),
    config            TEXT NOT NULL,
    config_hash       TEXT NOT NULL,
    segmenter_version TEXT NOT NULL DEFAULT '',
    parser_version    TEXT NOT NULL DEFAULT '',
    span_count        INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'complete',
    signal_json       TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_span_revision ON evidence_span(revision_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_span_view      ON evidence_span(view_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_view_revision  ON segmentation_view(revision_id, created_at);

-- R04 migration 003 (query-native source-local graphs).
-- Logical records per ARCHITECTURE §2; every assertion/relation is anchored to
-- an R02 evidence_span plus its extraction_run. Ambiguous bindings persist as
-- status='unresolved', never forced. This contract is frozen for R05
-- reconciliation: R05 may ADD tables/columns but must not alter these tables
-- or reinterpret local rel_type values. No schema_version insert: the
-- version-3 row is written only by the explicit migrate_graph_003() step, so
-- migration-001/002 callers keep observing their MAX(schema_version) values
-- until they opt into 003.
CREATE TABLE IF NOT EXISTS extraction_run (
    run_id            TEXT PRIMARY KEY,
    revision_id       TEXT NOT NULL REFERENCES source_revision(revision_id),
    view_id           TEXT NOT NULL REFERENCES segmentation_view(view_id),
    extractor_name    TEXT NOT NULL DEFAULT 'structural',
    extractor_version TEXT NOT NULL DEFAULT 'v1',
    schema_version    TEXT NOT NULL DEFAULT 'r04-graph-v1',
    config_hash       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'complete',
    error             TEXT,
    span_count        INTEGER NOT NULL DEFAULT 0,
    assertion_count   INTEGER NOT NULL DEFAULT 0,
    relation_count    INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_mention (
    mention_id       TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES extraction_run(run_id),
    revision_id      TEXT NOT NULL REFERENCES source_revision(revision_id),
    span_id          TEXT NOT NULL REFERENCES evidence_span(span_id),
    start_byte       INTEGER NOT NULL,
    end_byte         INTEGER NOT NULL,
    surface          TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'subject',
    canonical_key    TEXT,
    mapping_version  TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'asserted'
);

CREATE TABLE IF NOT EXISTS assertion (
    assertion_id     TEXT PRIMARY KEY,
    version          INTEGER NOT NULL DEFAULT 1,
    run_id           TEXT NOT NULL REFERENCES extraction_run(run_id),
    revision_id      TEXT NOT NULL REFERENCES source_revision(revision_id),
    predicate        TEXT NOT NULL,
    polarity         TEXT NOT NULL DEFAULT 'positive',
    modality         TEXT NOT NULL DEFAULT 'asserted',
    epistemic_status TEXT NOT NULL DEFAULT 'source_asserted',
    subject_text     TEXT,
    object_text      TEXT,
    condition_text   TEXT,
    units            TEXT,
    temporal_scope   TEXT,
    version_scope    TEXT,
    span_id          TEXT NOT NULL REFERENCES evidence_span(span_id),
    start_byte       INTEGER NOT NULL,
    end_byte         INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'asserted',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assertion_argument (
    argument_id       TEXT PRIMARY KEY,
    assertion_id      TEXT NOT NULL REFERENCES assertion(assertion_id),
    role              TEXT NOT NULL,
    target_mention_id TEXT NOT NULL REFERENCES entity_mention(mention_id),
    target_text       TEXT NOT NULL DEFAULT '',
    position          INTEGER NOT NULL DEFAULT 0
);

-- Typed directed endpoints use separate nullable FK columns (one per side at
-- most): real referential integrity for the polymorphic endpoint contract.
CREATE TABLE IF NOT EXISTS relation (
    relation_id          TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES extraction_run(run_id),
    revision_id          TEXT NOT NULL REFERENCES source_revision(revision_id),
    rel_type             TEXT NOT NULL,
    subject_mention_id   TEXT REFERENCES entity_mention(mention_id),
    subject_assertion_id TEXT REFERENCES assertion(assertion_id),
    object_mention_id    TEXT REFERENCES entity_mention(mention_id),
    object_assertion_id  TEXT REFERENCES assertion(assertion_id),
    subject_text         TEXT,
    object_text          TEXT,
    polarity             TEXT NOT NULL DEFAULT 'positive',
    condition_text       TEXT,
    epistemic_status     TEXT NOT NULL DEFAULT 'source_asserted',
    span_id              TEXT NOT NULL REFERENCES evidence_span(span_id),
    start_byte           INTEGER NOT NULL,
    end_byte             INTEGER NOT NULL,
    status               TEXT NOT NULL DEFAULT 'asserted',
    candidates_json      TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL,
    CHECK (subject_mention_id IS NULL OR subject_assertion_id IS NULL),
    CHECK (object_mention_id IS NULL OR object_assertion_id IS NULL)
);

CREATE TABLE IF NOT EXISTS dependency (
    dependency_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    derived_kind   TEXT NOT NULL,
    derived_id     TEXT NOT NULL,
    requires_kind  TEXT NOT NULL,
    requires_id    TEXT NOT NULL,
    dependency_kind TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_revision    ON extraction_run(revision_id, created_at);
CREATE INDEX IF NOT EXISTS idx_assertion_run    ON assertion(run_id);
CREATE INDEX IF NOT EXISTS idx_assertion_span   ON assertion(span_id);
CREATE INDEX IF NOT EXISTS idx_mention_run      ON entity_mention(run_id);
CREATE INDEX IF NOT EXISTS idx_mention_span     ON entity_mention(span_id);
CREATE INDEX IF NOT EXISTS idx_argument_assert  ON assertion_argument(assertion_id, position);
CREATE INDEX IF NOT EXISTS idx_relation_run     ON relation(run_id);
CREATE INDEX IF NOT EXISTS idx_relation_span    ON relation(span_id);
CREATE INDEX IF NOT EXISTS idx_dep_derived      ON dependency(derived_kind, derived_id);
CREATE INDEX IF NOT EXISTS idx_dep_requires     ON dependency(requires_kind, requires_id);

INSERT OR IGNORE INTO schema_version (version, applied_at)
VALUES (1, datetime('now'));

-- R05 migration 004 (query-native cross-source holons + reconciliation).
-- Logical records per ARCHITECTURE §5–§6; frozen for R06/R07. Purely
-- additive: new tables only (holon_state/holon_version/holon_containment/
-- holon_link/holon_canonical). The legacy holons/holon_members tables stay
-- owned by filing/holon.py (HOLON_SCHEMA) and gain columns only via ADD
-- COLUMN with defaults in migrate_holon_004(), never rewrites or drops.
-- No schema_version insert: the version-4 row is written only by the
-- explicit migrate_holon_004() step, so migration-001/002/003 callers keep
-- observing their MAX(schema_version) values until they opt into 004.
CREATE TABLE IF NOT EXISTS holon_state (
    holon_id             TEXT PRIMARY KEY REFERENCES holons(holon_id) ON DELETE CASCADE,
    kind                 TEXT NOT NULL DEFAULT 'discourse_span',
    status               TEXT NOT NULL DEFAULT 'active',
    descriptor           TEXT NOT NULL DEFAULT '',
    derivation           TEXT NOT NULL DEFAULT '',
    vector_sum           BLOB,
    weight_sum           REAL NOT NULL DEFAULT 0,
    member_count         INTEGER NOT NULL DEFAULT 0,
    representatives_json TEXT NOT NULL DEFAULT '[]',
    member_digest        TEXT NOT NULL DEFAULT '',
    updated_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holon_version (
    version_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    holon_id             TEXT NOT NULL REFERENCES holons(holon_id) ON DELETE CASCADE,
    version              INTEGER NOT NULL,
    kind                 TEXT NOT NULL,
    status               TEXT NOT NULL,
    descriptor           TEXT NOT NULL DEFAULT '',
    derivation           TEXT NOT NULL DEFAULT '',
    weight_sum           REAL NOT NULL DEFAULT 0,
    member_count         INTEGER NOT NULL DEFAULT 0,
    representatives_json TEXT NOT NULL DEFAULT '[]',
    member_digest        TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    UNIQUE (holon_id, version)
);
CREATE TABLE IF NOT EXISTS holon_containment (
    parent_holon_id TEXT NOT NULL REFERENCES holons(holon_id) ON DELETE CASCADE,
    child_holon_id  TEXT NOT NULL REFERENCES holons(holon_id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (parent_holon_id, child_holon_id),
    CHECK (parent_holon_id != child_holon_id)
);
CREATE TABLE IF NOT EXISTS holon_link (
    link_id      TEXT PRIMARY KEY,
    src_kind     TEXT NOT NULL,
    src_id       TEXT NOT NULL,
    dst_kind     TEXT NOT NULL,
    dst_id       TEXT NOT NULL,
    rel_type     TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holon_canonical (
    canonical_id TEXT NOT NULL,
    version      INTEGER NOT NULL,
    target_kind  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
    derivation   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    PRIMARY KEY (canonical_id, version, target_kind, target_id)
);
CREATE INDEX IF NOT EXISTS idx_holon_state_kind ON holon_state(kind, status);
CREATE INDEX IF NOT EXISTS idx_holon_version_holon ON holon_version(holon_id, version);
CREATE INDEX IF NOT EXISTS idx_containment_parent ON holon_containment(parent_holon_id);
CREATE INDEX IF NOT EXISTS idx_containment_child ON holon_containment(child_holon_id);
CREATE INDEX IF NOT EXISTS idx_link_src ON holon_link(src_kind, src_id);
CREATE INDEX IF NOT EXISTS idx_link_dst ON holon_link(dst_kind, dst_id);
CREATE INDEX IF NOT EXISTS idx_canonical_id ON holon_canonical(canonical_id, version);

-- N06 MAP-09 mint authority (P03, greenfield vs generations.py per N06a section 9).
-- mint_epoch: the SINGLE fenced epoch row (DUR-4/D-R1.2: DB row is the epoch
-- authority). mint_counter: durable per-(mint_epoch, generation_id) k counter
-- (DES-4). Retirement is fence-level mint-retirement with retention (D4):
-- advance marks superseded-epoch rows RETIRED; rows are NEVER deleted here
-- (deletion deferred to OWN-01). No schema_version insert: tables are picked
-- up by the Database open path (CREATE TABLE IF NOT EXISTS) on fresh and
-- existing DBs; the epoch ROW requires explicit init_mint (startup/recovery
-- wiring is a follow-up).
CREATE TABLE IF NOT EXISTS mint_epoch (
    birth_uuid TEXT NOT NULL,
    epoch      INTEGER NOT NULL CHECK (epoch >= 0)
);
CREATE TRIGGER IF NOT EXISTS trg_mint_epoch_single_row
    BEFORE INSERT ON mint_epoch
    WHEN (SELECT COUNT(*) FROM mint_epoch) >= 1
BEGIN
    SELECT RAISE(ABORT, 'mint_epoch is single-row');
END;
CREATE TABLE IF NOT EXISTS mint_counter (
    mint_epoch    INTEGER NOT NULL CHECK (mint_epoch >= 0),
    generation_id TEXT NOT NULL,
    k_next        INTEGER NOT NULL CHECK (k_next >= 0),
    state         TEXT NOT NULL CHECK (state IN ('LIVE', 'RETIRED')),
    PRIMARY KEY (mint_epoch, generation_id)
);

-- P06 (gate durable state): inhibitors + committed proposals + RPE accumulator.
-- Loaded by StepAcceptanceGate when constructed with a store connection, via
-- the normal Database open path (executescript of this file).
-- current_step stays session-local by design: the gate body never advances it.
CREATE TABLE IF NOT EXISTS gate_inhibitor (
    gene_id               TEXT PRIMARY KEY,
    proposal_hash         TEXT NOT NULL,
    violated_invariant_id TEXT NOT NULL,
    violating_operation   TEXT NOT NULL,
    diagnostic            TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'REFUTED',
    created_at_step       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gate_committed (
    action_id     TEXT PRIMARY KEY,
    proposal_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- A1 migration 006 (atom tombstones / retention).
-- Separate exclusion table: the tombstone must outlive the atom row it
-- excludes, so atom_id is a deliberately unenforced key (no FOREIGN KEY)
-- and delete_atom keeps dropping the atom row. retire_gen is an interim
-- opaque TEXT label until the N05/N06 triple settles (D5); reason starts
-- with 'retract' only. No schema_version insert: the version-6 row is
-- written only by the explicit migrate_tombstones_006() step, so
-- migration-001..005 callers keep observing their MAX(schema_version)
-- values until they opt into 006.
CREATE TABLE IF NOT EXISTS atom_tombstone (
    atom_id      INTEGER PRIMARY KEY,
    content_hash BLOB    NOT NULL,
    doc_id       TEXT    NOT NULL,
    retire_gen   TEXT    NOT NULL,
    reason       TEXT    NOT NULL DEFAULT 'retract',
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tombstone_gen ON atom_tombstone(retire_gen);
