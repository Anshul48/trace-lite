import json, os, base64

with open('ast_dump.json', 'r', encoding='utf-8') as f:
    ast_data = json.load(f)
with open('tree.txt', 'r', encoding='utf-8') as f:
    tree = f.read()

rep = []
rep.append('# trace-lite Comprehensive Scan Report\n')
rep.append('## 1. Introduction and Relationship to trace')
rep.append('Based on the README and project structure, trace-lite is a lightweight, single-node, high-performance filing cabinet and local memory substrate.')
rep.append('It acts as a lightweight edge memory engine, providing a deterministic (zero LLM dependencies for core indexing), pure SQLite foundation. It uses an append-only provenance model with content-addressed atoms and fast hybrid lexical + sparse activation retrieval. It is heavily focused on being a local, fast substrate compared to the presumably larger, distributed, or LLM-heavy full trace version.\n')
rep.append('## 2. Git State\n### Branches')
rep.append('`	ext\n  archive/v1-legacy-scaffold\n+ feat/l1-candidate-active-lifecycle\n+ feat/s2-tl2-bounded-graph\n  feature/graph-hipporag\n  feature/tri-channel-parity\n* main\n+ repair/v1b-r5-depth-overflow-20260825\n+ verify/v1b-tl-lifecycle-20260819\n  remotes/origin/archive/v1-legacy-scaffold\n  remotes/origin/feature/graph-hipporag\n  remotes/origin/feature/tri-channel-parity\n  remotes/origin/main\n`')
rep.append('### Recent Log (20 commits)')
rep.append('`	ext\n0df044b docs(query-native): update coordinator guide\n8de262a docs(coordinator): update launch prompt for query-native memory program\n670d839 docs(query-native): format packet contract R09-qualification\nc5565b1 docs(query-native): format packet contracts R00-R08 and execution specifications\ncd699b7 docs(query-native): record project authorization and evidence assumptions\n85322b4 fix(audit): remediate ghost-facet beam saturation, db-wide leakage, and sync re-warm bottleneck\nc8e6b70 feat(beir): BM25-led quality ranking, stem-aware gates, frozen adapter\n3bcdfe4 feat(quality): nDCG/abstention harness, corroboration gate for dense-only noise\n2d355d1 feat(scale): 1M qualification reports, chunked centroid refresh, state\nb9f8c1a perf(scale-1m): two-step FTS fetch, streaming warm and corpus\n526e866 perf(scale-1m): float16 dense matrix, bulk facet assignment\nd2aff0c perf(scale): index-ordered facet fetch, shared dense matrix, unranked FTS pools\n22ba79a fix(critic-loop): ghost facets, mode dispatch, watcher deletions, CLI mkdir\nfc39aea fix(audit): F1 FTS delete, F2 wire-compat, F3 beam, F4 watcher, F5 CLI, F6 schema\n092f11a chore: finalize overnight tranche artifacts\na4ccc84 docs(state): record P07 candidate 0d9f0c0\n0d9f0c0 feat(e2e,bench): P07 integration suite + 10k benchmark qualification\ne21e54f feat(cordis,obsidian,api): P05 DSH plugin suite + P06 vault sync and REST API\n69ff4ba feat(filing,router): P03 Hearst faceted engine + P04 sub-50ms 3-tier router\nc9e9bff feat(store): P02 canonical atom storage, events, FTS5, WAL governor\n`\n')
rep.append('## 3. Dependencies')
rep.append('From pyproject.toml:')
rep.append('`	oml\n[project]\nname = "trace-lite"\ndependencies = [\n    "pydantic>=2.7.0",\n    "rich>=13.7.0",\n    "typer>=0.12.0",\n    "numpy>=1.24.0",\n    "fastapi>=0.100.0",\n    "uvicorn[standard]>=0.20.0",\n]\n[project.optional-dependencies]\ndev = [\n    "pytest>=8.0.0",\n    "pytest-cov>=5.0.0",\n    "ruff>=0.4.0",\n    "mypy>=1.10.0",\n]\n`\n')
rep.append('## 4. Full Directory Tree\n`	ext\n' + tree + '\n`\n')
rep.append('## 5. Python Files, Classes, and Functions\n')

for file_info in ast_data:
    rep.append('### File: ' + str(file_info.get('path')) + '')
    doc = file_info.get('module_doc', '')
    if doc: rep.append('**Purpose**: ' + doc.splitlines()[0])
    classes = file_info.get('classes', [])
    if classes:
        rep.append('**Classes**:')
        for c in classes:
            c_doc = c.get('doc') or ''
            c_doc_trunc = c_doc.splitlines()[0] if c_doc else ''
            methods = ', '.join(c['methods']) if c['methods'] else 'None'
            rep.append('- ' + c['name'] + ': ' + c_doc_trunc)
            rep.append('  - Methods: ' + methods)
    functions = file_info.get('functions', [])
    if functions:
        rep.append('**Functions**:')
        for f in functions:
            f_doc = f.get('doc') or ''
            f_doc_trunc = f_doc.splitlines()[0] if f_doc else ''
            rep.append('- ' + f['name'] + ': ' + f_doc_trunc)
    imports = file_info.get('imports', [])
    if imports:
        rep.append('**Imports**: ' + ', '.join(imports[:20]) + ('...' if len(imports) > 20 else ''))
    rep.append('')

adir = r'C:\Users\anshu\.gemini\antigravity-cli\brain\e8e09973-7aa3-4b0e-a9ec-2873c9bd4953\scratch'
os.makedirs(adir, exist_ok=True)
rp = os.path.join(adir, 'trace_lite_report.md')
with open(rp, 'w', encoding='utf-8') as f:
    f.write('\n'.join(rep))
print('Report saved to ' + rp)
