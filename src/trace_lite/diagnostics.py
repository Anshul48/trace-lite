"""Typed, redaction-safe diagnostics for staged derived-index builds."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


DiagnosticStage = Literal[
    "preflight",
    "routing",
    "tree_naming",
    "naming",
    "embedding",
    "summary_generation",
    "validation",
    "activation",
    "failure",
    "build",
    "retries",
    "tree naming",
    "summary generation",
]
DiagnosticStatus = Literal["attempt", "retrying", "success", "final_failure"]


class BuildDiagnostic(BaseModel):
    """One safe progress/failure event emitted during a staged build.

    Raw prompts, model responses, provider exception bodies, and credentials are
    deliberately absent.  Consumers can print or serialize this model without
    turning a retry log into another trust-boundary leak.
    """

    stage: DiagnosticStage
    tree_id: str | None = None
    node_id: str | None = None
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    failure_code: str | None = None
    message: str = ""
    status: DiagnosticStatus = "attempt"
    retrying: bool = False
    final_failure: bool = False

    @property
    def explanation(self) -> str:
        return self.message


def diagnostic_dict(event: BuildDiagnostic) -> dict:
    """Dump a diagnostic across Pydantic v1/v2 environments."""
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        payload = model_dump()
    else:
        payload = event.dict()
    payload.setdefault("explanation", payload.get("message", ""))
    return payload


__all__ = ["BuildDiagnostic", "DiagnosticStage", "DiagnosticStatus", "diagnostic_dict"]
