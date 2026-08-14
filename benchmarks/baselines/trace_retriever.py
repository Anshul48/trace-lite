"""TraceLite baseline retriever wrapper supporting flat, tree, and hybrid retrieval modes."""

import shutil
import tempfile
from pathlib import Path
from typing import Literal
from benchmarks.baselines.base import BaseRetriever, IndexedDocument, RetrievalCandidate


class TraceLiteRetriever(BaseRetriever):
    """
    Wraps a TraceLite instance in an isolated temporary data directory.
    Supports evaluating mode='flat', mode='tree', and mode='hybrid'.
    """

    def __init__(
        self,
        mode: Literal["flat", "tree", "hybrid"] = "hybrid",
        data_dir: Path | str | None = None,
        auto_cleanup: bool = True,
    ):
        self.mode = mode
        self.custom_data_dir = Path(data_dir) if data_dir else None
        self.auto_cleanup = auto_cleanup
        self.temp_dir: tempfile.TemporaryDirectory | None = None
        self._db = None
        self.doc_map: dict[str, str] = {}  # maps atom_id or index to text

    def name(self) -> str:
        return f"trace_{self.mode}"

    def _init_db(self):
        from trace_lite import TraceLite
        if self.custom_data_dir:
            resolved_dir = self.custom_data_dir
            resolved_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.temp_dir = tempfile.TemporaryDirectory(prefix="tl_benchmark_")
            resolved_dir = Path(self.temp_dir.name)
        self._db = TraceLite(data_dir=str(resolved_dir))

    def index(self, documents: list[IndexedDocument]) -> None:
        self._init_db()
        self.doc_map.clear()

        # Ingest documents as text sources
        for doc in documents:
            # Join title and text
            content = f"{doc.title}\n\n{doc.text}".strip() if doc.title else doc.text
            res = self._db.ingest_text(
                content=content,
                source_id=doc.doc_id,
                metadata={"title": doc.title, "benchmark_doc_id": doc.doc_id, **doc.metadata},
            )
            for atom in res.atoms:
                self.doc_map[atom.atom_id] = atom.content
                self.doc_map[doc.doc_id] = atom.atom_id  # cross-reference

        # Run organization if in tree or hybrid mode
        if self.mode in ("tree", "hybrid"):
            try:
                self._db.organize()
            except Exception as e:
                # If LLM is not configured/mocked, fallback to reindex
                try:
                    self._db.reindex()
                except Exception:
                    pass

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalCandidate]:
        if self._db is None:
            return []

        try:
            # In flat mode or if tree blocked, force=True searches last verified index
            q_res = self._db.query(query_text=query, top_k=top_k, mode=self.mode, force=(self.mode == "flat"))
            results: list[RetrievalCandidate] = []
            for item in q_res.items:
                atom_id = item.atom.atom_id
                # Map back to benchmark doc_id if stored in metadata
                orig_doc_id = item.atom.metadata.get("benchmark_doc_id", atom_id)
                results.append(
                    RetrievalCandidate(
                        doc_id=orig_doc_id,
                        score=float(item.score),
                        text=item.atom.content,
                        metadata={
                            "atom_id": atom_id,
                            "tree_id": item.tree_id,
                            "traversal_path": item.traversal_path,
                        },
                    )
                )
            return results
        except Exception:
            return []

    def cleanup(self) -> None:
        if self.temp_dir and self.auto_cleanup:
            self.temp_dir.cleanup()
            self.temp_dir = None
