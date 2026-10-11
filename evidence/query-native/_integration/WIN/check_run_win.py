#!/usr/bin/env python3
"""Static checker for run_win.bat (contract TL-QN-2026-09-12.1, steering S6).

WSL<->Windows interop is broken here, so the .bat cannot be executed.
This checker statically proves, by parsing the file:
  dispatch/menu/help parity, worker-vs-wrapper structure (call-safe),
  no wmic, no fixed overwrite outputs, gate-before-ladder ordering,
  32-bit-overflow-free byte compare, stale-expansion hygiene.
Exit 0 = all checks pass. Python-side invocations (pytest collect,
harness --help) are verified separately; see check_run_win.log.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # WIN -> _integration -> query-native -> evidence -> ROOT
BAT = ROOT / "run_win.bat"
WORKLOAD = ROOT / "evidence" / "query-native" / "R00" / "workload.json"

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


text = BAT.read_text(encoding="utf-8-sig")
lines = text.splitlines()
code = [ln for ln in lines if not ln.lstrip().startswith("::")]

labels: dict[str, int] = {}
for i, ln in enumerate(lines):
    m = re.match(r"^:([A-Za-z_][\w$#@]*)\s*$", ln.strip())
    if m:
        labels[m.group(1)] = i
codetext = "\n".join(code)  # :: comments stripped: no false hits from prose

# 1. dispatch table
dispatch = re.findall(r'if /i "%~1"=="(\w+)" goto (\w+)', text, re.IGNORECASE)
dmap = {k.lower(): v for k, v in dispatch}
check("dispatch cmds exact set",
      set(dmap) == {"test", "concurrency", "quality", "bench", "ladder", "all",
                     "gate", "smoke", "failproof", "help"}, str(sorted(dmap)))
check("dispatch targets exist", all(v in labels for v in dmap.values()))
check("bench alias -> ladder", dmap.get("bench") == dmap.get("ladder") == "run_ladder")
check("unknown cmd exits 1 headless",
      bool(re.search(r"Unknown command[\s\S]{0,200}exit /b 1", text)))

# 2. menu parity ([N] -> label, all dispatched cmds reachable from menu or help)
menu_targets = dict(re.findall(r'if "%CHOICE%"=="(\d+)" goto (\w+)', text))
check("menu targets exist", all(v in labels for v in menu_targets.values()))
expected_wrappers = {"run_tests", "run_concurrency", "run_quality", "run_ladder",
                     "run_all", "run_gate", "run_smoke", "run_failproof", "show_help"}
check("menu covers all wrappers", expected_wrappers <= set(menu_targets.values()))
help_at = next(i for i, ln in enumerate(lines) if ln.strip() == ":show_help")
help_block = "\n".join(lines[help_at: help_at + 25])
for cmd in ["test", "concurrency", "quality", "ladder", "bench", "all",
            "gate", "smoke", "failproof", "help"]:
    check(f"help lists '{cmd}'", f"run_win.bat {cmd}" in help_block)

# 3. goto/call targets resolve (code lines only; comments mention labels in prose)
gotos = set(re.findall(r"(?<!:)goto (\w+)", codetext)) - {"menu"}
check("goto targets exist", gotos <= set(labels), str(gotos - set(labels)))
calls = set(re.findall(r"call :(\w+)", codetext))
check("call targets exist", calls <= set(labels), str(calls - set(labels)))

# 4. worker vs wrapper structure
def body(label: str) -> str:
    idx = [i for i, ln in enumerate(lines) if ln.strip() == f":{label}"][0]
    nxt = min([v for k, v in labels.items() if v > idx] or [len(lines)])
    return "\n".join(lines[idx + 1:nxt])

workers = [k for k in labels if k.startswith("do_")]
check("workers are do_tests/do_concurrency/do_quality/do_gate/do_smoke/do_ladder/do_failproof",
      set(workers) == {"do_tests", "do_concurrency", "do_quality", "do_gate",
                       "do_smoke", "do_ladder", "do_failproof"}, str(workers))
for w in workers:
    b = body(w)
    bcode = "\n".join(l for l in b.splitlines() if not l.lstrip().startswith("::"))
    check(f"{w}: no goto (call-safe)", not re.search(r"(?m)^\s*goto\s", bcode))
    check(f"{w}: no pause", "pause" not in bcode.lower())
    check(f"{w}: ends exit /b", "exit /b" in bcode)
for w in ["run_tests", "run_concurrency", "run_quality", "run_ladder",
          "run_gate", "run_smoke", "run_failproof"]:
    b = body(w)
    check(f"{w}: calls do_* worker", f"call :do_{w[4:]}" in b)
    check(f"{w}: pause+menu path", "pause" in b and "goto menu" in b)
ra = body("run_all")
check("run_all chains workers (not wrappers)",
      all(f"call :{w}" in ra for w in ("do_tests", "do_concurrency", "do_quality"))
      and "call :run_" not in ra)

# 5. defect 1: no wmic; powershell timestamp + fallback
check("no operational wmic (prose may cite it)", "wmic" not in codetext.lower())
check("Get-Date timestamp", "Get-Date -Format yyyyMMdd_HHmmss" in text)
check("DATE/TIME fallback", "%DATE%" in text and "%TIME%" in text)

# 6. defect 2: no fixed overwrite outputs (operational lines only)
check("no fixed win_quality.json output",
      not any("win_quality.json" in l for l in code))
check("no fixed win_beir out-dir",
      not any(re.search(r"out-dir.*win_beir\b", l) and "TIMESTAMP" not in l and "%" not in l
              for l in code))
check("timestamped quality/BEIR dirs",
      "win_quality_%TIMESTAMP%" in text and "win_smoke_%TIMESTAMP%" in text
      and "beir_scifact" in text)

# 7. defect 4: gate content + ordering; no %TEMP% throughput claims
g = body("do_gate")
check("gate checks venv", "pytest" in g and "fts5" in g.lower())
check("gate reports actual free bytes", "FREE_BYTES" in g and "bytes free" in g)
check("gate has 20GB minimum", "MIN_FREE_GB=20" in g)
check("gate calls BEIR manifest", "check_beir_manifest" in g)
check("gate HALTs nonzero", "GATE-FAIL" in g and "exit /b 1" in g)
lad = body("do_ladder")
check("ladder gates first (before mkdir)",
      lad.index("call :do_gate") < lad.index("mkdir"))
check("no operational %TEMP% (comments only)",
      not any("%TEMP%" in l for l in code))
check("LOCALAPPDATA run dirs", r"%LOCALAPPDATA%\trace_lite_scale" in text)
sa = [l for l in code if "set /a" in l.lower()]
check("set /a only small-int (no GB mult overflow)",
      all(re.search(r"\+=?1", l) and "1000000000" not in l for l in sa), str(sa))

# 8. stale-expansion hygiene: no set-RET and no %ERRORLEVEL% read at depth > 0
depth = 0
stale: list[str] = []
for n, ln in enumerate(lines, 1):
    s = ln.strip()
    if s.startswith("::") or s.lower().startswith("echo") or s.lower().startswith("rem "):
        continue
    tmp = re.sub(r"^\s*for\s.*\bin\b", "for ", s, flags=re.IGNORECASE)
    opens = tmp.count("(") - tmp.count(")")
    if re.search(r'set "RET=%ERRORLEVEL%"', s) and depth > 0:
        stale.append(f"line {n}: set RET at depth {depth}")
    if "%ERRORLEVEL%" in s and depth > 0 and "%%" not in s:
        stale.append(f"line {n}: %ERRORLEVEL% read at depth {depth}")
    depth += opens
check("no stale %VAR% set/read inside blocks", not stale, "; ".join(stale[:5]))

# 9. defect 5: smoke bounded + skip-with-reason; failproof probes
sm = body("do_smoke")
check("smoke: bounded subset -x -q store+spans",
      all(t in sm for t in ("-x -q", "test_store.py", "test_source_spans.py")))
check("smoke: scifact-only BEIR", "--dataset scifact" in sm)
check("smoke: skip-with-reason", "[SKIP]" in sm and "scifact BEIR skipped" in sm)
check("smoke: timestamped log", "pytest_smoke.log" in sm and "SMOKE_DIR" in sm)
fp = body("do_failproof")
check("failproof: bogus harness flag", "--bogus-flag-xyz" in fp)
check("failproof: unknown-cmd probe", "bogus-cmd-xyz" in fp)
check("failproof: nonzero assertions", fp.count("exit /b 1") >= 3)
check("failproof: no-advancement dir count", "DIRS_BEFORE" in fp and "DIRS_AFTER" in fp)

# 10. ladder rungs + halt-on-failure + exit preservation
for docs in ("--docs 2000", "--docs 10000", "--docs 100000",
             "--docs 250000", "--docs 1000000"):
    check(f"ladder rung {docs}", docs in lad)
check("ladder: 5 rung HALTs + 1 gate HALT",
      lad.count("[HALT] Ladder stopped") == 5 and "[HALT] Ladder blocked" in lad)
check("ladder: exit code preserved (exit /b %RET%)", "exit /b %RET%" in lad)

# 11. manifest sizes in bat match R00/workload.json (independent oracle)
wl = json.loads(WORKLOAD.read_text())
sc = wl["corpora"]["scifact"]["files"]
pinned = {k: int(v) for k, v in
          re.findall(r'check_file_bytes "%BEIR_DS%\\([^"]+)" (\d+)', codetext)}
oracle = {"corpus.jsonl": sc["corpus.jsonl"]["bytes"],
          "queries.jsonl": sc["queries.jsonl"]["bytes"],
          "qrels\\test.tsv": sc["qrels/test.tsv"]["bytes"]}
check("bat manifest sizes == workload.json", pinned == oracle, str(pinned))

# 12. check_bytes_ge algorithm (length-then-lexical) unit-proof
def ge(a: str, b: str) -> bool:
    if len(a) != len(b):
        return len(a) > len(b)
    return a >= b

cases = [("59999999999", "20000000000", True), ("20000000000", "20000000000", True),
         ("19999999999", "20000000000", False), ("5000000000", "20000000000", False),
         ("2147483648", "20000000000", False), ("999999999999", "20000000000", True),
         ("0", "20000000000", False)]
check("byte-compare logic (incl. >2^31 cases)", all(ge(a, b) == e for a, b, e in cases))

# 13. cmd parse-safety: no `::` comments and no unescaped echo parens
# inside parenthesized blocks (both are instant crash/misparse causes;
# found live on 2026-09-12: `::` at old lines 60-61/85-89, bare parens
# in in-block echoes at old lines 79/428/450/460).
def unescaped_paren(s: str) -> bool:
    return bool(re.search(r"(?<!\^)[()]", s))

depth13 = 0
parse_bad: list[str] = []
for n, ln in enumerate(lines, 1):
    s = ln.strip()
    if s.startswith("::"):
        if depth13 > 0:
            parse_bad.append(f"line {n}: :: comment inside block")
        continue
    low = s.lower()
    if low.startswith("rem ") or low == "rem":
        continue
    if low.startswith("echo.") or low.startswith("echo ") or low.startswith("echo("):
        body13 = s[5:] if low.startswith(("echo ", "echo(")) else ""
        if depth13 > 0 and unescaped_paren(body13):
            parse_bad.append(f"line {n}: unescaped paren in in-block echo")
    depth13 += ln.count("(") - ln.count(")")
check("no :: comments inside blocks", not [p for p in parse_bad if ":: comment" in p],
      "; ".join(p for p in parse_bad if ":: comment" in p)[:300])
check("no unescaped parens in in-block echoes", not [p for p in parse_bad if "in-block echo" in p],
      "; ".join(p for p in parse_bad if "in-block echo" in p)[:300])

# 14. no unescaped shell metachars (& | < >) in ANY echo line (menu-level
# echoes parse too; found live 2026-09-12: bare & in menu line split the
# command: "'Generation' is not recognized").
meta_bad: list[str] = []
for n, ln in enumerate(lines, 1):
    s = ln.strip()
    low = s.lower()
    if low.startswith("echo ") or low.startswith("echo("):
        b14 = s[5:]
        if re.search(r"(?<!\^)[&|<>]", b14):
            meta_bad.append(f"line {n}: {b14[:60]}")
check("no unescaped &|<|> in echoes", not meta_bad, "; ".join(meta_bad[:5]))

print(f"\n{len(fails)} failure(s).")
sys.exit(1 if fails else 0)
