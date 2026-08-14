"""Base protocol and data models for benchmark dataset adapters."""

import json
import os
import shutil
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


def download_file(url: str, dest_path: Path | str) -> Path:
    """Download a file using httpx atomically with automatic follow_redirects and SSL handling."""
    import httpx

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest.with_suffix(".download.tmp")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TraceLite-Benchmark/1.0"}

    try:
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=180.0) as resp:
            resp.raise_for_status()
            with open(temp_dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
    except Exception:
        # Fallback with verify=False
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, verify=False, timeout=180.0) as resp:
            resp.raise_for_status()
            with open(temp_dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

    if temp_dest.exists():
        if dest.exists():
            dest.unlink()
        temp_dest.replace(dest)

    return dest


@dataclass
class CorpusDocument:
    """A document or passage in a benchmark corpus."""
    doc_id: str
    title: str = ""
    text: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class BenchmarkQuery:
    """A query in a benchmark with ground-truth relevant document IDs."""
    query_id: str
    text: str
    gold_doc_ids: list[str] = field(default_factory=list)
    relevance_scores: dict[str, int] = field(default_factory=dict)
    category: str = "general"
    metadata: dict = field(default_factory=dict)


class BaseDatasetAdapter(ABC):
    """Abstract interface for public benchmark suite loaders."""

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def dataset_name(self) -> str:
        """Name of the dataset (e.g. 'scifact', 'bright-coding', 'multihop-rag')."""
        pass

    @abstractmethod
    def download_or_prepare(self) -> None:
        """Download or extract dataset to cache if not already present."""
        pass

    @abstractmethod
    def load_corpus(self) -> list[CorpusDocument]:
        """Load and return all corpus documents."""
        pass

    @abstractmethod
    def load_queries(self) -> list[BenchmarkQuery]:
        """Load and return evaluation queries with gold document IDs."""
        pass

    def export_manifest(self, output_path: Path | str | None = None) -> tuple:
        """Convert loaded dataset into a validated BenchmarkManifest JSON file."""
        from benchmarks.datasets.schema import BenchmarkManifest, BenchmarkCase
        from benchmarks.datasets.loader import compute_sha256

        docs = self.load_corpus()
        queries = self.load_queries()

        paragraphs = []
        doc_id_to_idx: dict[str, int] = {}
        for idx, d in enumerate(docs):
            content = f"{d.title}\n\n{d.text}".strip() if d.title else d.text
            paragraphs.append(content)
            doc_id_to_idx[d.doc_id] = idx

        corpus_text = "\n\n".join(paragraphs)
        corpus_hash = compute_sha256(corpus_text)

        cases: list[BenchmarkCase] = []
        for q in queries:
            gold_atom_ids = [
                f"atom-{doc_id_to_idx[g_id]}" for g_id in q.gold_doc_ids if g_id in doc_id_to_idx
            ]
            cases.append(
                BenchmarkCase(
                    id=q.query_id,
                    category=q.category if q.category in (
                        "direct_lookup", "chronology", "contradiction", "cross_domain",
                        "global_context", "multi_hop", "historical", "out_of_scope"
                    ) else "direct_lookup",
                    query=q.text,
                    gold_atom_ids=gold_atom_ids,
                    expected_abstention=(len(gold_atom_ids) == 0),
                    metadata=q.metadata,
                )
            )

        manifest = BenchmarkManifest(
            name=self.dataset_name(),
            version="1.0.0",
            description=f"Auto-generated manifest for {self.dataset_name()}",
            corpus_sha256=corpus_hash,
            corpus_text=corpus_text,
            release_authority=False,
            cases=cases,
            metadata={"source": self.dataset_name(), "total_docs": len(docs), "total_queries": len(queries)},
        )

        out = Path(output_path) if output_path else (self.cache_dir / f"{self.dataset_name()}_manifest.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(manifest.model_dump(), f, indent=2)

        return manifest, corpus_text
