"""Deterministic synthetic corpus and workload generators for scale benchmarking."""

from benchmarks.generators.scale_corpus import generate_scale_benchmark, ScaleTier

__all__ = [
    "generate_scale_benchmark",
    "ScaleTier",
]
