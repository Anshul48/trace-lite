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
