"""Hearst facet taxonomy: forest of independent trees over fixed dimensions."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

DIMENSIONS = ("Topics", "Entities", "Types", "Projects", "Sources")


class CircularFacetError(ValueError):
    """Raised when a facet operation would create circular taxonomic parentage."""


class UnknownFacetError(KeyError):
    """Raised when referencing a facet id that does not exist."""


@dataclass(frozen=True)
class Facet:
    facet_id: str
    dimension: str
    name: str
    parent_id: str | None
    path: str


class Taxonomy:
    """CRUD + traversal over the facet forest stored in the `facets` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_facet(self, dimension: str, name: str, parent_id: str | None = None) -> str:
        if "/" in name or not name.strip():
            raise ValueError(f"invalid facet name: {name!r}")
        parent_path = ""
        if parent_id is not None:
            parent = self.get_facet(parent_id)
            if parent.dimension != dimension:
                raise ValueError("parent facet must share the child dimension")
            parent_path = parent.path
        facet_id = uuid.uuid4().hex
        path = f"{parent_path}/{name}" if parent_path else name
        self.conn.execute(
            "INSERT INTO facets (facet_id, dimension, name, parent_facet_id, path)"
            " VALUES (?, ?, ?, ?, ?)",
            (facet_id, dimension, name, parent_id, path),
        )
        self.conn.commit()
        return facet_id

    def get_facet(self, facet_id: str) -> Facet:
        row = self.conn.execute(
            "SELECT facet_id, dimension, name, parent_facet_id, path FROM facets WHERE facet_id = ?",
            (facet_id,),
        ).fetchone()
        if row is None:
            raise UnknownFacetError(facet_id)
        return Facet(row[0], row[1], row[2], row[3], row[4])

    def move_facet(self, facet_id: str, new_parent_id: str | None) -> Facet:
        """Reparent a facet; raises CircularFacetError when the new parent is self or a descendant."""
        facet = self.get_facet(facet_id)
        if new_parent_id is None:
            new_path = facet.name
        else:
            if new_parent_id == facet_id:
                raise CircularFacetError("a facet cannot parent itself")
            new_parent = self.get_facet(new_parent_id)
            if new_parent.dimension != facet.dimension:
                raise ValueError("parent facet must share the child dimension")
            descendants = {f.facet_id for f in self.subtree(facet_id)}
            if new_parent_id in descendants:
                raise CircularFacetError("new parent is a descendant of the facet")
            new_path = f"{new_parent.path}/{facet.name}"
        old_path = facet.path
        self.conn.execute(
            "UPDATE facets SET parent_facet_id = ?, path = ? WHERE facet_id = ?",
            (new_parent_id, new_path, facet_id),
        )
        # Rewrite descendant paths deterministically.
        for child in self.conn.execute(
            "SELECT facet_id, path FROM facets WHERE path LIKE ? ESCAPE '\\'",
            (old_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%",),
        ).fetchall():
            self.conn.execute(
                "UPDATE facets SET path = ? WHERE facet_id = ?",
                (new_path + child[1][len(old_path):], child[0]),
            )
        self.conn.commit()
        return self.get_facet(facet_id)

    def subtree(self, facet_id: str) -> list[Facet]:
        """The facet plus all descendants, ordered by path (deterministic)."""
        facet = self.get_facet(facet_id)
        like = facet.path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        return [
            Facet(r[0], r[1], r[2], r[3], r[4])
            for r in self.conn.execute(
                "SELECT facet_id, dimension, name, parent_facet_id, path FROM facets"
                " WHERE path = ? OR path LIKE ? ESCAPE '\\' ORDER BY path",
                (facet.path, like),
            ).fetchall()
        ]

    def subtree_ids(self, facet_id: str) -> set[str]:
        return {f.facet_id for f in self.subtree(facet_id)}

    def children(self, facet_id: str) -> list[Facet]:
        return [
            Facet(r[0], r[1], r[2], r[3], r[4])
            for r in self.conn.execute(
                "SELECT facet_id, dimension, name, parent_facet_id, path FROM facets"
                " WHERE parent_facet_id = ? ORDER BY name",
                (facet_id,),
            ).fetchall()
        ]
