"""Energy model: Power-law decay and retrieval activation spikes."""

import math
from datetime import datetime, timezone


class EnergyModel:
    """
    V1 Energy Dynamics:
    - Power-law decay on node visibility
    - Retrieval spikes on access
    - Active threshold gating for LATTICE traversal
    """

    def __init__(self, decay_exponent: float = 0.5, retrieval_threshold: float = 0.1):
        self.decay_exponent = decay_exponent
        self.retrieval_threshold = retrieval_threshold

    def compute_activation(
        self,
        last_accessed: str | datetime,
        access_count: int = 1,
        now: datetime | None = None,
    ) -> float:
        now_dt = now or datetime.now(timezone.utc)

        if isinstance(last_accessed, str):
            last_dt = datetime.fromisoformat(last_accessed)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        else:
            last_dt = last_accessed
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)

        hours_since = max(0.001, (now_dt - last_dt).total_seconds() / 3600.0)

        # Less than 1 hour -> fully active (1.0)
        if hours_since <= 1.0:
            return 1.0

        # Frequency boost (logarithmic scale)
        base = 1.0 + math.log1p(access_count)

        # Power-law decay: base * (hours)^(-decay_exponent)
        activation = base * (hours_since ** (-self.decay_exponent))
        return float(activation)

    def is_active(
        self,
        last_accessed: str | datetime,
        access_count: int = 1,
        now: datetime | None = None,
    ) -> bool:
        return self.compute_activation(last_accessed, access_count, now) >= self.retrieval_threshold
