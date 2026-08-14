"""Self-organization metrics: Source coverage, orphan counts, duplicates, routing purity, and navigation recall."""

from dataclasses import dataclass, field
from collections import Counter


@dataclass
class OrganizationMetrics:
    """Evaluates the structural integrity and self-organization quality of the index."""
    total_source_atoms: int = 0
    preserved_source_atoms: int = 0
    source_atom_coverage: float = 1.0
    orphan_atom_count: int = 0
    duplicate_atom_count: int = 0
    routing_purity: float = 1.0
    tree_navigation_recall_vs_flat: float = 1.0


def source_atom_coverage(preserved_atom_ids: set[str], expected_atom_ids: set[str]) -> float:
    """Fraction of source input atoms preserved in the immutable spine."""
    if not expected_atom_ids:
        return 1.0
    return len(preserved_atom_ids & expected_atom_ids) / len(expected_atom_ids)


def orphan_count(spine_atom_ids: set[str], indexed_atom_ids: set[str]) -> int:
    """Number of spine atoms not indexed in any tree or vector partition."""
    return len(spine_atom_ids - indexed_atom_ids)


def duplicate_membership_count(node_atom_lists: list[list[str]]) -> int:
    """Number of duplicate atom memberships across nodes when strict partitioning is expected."""
    seen = set()
    duplicates = 0
    for atom_list in node_atom_lists:
        for atom_id in atom_list:
            if atom_id in seen:
                duplicates += 1
            seen.add(atom_id)
    return duplicates


def routing_purity(assignments: list[str], ground_truth_labels: list[str]) -> float:
    """
    Compute cluster/topic routing purity:
    sum_k max_j |c_k intersect t_j| / N
    """
    if not assignments or not ground_truth_labels or len(assignments) != len(ground_truth_labels):
        return 1.0

    cluster_to_labels: dict[str, list[str]] = {}
    for cluster, label in zip(assignments, ground_truth_labels):
        cluster_to_labels.setdefault(cluster, []).append(label)

    purity_sum = 0
    for labels in cluster_to_labels.values():
        most_common_count = Counter(labels).most_common(1)[0][1]
        purity_sum += most_common_count

    return purity_sum / len(assignments)


def navigation_recall(tree_retrieved_ids: list[str], flat_retrieved_ids: list[str]) -> float:
    """Fraction of flat top-K retrieved atoms discovered through tree navigation."""
    if not flat_retrieved_ids:
        return 1.0
    flat_set = set(flat_retrieved_ids)
    tree_set = set(tree_retrieved_ids)
    return len(flat_set & tree_set) / len(flat_set)
