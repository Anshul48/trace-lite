"""Dataset adapters for the complete benchmark matrix (P0 through P2)."""

from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery
from benchmarks.adapters.beir_adapter import BeirAdapter
from benchmarks.adapters.bright_adapter import BrightAdapter
from benchmarks.adapters.multihop_adapter import MultiHopAdapter
from benchmarks.adapters.hichunk_adapter import HiChunkAdapter
from benchmarks.adapters.longmemeval_adapter import LongMemEvalAdapter
from benchmarks.adapters.trec_rag_adapter import TrecRagAdapter
from benchmarks.adapters.mteb_adapter import MtebAdapter
from benchmarks.adapters.ann_scale_adapter import AnnScaleAdapter
from benchmarks.adapters.hipporag_adapter import HippoRagAdapter

__all__ = [
    "BaseDatasetAdapter",
    "CorpusDocument",
    "BenchmarkQuery",
    "BeirAdapter",
    "BrightAdapter",
    "MultiHopAdapter",
    "HiChunkAdapter",
    "LongMemEvalAdapter",
    "TrecRagAdapter",
    "MtebAdapter",
    "AnnScaleAdapter",
    "HippoRagAdapter",
]
