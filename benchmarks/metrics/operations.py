"""Operational metrics: Latency percentiles, memory RSS, ingestion throughput, and storage footprint."""

import os
import time
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class OpsProfile:
    """Operational profile containing latency, throughput, memory, and storage metrics."""
    total_queries: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_mean_ms: float = 0.0
    latency_min_ms: float = 0.0
    latency_max_ms: float = 0.0
    ingestion_duration_sec: float = 0.0
    ingestion_atoms_per_sec: float = 0.0
    organization_duration_sec: float = 0.0
    peak_memory_rss_mb: float = 0.0
    storage_total_bytes: int = 0
    storage_bytes_per_atom: float = 0.0
    llm_tokens_used: int = 0


class LatencyTracker:
    """Records query latency samples and computes summary percentiles."""

    def __init__(self):
        self.samples_ms: list[float] = []

    def record(self, duration_sec: float) -> None:
        self.samples_ms.append(duration_sec * 1000.0)

    def summary(self) -> dict[str, float]:
        if not self.samples_ms:
            return {
                "count": 0,
                "p50": 0.0,
                "p95": 0.0,
                "p99": 0.0,
                "mean": 0.0,
                "min": 0.0,
                "max": 0.0,
            }
        arr = np.array(self.samples_ms)
        return {
            "count": len(arr),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
            "mean": float(np.mean(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
        }


class MemoryTracker:
    """Measures process RSS memory."""

    @staticmethod
    def get_current_rss_mb() -> float:
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0


class StorageTracker:
    """Calculates disk directory size."""

    @staticmethod
    def calculate_directory_bytes(directory_path: Path | str) -> int:
        p = Path(directory_path)
        if not p.exists():
            return 0
        if p.is_file():
            return p.stat().st_size
        total = 0
        for entry in p.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        return total
