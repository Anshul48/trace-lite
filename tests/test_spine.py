import pytest
from pathlib import Path
from trace_lite.spine import Atom, SourceArtifact, SpineEvent, SpineStore, Atomizer


def test_atom_creation():
    atom = Atom.create(
        atom_id="atom-1",
        content="SQLite is lightweight and fast.",
        source_artifact_id="art-1",
        sequence_index=0,
        char_offset_start=0,
        char_offset_end=31,
    )
    assert atom.atom_id == "atom-1"
    assert atom.content_hash is not None
    assert len(atom.content_hash) == 64


def test_spine_store_append_and_retrieve(tmp_path: Path):
    db_path = tmp_path / "spine.sqlite3"
    store = SpineStore(db_path)

    artifact = SourceArtifact.create(
        artifact_id="art-1",
        content="Full text document",
        document_name="Doc1.txt",
    )
    store.store_artifact(artifact)

    atom1 = Atom.create("atom-1", "Para 1", "art-1", 0, 0, 6)
    atom2 = Atom.create("atom-2", "Para 2", "art-1", 1, 7, 13)
    store.store_atoms_batch([atom1, atom2])

    event = SpineEvent.create("evt-1", "atoms.created", {"count": 2})
    store.append_event(event)

    fetched_art = store.get_artifact("art-1")
    assert fetched_art is not None
    assert fetched_art.document_name == "Doc1.txt"

    fetched_atoms = store.get_atoms_by_artifact("art-1")
    assert len(fetched_atoms) == 2
    assert fetched_atoms[0].content == "Para 1"

    events = list(store.replay_events())
    assert len(events) == 1
    assert events[0].event_type == "atoms.created"


def test_atomizer():
    atomizer = Atomizer(min_length=10, max_length=100)
    text = "Paragraph 1 is here.\n\nParagraph 2 is here, and it is slightly longer."
    atoms = atomizer.atomize(text, source_artifact_id="art-1")
    assert len(atoms) == 2
    assert atoms[0].sequence_index == 0
    assert atoms[1].sequence_index == 1
    assert atoms[0].char_offset_start == 0


def test_spine_search_lexical_basic(tmp_path: Path):
    db_path = tmp_path / "spine.sqlite3"
    store = SpineStore(db_path)

    art = SourceArtifact.create("art-1", "Doc on Databases", "doc.txt")
    store.store_artifact(art)

    atom1 = Atom.create("atom-1", "SQLite is an embedded relational database engine.", "art-1", 0, 0, 50)
    atom2 = Atom.create("atom-2", "LanceDB provides fast vector similarity search.", "art-1", 1, 51, 100)
    store.store_atoms_batch([atom1, atom2])

    results = store.search_lexical("relational engine", top_k=10)
    assert len(results) >= 1
    top_atom_id, top_score = results[0]
    assert top_atom_id == "atom-1"
    assert 0.0 <= top_score <= 1.0


def test_spine_search_lexical_porter_stemming(tmp_path: Path):
    db_path = tmp_path / "spine.sqlite3"
    store = SpineStore(db_path)

    art = SourceArtifact.create("art-1", "Network Doc", "net.txt")
    store.store_artifact(art)

    atom = Atom.create("atom-1", "Establishing connections between nodes.", "art-1", 0, 0, 40)
    store.store_atom(atom)

    # Porter stemmer should map "connecting", "connection", "connect" to the same stem
    for query in ["connect", "connecting", "connections", "connected"]:
        hits = store.search_lexical(query)
        assert len(hits) == 1, f"Failed for query: {query}"
        assert hits[0][0] == "atom-1"
        assert 0.0 < hits[0][1] <= 1.0


def test_spine_search_lexical_ranking(tmp_path: Path):
    db_path = tmp_path / "spine.sqlite3"
    store = SpineStore(db_path)

    art = SourceArtifact.create("art-1", "Ranking Doc", "rank.txt")
    store.store_artifact(art)

    atom_high = Atom.create(
        "atom-high",
        "Trace-Lite hierarchical cognitive retrieval architecture with tree navigation.",
        "art-1",
        0,
        0,
        77,
    )
    atom_low = Atom.create(
        "atom-low",
        "General software architecture patterns.",
        "art-1",
        1,
        78,
        118,
    )
    atom_unrelated = Atom.create(
        "atom-none",
        "A recipe for chocolate chip cookies.",
        "art-1",
        2,
        119,
        155,
    )
    store.store_atoms_batch([atom_high, atom_low, atom_unrelated])

    results = store.search_lexical("Trace-Lite cognitive retrieval tree", top_k=10)
    assert len(results) >= 1
    assert results[0][0] == "atom-high"
    # atom-high should have higher relevance score than atom-low if atom-low matched any keyword
    matching_ids = [aid for aid, _ in results]
    assert "atom-none" not in matching_ids
    assert matching_ids[0] == "atom-high"


def test_spine_search_lexical_query_sanitization(tmp_path: Path):
    db_path = tmp_path / "spine.sqlite3"
    store = SpineStore(db_path)

    art = SourceArtifact.create("art-1", "Sanitization Doc", "san.txt")
    store.store_artifact(art)

    atom = Atom.create("atom-1", "Testing robust full-text search indexing with SQLite FTS5.", "art-1", 0, 0, 60)
    store.store_atom(atom)

    # Queries with special FTS5 operators and punctuation should not raise exceptions
    queries = [
        'FTS5 "indexing" AND OR NOT',
        'full-text:search*',
        'SQLite (FTS5) ^10',
        '"""',
        '::: --- *** ??? !!!',
        '   ',
        '',
    ]

    for q in queries:
        # Must execute without raising sqlite3.OperationalError
        res = store.search_lexical(q)
        assert isinstance(res, list)

    # Verify matching still works when mixed with special characters
    res = store.search_lexical('SQLite: "indexing" *full-text*')
    assert len(res) == 1
    assert res[0][0] == "atom-1"


def test_spine_store_atom_fts_duplicate_idempotence(tmp_path: Path):
    db_path = tmp_path / "spine.sqlite3"
    store = SpineStore(db_path)

    art = SourceArtifact.create("art-1", "Doc", "doc.txt")
    store.store_artifact(art)

    atom = Atom.create("atom-1", "Unique database content identifier", "art-1", 0, 0, 36)
    store.store_atom(atom)
    # Storing identical atom again should be ignored without duplicate FTS entries
    store.store_atom(atom)
    store.store_atoms_batch([atom])

    hits = store.search_lexical("identifier")
    assert len(hits) == 1
    assert hits[0][0] == "atom-1"


def test_spine_search_lexical_top_k_limit(tmp_path: Path):
    db_path = tmp_path / "spine.sqlite3"
    store = SpineStore(db_path)

    art = SourceArtifact.create("art-1", "Batch Doc", "batch.txt")
    store.store_artifact(art)

    atoms = [
        Atom.create(f"atom-{i}", f"Common keyword search entry number {i}", "art-1", i, i * 40, (i + 1) * 40)
        for i in range(10)
    ]
    store.store_atoms_batch(atoms)

    hits_top3 = store.search_lexical("keyword search", top_k=3)
    assert len(hits_top3) == 3

    hits_top0 = store.search_lexical("keyword search", top_k=0)
    assert hits_top0 == []

    hits_negative = store.search_lexical("keyword search", top_k=-5)
    assert hits_negative == []
