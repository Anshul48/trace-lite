"""Serializers for converting trace-lite data structures into JSON-compatible dictionaries."""

from typing import TYPE_CHECKING, Any, Dict, List
import numpy as np
from trace_lite.cortex import VectorStoreError

if TYPE_CHECKING:
    from trace_lite.db import TraceLite
    from trace_lite.engines import QueryResult


def _fmt_datetime(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def serialize_status(db: "TraceLite") -> Dict[str, Any]:
    """Serialize database status and metrics."""
    s = db.status()
    return {
        "total_atoms": s.total_atoms,
        "total_trees": s.total_trees,
        "estimated_tokens": s.estimated_tokens,
        "active_nodes": s.active_nodes,
        "total_nodes": s.total_nodes,
        "pending_atoms": s.pending_atoms,
        "pending_trees": s.pending_trees,
        "orphaned_atoms": s.orphaned_atoms,
        "needs_organization": s.needs_organization,
        "needs_recovery": s.needs_recovery,
        "indexed_atoms": s.indexed_atoms,
        "valid_summaries": s.valid_summaries,
        "fallback_summaries": s.fallback_summaries,
        "vector_count": s.vector_count,
        "active_build_id": s.active_build_id,
        "validation_state": s.validation_state,
        "validation_errors": s.validation_errors or [],
        "validation_warnings": s.validation_warnings or [],
        "index_trusted": s.index_trusted,
        "structure_present": s.structure_present,
        "quality_verified": s.quality_verified,
        "credential_state": s.credential_state,
        "active_provider": s.active_provider,
        "data_dir": str(db.data_dir),
        "embedding_model": db.config.embedding_model,
        "llm_model": db.config.llm_model,
    }


def serialize_forest(db: "TraceLite") -> Dict[str, Any]:
    """Serialize all RAPTOR summary trees and nodes in the cortex."""
    trees = db.trees()
    health = db.status()
    forest_data = []

    for t in trees:
        nodes = db.forest.get_tree_nodes(t.tree_id)
        serialized_nodes = []
        for n in nodes:
            is_active = db.energy.is_active(n.last_accessed, n.access_count)
            energy_score = db.energy.compute_activation(
                last_accessed=n.last_accessed, access_count=n.access_count
            )
            serialized_nodes.append(
                {
                    "node_id": n.node_id,
                    "tree_id": n.tree_id,
                    "level": n.level,
                    "node_type": n.node_type,
                    "summary_text": n.summary_text,
                    "summary_provenance": n.summary_provenance,
                    "atom_ids": n.atom_ids,
                    "child_node_ids": n.children_ids,
                    "parent_node_id": n.parent_id,
                    "access_count": n.access_count,
                    "last_accessed": _fmt_datetime(n.last_accessed),
                    "is_active": is_active,
                    "energy_score": round(float(energy_score), 4),
                    "structurally_present": True,
                    "quality_verified": bool(health.quality_verified),
                }
            )

        forest_data.append(
            {
                "tree_id": t.tree_id,
                "name": t.name,
                "description": t.description,
                "root_node_id": t.root_node_id,
                "leaf_count": t.leaf_count,
                "depth": t.depth,
                "node_count": t.node_count,
                "nodes": serialized_nodes,
            }
        )

    return {
        "total_trees": len(forest_data),
        "index_health": {
            "state": health.validation_state,
            "active_build_id": health.active_build_id,
            "warnings": health.validation_warnings or [],
            "errors": health.validation_errors or [],
            "structure_present": health.structure_present,
            "quality_verified": health.quality_verified,
        },
        "trees": forest_data,
    }


def serialize_vectors(db: "TraceLite") -> Dict[str, Any]:
    """Serialize vectors through the public store API with bounded sampling."""
    points = []
    vectors = []
    metadata_list = []

    try:
        records = list(db.vector_store.iter_vectors())
    except Exception as exc:
        raise VectorStoreError(f"Vector explorer extraction failed: {exc}") from exc

    total_count = len(records)
    max_points = db.config.projection_max_points

    # Deterministic stratified sample: sort each tree/level stratum by node ID,
    # then select evenly spaced records so small trees and levels remain visible.
    strata: dict[tuple[str, int], list[tuple[str, np.ndarray, dict]]] = {}
    for record in records:
        node_id, vector, metadata = record
        key = (str(metadata.get("tree_id", "")), int(metadata.get("level", 0)))
        strata.setdefault(key, []).append((node_id, np.asarray(vector), metadata))
    for values in strata.values():
        values.sort(key=lambda item: item[0])
    selected: list[tuple[str, np.ndarray, dict]] = []
    if total_count <= max_points:
        selected = [record for key in sorted(strata) for record in strata[key]]
    else:
        keys = sorted(strata)
        if max_points < len(keys):
            selected = [strata[key][0] for key in keys[:max_points]]
            keys = []
        quotas = {key: 1 for key in keys}
        remaining = max(0, max_points - len(keys))
        while remaining:
            eligible = [key for key in keys if quotas[key] < len(strata[key])]
            if not eligible:
                break
            for key in eligible:
                if remaining <= 0:
                    break
                quotas[key] += 1
                remaining -= 1
        for key in keys:
            values = strata[key]
            quota = quotas[key]
            if quota >= len(values):
                selected.extend(values)
            elif quota == 1:
                selected.append(values[0])
            else:
                indices = np.linspace(0, len(values) - 1, quota, dtype=int)
                selected.extend(values[index] for index in sorted(set(indices)))

    for node_id, vec, meta in selected:
        node = db.forest.get_node(node_id)
        label = (
            (node.summary_text[:80] + ("..." if len(node.summary_text) > 80 else ""))
            if node and node.summary_text
            else f"Node {node_id[:8]}"
        )
        vectors.append(np.asarray(vec, dtype=np.float32))
        metadata_list.append({
            "node_id": node_id,
            "tree_id": meta.get("tree_id", ""),
            "level": meta.get("level", 0),
            "label": label,
            "summary_provenance": meta.get(
                "summary_provenance", node.summary_provenance if node else None
            ),
        })

    if not vectors:
        return {
            "available": False,
            "diagnostic": {"code": "empty", "message": "No active embeddings are available."},
            "points": [],
            "count": 0,
            "total_count": 0,
            "displayed_count": 0,
            "projection_method": "none",
            "sampled": False,
        }

    # Perform Dimensionality Reduction
    mat = np.array(vectors)
    coords_3d = []
    projection_method = "deterministic_pca"

    try:
        if len(vectors) >= 3:
            mean = np.mean(mat, axis=0)
            centered = mat - mean
            u, s, vh = np.linalg.svd(centered, full_matrices=False)
            proj = centered @ vh.T[:, : db.config.projection_components]
            max_val = np.max(np.abs(proj)) or 1.0
            coords_3d = (proj / max_val * 10.0).tolist()
        else:
            coords_3d = [[float(idx * 2.0), 0.0, 0.0] for idx in range(len(vectors))]
    except Exception:
        coords_3d = [[0.0, 0.0, 0.0] for _ in range(len(vectors))]
        # This fallback is only for plotting coordinates. It never supplies
        # summary text or changes index trust/provenance.
        projection_method = "deterministic_projection_fallback"

    for i, meta in enumerate(metadata_list):
        c = coords_3d[i] if i < len(coords_3d) else [0.0, 0.0, 0.0]
        points.append({
            "id": meta["node_id"],
            "tree_id": meta["tree_id"],
            "level": meta["level"],
            "label": meta["label"],
            "summary_provenance": meta["summary_provenance"],
            "x": round(float(c[0]), 3),
            "y": round(float(c[1]), 3),
            "z": round(float(c[2]), 3) if len(c) > 2 else 0.0,
        })

    return {
        "available": True,
        "diagnostic": None,
        "points": points,
        "count": len(points),
        "total_count": total_count,
        "displayed_count": len(points),
        "projection_method": projection_method,
        "sampled": len(points) < total_count,
    }


def serialize_query_result(res: "QueryResult") -> Dict[str, Any]:
    """Serialize a query result and search path trajectory."""
    evidence_items = []
    for item in res.items:
        if item.atom is None:
            continue
        evidence_items.append({
            "atom_id": item.atom.atom_id,
            "content": item.atom.content,
            "score": round(float(item.score), 4),
            "source": item.source_artifact.document_name if item.source_artifact else "Unknown",
            "traversal_path": item.traversal_path,
            "source_location": getattr(item, "source_location", {}),
            "channel_scores": getattr(item, "channel_scores", {}),
        })

    return {
        "query_text": res.query_text,
        "mode": res.mode,
        "results_count": len(evidence_items),
        "items": evidence_items,
        "warnings": list(res.warnings),
        "sufficiency_state": getattr(res, "sufficiency_state", "answerable"),
    }


def serialize_spine(db: "TraceLite") -> Dict[str, Any]:
    """Serialize Spine artifacts, atoms, and events."""
    artifacts = db.spine.list_artifacts()
    art_list = []
    for art in artifacts:
        atoms = db.spine.get_atoms_by_artifact(art.artifact_id)
        first_atom_content = atoms[0].content if atoms else ""
        art_list.append({
            "artifact_id": art.artifact_id,
            "document_name": art.document_name or "Untitled Document",
            "source_uri": art.source_uri,
            "created_at": _fmt_datetime(art.created_at),
            "atom_count": len(atoms),
            "content_preview": first_atom_content[:150] + ("..." if len(first_atom_content) > 150 else ""),
        })

    events = db.spine.get_events(limit=20)
    event_list = [
        {
            "event_id": e.event_id,
            "event_type": e.event_type,
            "timestamp": _fmt_datetime(e.timestamp),
            "payload": e.payload,
        }
        for e in events
    ]

    return {
        "artifacts_count": len(art_list),
        "artifacts": art_list,
        "events": event_list,
        "folders": db.spine.list_source_connections(),
    }


def serialize_dag(db: "TraceLite", tree_id: str) -> Dict[str, Any]:
    """Serialize tree into DAG nodes and links format for force-graph rendering."""
    tree = db.forest.get_tree(tree_id)
    if not tree:
        return {"tree_id": tree_id, "nodes": [], "links": []}

    nodes = db.forest.get_tree_nodes(tree_id)
    node_map = {n.node_id: n for n in nodes}
    quality_verified = db.status().quality_verified

    serialized_nodes = []
    links = []
    seen_links = set()

    for n in nodes:
        label = (n.summary_text[:80] + ("..." if len(n.summary_text or "") > 80 else "")) if n.summary_text else f"Node {n.node_id[:8]}"
        serialized_nodes.append({
            "id": n.node_id,
            "node_id": n.node_id,
            "tree_id": n.tree_id,
            "level": n.level,
            "node_type": n.node_type,
            "type": n.node_type,
            "label": label,
            "summary_text": n.summary_text,
            "summary_provenance": n.summary_provenance,
            "parent_id": n.parent_id,
            "atom_ids": n.atom_ids,
            "access_count": n.access_count,
            "last_accessed": _fmt_datetime(n.last_accessed),
            "structurally_present": True,
            "quality_verified": bool(quality_verified),
        })

        if n.children_ids:
            for cid in n.children_ids:
                if cid in node_map:
                    edge_key = (n.node_id, cid)
                    if edge_key not in seen_links:
                        seen_links.add(edge_key)
                        links.append({"source": n.node_id, "target": cid})
        elif n.parent_id and n.parent_id in node_map:
            edge_key = (n.parent_id, n.node_id)
            if edge_key not in seen_links:
                seen_links.add(edge_key)
                links.append({"source": n.parent_id, "target": n.node_id})

    return {
        "tree_id": tree_id,
        "name": tree.name,
        "root_id": tree.root_node_id,
        "node_count": len(serialized_nodes),
        "nodes": serialized_nodes,
        "links": [{**link, "type": "parent"} for link in links],
        "topology": "parent-child",
    }


def serialize_workspace(db: "TraceLite", project: dict | None = None) -> Dict[str, Any]:
    """Serialize the small set of facts needed to render the workspace home."""
    status = serialize_status(db)
    spine = serialize_spine(db)
    needs_recovery = bool(status["needs_recovery"])
    needs_organization = bool(status["needs_organization"])
    if needs_recovery:
        next_action = "recovery"
    elif needs_organization:
        next_action = "organize"
    elif status["total_atoms"] == 0:
        next_action = "add_source"
    else:
        next_action = "ask"

    return {
        "project": project,
        "status": status,
        "sources": {
            "count": spine["artifacts_count"],
            "artifacts": spine["artifacts"],
        },
        "folders": spine["folders"],
        "next_action": next_action,
    }
