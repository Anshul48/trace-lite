"""Retrieval quality metrics: Recall@K, nDCG@K, Complete Gold Coverage, Precision, MRR, Abstention."""

import math
from dataclasses import dataclass, field


@dataclass
class CaseRetrievalMetrics:
    """Computed retrieval metrics for an individual query evaluation case."""
    case_id: str
    category: str
    recall_at_5: float = 0.0
    recall_at_20: float = 0.0
    recall_at_100: float = 0.0
    ndcg_at_10: float = 0.0
    ndcg_at_30: float = 0.0
    complete_gold_coverage_at_10: float = 0.0
    complete_gold_coverage_at_20: float = 0.0
    citation_precision_at_5: float = 0.0
    citation_precision_at_10: float = 0.0
    mrr: float = 0.0
    multihop_coverage: float = 0.0
    abstention_accuracy: float = 1.0


def recall_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Compute Recall@K."""
    if not gold_ids:
        # Out-of-scope queries: if no retrieval expected, recall is 1.0 if empty, else 0.0
        return 1.0 if not retrieved_ids[:k] else 0.0
    gold_set = set(gold_ids)
    retrieved_k = set(retrieved_ids[:k])
    return len(retrieved_k & gold_set) / len(gold_set)


def complete_gold_coverage(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Return 1.0 if ALL gold atom IDs are within top-K, else 0.0."""
    if not gold_ids:
        return 1.0 if not retrieved_ids[:k] else 0.0
    gold_set = set(gold_ids)
    retrieved_k = set(retrieved_ids[:k])
    return 1.0 if gold_set.issubset(retrieved_k) else 0.0


def citation_precision(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Compute Precision@K."""
    retrieved_k = retrieved_ids[:k]
    if not retrieved_k:
        return 1.0 if not gold_ids else 0.0
    gold_set = set(gold_ids)
    hits = sum(1 for item_id in retrieved_k if item_id in gold_set)
    return hits / len(retrieved_k)


def ndcg_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Compute Normalized Discounted Cumulative Gain at K (nDCG@K)."""
    if not gold_ids:
        return 1.0 if not retrieved_ids[:k] else 0.0
    gold_set = set(gold_ids)
    dcg = 0.0
    for i, item_id in enumerate(retrieved_ids[:k]):
        if item_id in gold_set:
            dcg += 1.0 / math.log2(i + 2)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(gold_set))))
    return dcg / idcg if idcg > 0.0 else 0.0


def mrr_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int = 100) -> float:
    """Compute Mean Reciprocal Rank (MRR)."""
    if not gold_ids:
        return 1.0 if not retrieved_ids[:k] else 0.0
    gold_set = set(gold_ids)
    for i, item_id in enumerate(retrieved_ids[:k]):
        if item_id in gold_set:
            return 1.0 / (i + 1)
    return 0.0


def multihop_coverage(retrieved_ids: list[str], gold_ids: list[str], k: int = 20) -> float:
    """Percentage of multi-hop evidence hops recovered in top-K."""
    return recall_at_k(retrieved_ids, gold_ids, k)


def abstention_accuracy(predicted_abstention: bool, expected_abstention: bool) -> float:
    """Return 1.0 if predicted abstention matches expected, else 0.0."""
    return 1.0 if predicted_abstention == expected_abstention else 0.0


def evaluate_case_retrieval(
    case_id: str,
    category: str,
    retrieved_ids: list[str],
    gold_ids: list[str],
    is_abstaining: bool,
    expected_abstention: bool,
) -> CaseRetrievalMetrics:
    """Compute all standard retrieval metrics for one case."""
    if expected_abstention:
        abstain_acc = abstention_accuracy(is_abstaining, expected_abstention)
        return CaseRetrievalMetrics(
            case_id=case_id,
            category=category,
            recall_at_5=1.0 if is_abstaining else 0.0,
            recall_at_20=1.0 if is_abstaining else 0.0,
            recall_at_100=1.0 if is_abstaining else 0.0,
            ndcg_at_10=1.0 if is_abstaining else 0.0,
            ndcg_at_30=1.0 if is_abstaining else 0.0,
            complete_gold_coverage_at_10=1.0 if is_abstaining else 0.0,
            complete_gold_coverage_at_20=1.0 if is_abstaining else 0.0,
            citation_precision_at_5=1.0 if is_abstaining else 0.0,
            citation_precision_at_10=1.0 if is_abstaining else 0.0,
            mrr=1.0 if is_abstaining else 0.0,
            multihop_coverage=1.0 if is_abstaining else 0.0,
            abstention_accuracy=abstain_acc,
        )

    return CaseRetrievalMetrics(
        case_id=case_id,
        category=category,
        recall_at_5=recall_at_k(retrieved_ids, gold_ids, 5),
        recall_at_20=recall_at_k(retrieved_ids, gold_ids, 20),
        recall_at_100=recall_at_k(retrieved_ids, gold_ids, 100),
        ndcg_at_10=ndcg_at_k(retrieved_ids, gold_ids, 10),
        ndcg_at_30=ndcg_at_k(retrieved_ids, gold_ids, 30),
        complete_gold_coverage_at_10=complete_gold_coverage(retrieved_ids, gold_ids, 10),
        complete_gold_coverage_at_20=complete_gold_coverage(retrieved_ids, gold_ids, 20),
        citation_precision_at_5=citation_precision(retrieved_ids, gold_ids, 5),
        citation_precision_at_10=citation_precision(retrieved_ids, gold_ids, 10),
        mrr=mrr_at_k(retrieved_ids, gold_ids),
        multihop_coverage=multihop_coverage(retrieved_ids, gold_ids, 20),
        abstention_accuracy=abstention_accuracy(is_abstaining, expected_abstention),
    )
