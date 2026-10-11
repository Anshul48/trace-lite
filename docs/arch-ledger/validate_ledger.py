#!/usr/bin/env python3
"""docs/arch-ledger/validate_ledger.py
Mechanical validator for TRACE-LITE Architecture Ledger.
Enforces zero-trust disk-backed state machine transitions, dependency DAG
integrity, probe execution without mocks, and cryptographic manifest freezing.
"""

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

LEDGER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = LEDGER_ROOT.parents[1]


def resolve_python() -> str:
  venv_python = REPO_ROOT / ".venv" / "bin" / "python"
  if venv_python.exists():
    return str(venv_python)
  return sys.executable


def fail(msg: str) -> None:
  print(f"[LEDGER VALIDATOR ERROR] {msg}", file=sys.stderr)
  sys.exit(1)


def count_executable_code_lines(code: str) -> int:
  """Count non-comment, non-docstring lines using AST parsing."""
  try:
    tree = ast.parse(code)
  except SyntaxError as e:
    fail(f"Probe syntax error: {e}")

  docstring_lines = set()
  for node in ast.walk(tree):
    if (
        isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
        )
        and ast.get_docstring(node, clean=False) is not None
    ):
      body = node.body
      if body and isinstance(body[0], ast.Expr):
        val = body[0].value
        if isinstance(val, (ast.Constant, getattr(ast, "Str", type(None)))):
          docstring_lines.update(range(body[0].lineno, body[0].end_lineno + 1))

  lines = code.splitlines()
  code_lines = 0
  for idx, line in enumerate(lines, start=1):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
      continue
    if idx in docstring_lines:
      continue
    code_lines += 1
  return code_lines


def validate(final_check: bool = False) -> None:
  state_path = LEDGER_ROOT / "STATE.json"
  agenda_path = LEDGER_ROOT / "AGENDA.md"

  if not state_path.exists():
    fail("STATE.json does not exist.")

  with open(state_path, "r", encoding="utf-8") as f:
    try:
      state = json.load(f)
    except json.JSONDecodeError as e:
      fail(f"STATE.json is corrupted: {e}")

  # Strict Final Freeze Guard: cannot invoke --final if not ROADMAP_FROZEN
  if final_check:
    if state.get("state") != "ROADMAP_FROZEN":
      fail(
          "Final freeze check (--final) requires state == 'ROADMAP_FROZEN', got"
          f" '{state.get('state')}'."
      )

  # In INIT state (and non-final check), agenda and topics are not yet generated
  if state.get("state") == "INIT":
    print("[LEDGER VALIDATOR PASS] Initial state verified (state: 'INIT', 0 topics).")
    return

  if not agenda_path.exists():
    fail("AGENDA.md does not exist.")

  topics = state.get("topics", [])
  if not topics:
    fail("STATE.json contains no topics.")

  topic_map = {t.get("id", ""): t for t in topics}
  py_bin = resolve_python()

  for t in topics:
    tid = t.get("id", "UNKNOWN")
    status = t.get("status")
    deps = t.get("dependencies", [])

    # Verify schema completeness
    for req_field in [
        "id",
        "title",
        "dependencies",
        "status",
        "probe_spec",
        "probe_path",
        "adr_path",
        "receipt_path",
    ]:
      if req_field not in t:
        fail(f"Topic {tid} missing required schema field '{req_field}'.")

    # Verify dependency constraints
    if status in ("IN_SESSION", "ACCEPTED"):
      for dep in deps:
        if topic_map.get(dep, {}).get("status") != "ACCEPTED":
          fail(
              f"Topic {tid} is {status} but prerequisite {dep} is not ACCEPTED."
          )

    # Verify artifacts for ACCEPTED topics
    if status == "ACCEPTED":
      if not t.get("probe_path"):
        fail(f"Topic {tid} ACCEPTED but probe_path is null.")
      if not t.get("adr_path"):
        fail(f"Topic {tid} ACCEPTED but adr_path is null.")
      if not t.get("receipt_path"):
        fail(f"Topic {tid} ACCEPTED but receipt_path is null.")

      adr_path = LEDGER_ROOT / t["adr_path"]
      probe_path = LEDGER_ROOT / t["probe_path"]
      receipt_path = LEDGER_ROOT / t["receipt_path"]

      if not adr_path.exists():
        fail(f"ADR missing for {tid}: {adr_path}")
      if not probe_path.exists():
        fail(f"Micro-probe missing for {tid}: {probe_path}")
      if not receipt_path.exists():
        fail(f"Receipt missing for {tid}: {receipt_path}")

      code = probe_path.read_text(encoding="utf-8")
      code_lines = count_executable_code_lines(code)
      if code_lines > 50:
        fail(
            f"Probe {probe_path} exceeds 50 lines of code (found {code_lines})."
        )

      # AST Inspection: verify no string constant contains ':memory:' and no mocks are imported/used
      tree = ast.parse(code)
      for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
          if ":memory:" in node.value:
            fail(
                f"Probe {probe_path} illegally uses in-memory SQLite"
                " (':memory:'). Must use disk scratch."
            )
        elif isinstance(node, ast.Import):
          for alias in node.names:
            if "mock" in alias.name:
              fail(f"Probe {probe_path} illegally imports mock ({alias.name}).")
        elif isinstance(node, ast.ImportFrom):
          if node.module and "mock" in node.module:
            fail(f"Probe {probe_path} illegally imports from mock ({node.module}).")
          for alias in node.names:
            if "mock" in alias.name:
              fail(f"Probe {probe_path} illegally imports mock ({alias.name}).")
        elif isinstance(node, ast.Attribute) and node.attr == "mock":
          fail(f"Probe {probe_path} accesses mock attribute ({node.attr}).")

      # Verify receipt contents
      with open(receipt_path, "r", encoding="utf-8") as rf:
        receipt = json.load(rf)
      if receipt.get("exit_code") != 0:
        fail(f"Receipt for {tid} indicates failure exit code.")

      # Execute probe independently in isolated environment with timeout handling
      try:
        res = subprocess.run(
            [py_bin, str(probe_path)],
            capture_output=True,
            text=True,
            timeout=12,
            cwd=str(REPO_ROOT),
        )
      except subprocess.TimeoutExpired:
        fail(f"Probe {probe_path} execution timed out after 12s.")

      if res.returncode != 0:
        fail(f"Probe {probe_path} failed execution:\n{res.stderr}")

  # Final Freeze Validation
  if final_check:
    manifest_path = LEDGER_ROOT / "MANIFEST.json"
    roadmap_path = LEDGER_ROOT / "ROADMAP_M1.md"
    if not manifest_path.exists():
      fail("MANIFEST.json missing for final freeze.")
    if not roadmap_path.exists():
      fail("ROADMAP_M1.md missing for final freeze.")

    manifest_bytes = manifest_path.read_bytes()
    expected_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if state.get("manifest_hash") != expected_manifest_hash:
      fail(
          f"Top-level state manifest_hash mismatch: expected {expected_manifest_hash}, got {state.get('manifest_hash')}"
      )

    with open(manifest_path, "r", encoding="utf-8") as mf:
      manifest = json.load(mf)

    # Verify every accepted topic's artifacts are indexed in MANIFEST.json
    for t in topics:
      if t.get("status") == "ACCEPTED":
        for field in ["adr_path", "probe_path", "receipt_path"]:
          rel_p = t.get(field)
          if rel_p and rel_p not in manifest:
            fail(f"Artifact {rel_p} for topic {t['id']} not indexed in MANIFEST.json.")

    # Verify bit-for-bit SHA-256 for all manifest entries
    for rel_path, expected_hash in manifest.items():
      target_file = LEDGER_ROOT / rel_path
      if not target_file.exists():
        fail(f"Manifest entry missing on disk: {rel_path}")
      actual_hash = hashlib.sha256(target_file.read_bytes()).hexdigest()
      if actual_hash != expected_hash:
        fail(
            f"Hash mismatch for {rel_path}: expected {expected_hash}, got"
            f" {actual_hash}"
        )

  print(
      f"[LEDGER VALIDATOR PASS] All state machine invariants verified"
      f" ({len(topics)} topics checked)."
  )


if __name__ == "__main__":
  if "--help" in sys.argv or "-h" in sys.argv:
    print("Usage: validate_ledger.py [--final]")
    sys.exit(0)
  is_final = "--final" in sys.argv
  validate(final_check=is_final)
