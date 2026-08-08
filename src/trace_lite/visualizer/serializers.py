"""Serializers for converting trace-lite data structures into JSON-compatible dictionaries."""

from typing import TYPE_CHECKING, Any, Dict, List
import json
import numpy as np

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
        "data_dir": str(db.data_dir),
        "embedding_model": db.config.embedding_model,
        "llm_model": db.config.llm_model,
    }


def serialize_forest(db: "TraceLite") -> Dict[str, Any]:
    """Serialize all RAPTOR summary trees and nodes in the cortex."""
    trees = db.trees()
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
                    "summary_text": n.summary_text,
                    "atom_ids": n.atom_ids,
                    "child_node_ids": n.children_ids,
                    "parent_node_id": n.parent_id,
                    "access_count": n.access_count,
                    "last_accessed": _fmt_datetime(n.last_accessed),
                    "is_active": is_active,
                    "energy_score": round(float(energy_score), 4),
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
        "trees": forest_data,
    }


def serialize_vectors(db: "TraceLite") -> Dict[str, Any]:
    """Serialize vector embeddings into 2D/3D projection for point-cloud visualization."""
    points = []
    vectors = []
    metadata_list = []

    vs = db.vector_store
    if hasattr(vs, "nodes"):  # MockVectorStore
        for node_id, (vec, meta) in vs.nodes.items():
            vectors.append(vec)
            node = db.forest.get_node(node_id)
            label = (node.summary_text[:80] + ("..." if len(node.summary_text) > 80 else "")) if node and node.summary_text else f"Node {node_id[:8]}"
            metadata_list.append({
                "node_id": node_id,
                "tree_id": meta.get("tree_id", ""),
                "level": meta.get("level", 0),
                "label": label,
            })
    elif hasattr(vs, "_table") and vs._table is not None:  # LanceDBStore
        try:
            df = vs._table.to_pandas()
            for _, row in df.iterrows():
                node_id = row["node_id"]
                vec = np.array(row["vector"])
                meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row.get("metadata", {})
                node = db.forest.get_node(node_id)
                label = (node.summary_text[:80] + ("..." if len(node.summary_text) > 80 else "")) if node and node.summary_text else f"Node {node_id[:8]}"
                vectors.append(vec)
                metadata_list.append({
                    "node_id": node_id,
                    "tree_id": meta.get("tree_id", ""),
                    "level": meta.get("level", 0),
                    "label": label,
                })
        except Exception:
            pass

    if not vectors:
        return {"points": [], "count": 0}

    # Perform Dimensionality Reduction
    mat = np.array(vectors)
    coords_3d = []

    try:
        if len(vectors) >= 3:
            mean = np.mean(mat, axis=0)
            centered = mat - mean
            u, s, vh = np.linalg.svd(centered, full_matrices=False)
            proj = centered @ vh.T[:, :3]
            max_val = np.max(np.abs(proj)) or 1.0
            coords_3d = (proj / max_val * 10.0).tolist()
        else:
            coords_3d = [[float(idx * 2.0), 0.0, 0.0] for idx in range(len(vectors))]
    except Exception:
        coords_3d = [[0.0, 0.0, 0.0] for _ in range(len(vectors))]

    for i, meta in enumerate(metadata_list):
        c = coords_3d[i] if i < len(coords_3d) else [0.0, 0.0, 0.0]
        points.append({
            "id": meta["node_id"],
            "tree_id": meta["tree_id"],
            "level": meta["level"],
            "label": meta["label"],
            "x": round(float(c[0]), 3),
            "y": round(float(c[1]), 3),
            "z": round(float(c[2]), 3) if len(c) > 2 else 0.0,
        })

    return {
        "points": points,
        "count": len(points),
    }


def serialize_query_result(res: "QueryResult") -> Dict[str, Any]:
    """Serialize a query result and search path trajectory."""
    evidence_items = []
    for item in res.items:
        evidence_items.append({
            "atom_id": item.atom.atom_id,
            "content": item.atom.content,
            "score": round(float(item.score), 4),
            "source": item.source_artifact.document_name if item.source_artifact else "Unknown",
            "traversal_path": item.traversal_path,
        })

    return {
        "query_text": res.query_text,
        "mode": res.mode,
        "results_count": len(evidence_items),
        "items": evidence_items,
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
    }


def serialize_dag(db: "TraceLite", tree_id: str) -> Dict[str, Any]:
    """Serialize tree into DAG nodes and links format for force-graph rendering."""
    tree = db.forest.get_tree(tree_id)
    if not tree:
        return {"tree_id": tree_id, "nodes": [], "links": []}

    nodes = db.forest.get_tree_nodes(tree_id)
    node_map = {n.node_id: n for n in nodes}

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
            "label": label,
            "summary_text": n.summary_text,
            "parent_id": n.parent_id,
            "atom_ids": n.atom_ids,
            "access_count": n.access_count,
            "last_accessed": _fmt_datetime(n.last_accessed),
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
        "nodes": serialized_nodes,
        "links": links,
    }

