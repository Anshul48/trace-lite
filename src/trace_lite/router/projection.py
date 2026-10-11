"""R03 projection workers: bounded-batch encoding outside the writer lock.

Model/embedding work must never hold the database writer lock (ARCH §8): the
worker reads pinned atom rows on its own connection, encodes in bounded
batches, and publishes through the index adapter's save→manifest→CURRENT→
generation-row protocol. Late output for a superseded watermark never
overwrites the active generation (watermark check before publish).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover - venv always provides numpy
    np = None  # type: ignore[assignment]

from .encoder import HashBowEncoder
from .index_adapter import GENERATION_KIND, VectorIndex

BATCH_ROWS = 2000


def iter_atom_batches(conn: sqlite3.Connection, batch: int = BATCH_ROWS,
                       after_id: int = 0) -> Iterator[list[tuple[int, str]]]:
    """Read-only, bounded batches in rowid order (no writer lock held)."""
    cursor = after_id
    while True:
        rows = conn.execute(
            "SELECT id, text FROM atom WHERE id > ? ORDER BY id LIMIT ?",
            (cursor, batch)).fetchall()
        if not rows:
            return
        yield [(int(r[0]), str(r[1])) for r in rows]
        cursor = rows[-1][0]


def encode_batches(conn: sqlite3.Connection, encoder: HashBowEncoder,
                   batch: int = BATCH_ROWS) -> Iterator[tuple[list[int], "np.ndarray"]]:
    """Yield (ids, IDF-weighted normalized matrix) per bounded batch."""
    assert np is not None
    texts: list[str] = []
    first: list[tuple[int, str]] = []
    for chunk in iter_atom_batches(conn, batch):
        if encoder._idf is None:
            texts.extend(t for _, t in chunk)
            first.append(chunk)
            continue
        ids = [aid for aid, _ in chunk]
        mat = np.asarray([encoder.encode_weighted(t) for _, t in chunk],
                         dtype=np.float32)
        yield ids, mat


def build_and_publish(conn: sqlite3.Connection, index_dir: str | Path,
                      generation_id: str, watermark: str = "",
                      batch: int = BATCH_ROWS, nprobe: int = 8,
                      n_lists: int | None = None,
                      check_watermark: str | None = None) -> dict:
    """Two-pass build (IDF fit, then encode) + crash-safe publish.

    ``check_watermark``: when set, abort without publishing if the CURRENT
    manifest's watermark already supersedes this build (late-worker guard).
    Returns the publish receipt plus encoder identity.
    """
    assert np is not None
    from ..store import generations as gen_store

    index_dir = Path(index_dir)
    if check_watermark is not None and (index_dir / "CURRENT").exists():
        try:
            _, info = VectorIndex.open(index_dir)
            if info.get("generation_id") and check_watermark:
                active = gen_store.get_active(conn, GENERATION_KIND)
                if active is not None and active.get("watermark", "") > watermark:
                    return {"published": False, "reason": "superseded-watermark",
                            "active_watermark": active.get("watermark", "")}
        except (OSError, ValueError):
            pass
    encoder = HashBowEncoder()
    corpus: list[str] = []
    for chunk in iter_atom_batches(conn, batch):
        corpus.extend(t for _, t in chunk)
    encoder.fit_idf(corpus)
    all_ids: list[int] = []
    mats: list[np.ndarray] = []
    for chunk in iter_atom_batches(conn, batch):
        all_ids.extend(aid for aid, _ in chunk)
        mats.append(np.asarray([encoder.encode_weighted(t) for _, t in chunk],
                               dtype=np.float32))
    mat = np.concatenate(mats, axis=0) if mats else np.zeros((0, encoder.dim), dtype=np.float32)
    index = VectorIndex(encoder, nprobe=nprobe)
    index.build(all_ids, mat, generation_id=generation_id, watermark=watermark,
                n_lists=n_lists)
    receipt = index.publish(index_dir, conn, generation_id=generation_id,
                            watermark=watermark)
    receipt["published"] = True
    receipt["encoder"] = encoder.spec.to_dict()
    return receipt
