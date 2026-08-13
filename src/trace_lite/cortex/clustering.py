"""Clustering pipeline using UMAP and HDBSCAN for RAPTOR tree building."""

from collections import defaultdict
import numpy as np


class ClusteringPipeline:
    """
    Groups embedding vectors into semantically coherent clusters.
    Uses UMAP for dimensionality reduction + HDBSCAN for density clustering.
    Falls back gracefully for small datasets (< 10 items).
    """

    def __init__(
        self,
        n_components: int = 5,
        min_cluster_size: int = 3,
        max_children: int = 8,
        projection_max_samples: int = 10000,
        max_children_per_summary: int | None = None,
    ):
        if max_children_per_summary is not None:
            max_children = max_children_per_summary
        if n_components < 1:
            raise ValueError("n_components must be positive")
        if min_cluster_size < 1:
            raise ValueError("min_cluster_size must be positive")
        if max_children < 2:
            raise ValueError("max_children must be at least 2")
        if projection_max_samples < 1:
            raise ValueError("projection_max_samples must be positive")
        self.n_components = n_components
        self.min_cluster_size = min_cluster_size
        self.max_children = max_children
        self.projection_max_samples = projection_max_samples

    def cluster(self, embeddings: np.ndarray) -> tuple[list[list[int]], list[int]]:
        """
        Cluster embeddings.
        Returns:
            clusters: List of index lists (e.g. [[0, 2], [1, 3, 4]])
            orphans: List of unclustered indices (noise)
        """
        n_samples = len(embeddings)

        if n_samples == 0:
            return [], []

        if n_samples <= self.max_children:
            # All items in single cluster if tiny
            return [list(range(n_samples))], []

        # For small sample sizes, simplify UMAP/HDBSCAN or use cosine KMeans
        try:
            from umap import UMAP
            import hdbscan

            input_dim = embeddings.shape[1]
            n_neighbors = min(15, max(2, n_samples - 1))
            n_comp = min(self.n_components, n_samples - 2, input_dim - 1)
            if n_comp < 2:
                n_comp = min(2, input_dim)
            if n_comp < 1:
                n_comp = 1

            # UMAP is deliberately skipped for very large layers.  The
            # nearest-neighbour grouping fallback is deterministic and avoids a large
            # projection becoming the availability bottleneck for indexing.
            if n_samples > self.projection_max_samples:
                raise RuntimeError("projection sample limit exceeded")
            reducer = UMAP(
                n_neighbors=n_neighbors,
                n_components=n_comp,
                metric="cosine",
                random_state=42,
            )

            reduced = reducer.fit_transform(embeddings)

            clusterer = hdbscan.HDBSCAN(
                min_cluster_size=min(self.min_cluster_size, n_samples),
                metric="euclidean",
                cluster_selection_method="eom",
            )
            labels = clusterer.fit_predict(reduced)

        except Exception:
            # Fallback simple k-means-style grouping using cosine similarity;
            # clustering fallback is not an LLM or summary fallback.
            labels = self._fallback_clustering(embeddings)

        cluster_map = defaultdict(list)
        for idx, label in enumerate(labels):
            cluster_map[label].append(idx)

        orphans = cluster_map.pop(-1, [])
        clusters = [members for members in cluster_map.values() if len(members) > 0]

        return clusters, orphans

    def split_oversized(
        self, indices: list[int], embeddings: np.ndarray | None = None
    ) -> list[list[int]]:
        """Split an index group into bounded, deterministic semantic groups."""
        ordered = sorted(dict.fromkeys(indices))
        if len(ordered) <= self.max_children or embeddings is None:
            return [
                ordered[offset : offset + self.max_children]
                for offset in range(0, len(ordered), self.max_children)
            ]
        group_count = int(np.ceil(len(ordered) / self.max_children))
        seed_positions = np.linspace(0, len(ordered) - 1, group_count, dtype=int)
        seeds = [ordered[position] for position in sorted(set(seed_positions))]
        groups = [[seed] for seed in seeds]
        remaining = [index for index in ordered if index not in seeds]
        for index in remaining:
            available = [
                group_index for group_index, group in enumerate(groups)
                if len(group) < self.max_children
            ]
            best = max(
                available,
                key=lambda group_index: (
                    self.cosine_similarity(
                        embeddings[index],
                        np.mean([embeddings[item] for item in groups[group_index]], axis=0),
                    ),
                    -group_index,
                ),
            )
            groups[best].append(index)
        return [sorted(group) for group in groups]

    @staticmethod
    def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
        left_norm = np.linalg.norm(left) + 1e-9
        right_norm = np.linalg.norm(right) + 1e-9
        return float(np.dot(left, right) / (left_norm * right_norm))

    def _fallback_clustering(self, embeddings: np.ndarray) -> np.ndarray:
        n_samples = len(embeddings)
        k = max(1, n_samples // self.min_cluster_size)
        if k == 1:
            return np.zeros(n_samples, dtype=int)

        # Simple greedy k-means
        from sklearn.cluster import KMeans

        kmeans = KMeans(n_clusters=k, random_state=42, n_init=3)
        return kmeans.fit_predict(embeddings)
