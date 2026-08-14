"""Dataset loading, validation, and SHA-256 integrity verification."""

import hashlib
import json
from pathlib import Path
from benchmarks.datasets.schema import BenchmarkManifest, BenchmarkCase


class DatasetIntegrityError(Exception):
    """Raised when a benchmark dataset fails SHA-256 hash or schema validation."""
    pass


def compute_sha256(text_or_bytes: str | bytes) -> str:
    """Compute SHA-256 hex digest, normalizing string line endings to \\n."""
    if isinstance(text_or_bytes, str):
        normalized = text_or_bytes.replace("\r\n", "\n")
        data = normalized.encode("utf-8")
    else:
        data = text_or_bytes
    return hashlib.sha256(data).hexdigest()


def verify_corpus_integrity(manifest: BenchmarkManifest, base_dir: Path | None = None) -> str:
    """
    Verify the manifest corpus matches corpus_sha256.
    Returns the resolved corpus text.
    Raises DatasetIntegrityError on mismatch or missing corpus.
    """
    corpus_text = ""
    if manifest.corpus_text is not None:
        corpus_text = manifest.corpus_text
    elif manifest.corpus_file is not None:
        file_path = Path(manifest.corpus_file)
        if not file_path.is_absolute() and base_dir is not None:
            file_path = base_dir / file_path
        if not file_path.exists():
            raise DatasetIntegrityError(f"Corpus file does not exist: {file_path}")
        corpus_text = file_path.read_text(encoding="utf-8")
    else:
        raise DatasetIntegrityError("Benchmark manifest must specify either corpus_text or corpus_file")

    computed = compute_sha256(corpus_text)
    if manifest.corpus_sha256 and manifest.corpus_sha256 != computed:
        raise DatasetIntegrityError(
            f"Corpus SHA-256 mismatch! Manifest expected '{manifest.corpus_sha256}', computed '{computed}'"
        )

    return corpus_text


def load_manifest(path: str | Path) -> tuple[BenchmarkManifest, str]:
    """
    Load a BenchmarkManifest from a JSON file and verify corpus integrity.
    Returns tuple of (manifest, resolved_corpus_text).
    """
    manifest_path = Path(path).resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"Benchmark manifest not found: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest = BenchmarkManifest.model_validate(data)
    corpus_text = verify_corpus_integrity(manifest, base_dir=manifest_path.parent)
    return manifest, corpus_text
