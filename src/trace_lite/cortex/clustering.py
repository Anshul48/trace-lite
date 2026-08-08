"""Clustering pipeline using UMAP and HDBSCAN for RAPTOR tree building."""

from collections import defaultdict
import numpy as np


class ClusteringPipeline:
    """
    Groups embedding vectors into semantically coherent clusters.
    Uses UMAP for dimensionality reduction + HDBSCAN for density clustering.
    Falls back gracefully for small datasets (< 10 items).
    """

    def __init__(self, n_components: int = 5, min_cluster_size: int = 3):
        self.n_components = n_components
        self.min_cluster_size = min_cluster_size

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

        if n_samples < self.min_cluster_size:
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
            # Fallback simple k-means style clustering using cosine similarity
            labels = self._fallback_clustering(embeddings)

        cluster_map = defaultdict(list)
        for idx, label in enumerate(labels):
            cluster_map[label].append(idx)

        orphans = cluster_map.pop(-1, [])
        clusters = [members for members in cluster_map.values() if len(members) > 0]

        # If no clusters formed and everything was noise, group into clusters of size min_cluster_size
        if not clusters and orphans:
            clusters = [
                orphans[i : i + self.min_cluster_size]
                for i in range(0, len(orphans), self.min_cluster_size)
            ]
            orphans = []

        return clusters, orphans

    def _fallback_clustering(self, embeddings: np.ndarray) -> np.ndarray:
        n_samples = len(embeddings)
        k = max(1, n_samples // self.min_cluster_size)
        if k == 1:
            return np.zeros(n_samples, dtype=int)

        # Simple greedy k-means
        from sklearn.cluster import KMeans

        kmeans = KMeans(n_clusters=k, random_state=42, n_init=3)
        return kmeans.fit_predict(embeddings)
