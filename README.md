# trace-lite

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

trace-lite is a multi-project knowledge database with an immutable source
store, LLM-generated summary trees, vector retrieval, and CLI, SDK, web, and
Obsidian interfaces.

The important operational guarantee is **fail-closed indexing**:

- Capturing source material is durable and does not require an LLM.
- Organizing or rebuilding derived structure requires a verified provider and a
  real completion request.
- A failed build is discarded before activation. The last verified index and
  all source captures remain intact.
- A non-leaf tree node is valid only when its summary came from a successful
  LLM response (including a recorded retry). There is no deterministic or
  extractive summary fallback.

This makes a provider outage visible instead of silently turning an
authentication failure into plausible-looking, but untrusted, knowledge.

## Architecture

~~~mermaid
graph TD
    Client["CLI / SDK / Web UI / Obsidian"] --> DB["TraceLite"]
    DB --> Spine["Spine: SQLite sources, artifacts, atoms"]
    Spine --> Queue["Durable source-level pending queue"]
    DB --> Preflight["Provider preflight + live completion"]
    Preflight --> Stage["Staged routing, naming, summaries, vectors"]
    Stage --> Manifest["Verified active build manifest"]
    Manifest --> Query["Default query / force query"]
~~~

The **Spine** is the durable source of truth. Every ingest writes the source
artifact and atoms first, then records them in a source-level pending queue.
The **Cortex** (forest, summaries, and vectors) is rebuildable derived state.
An active Cortex build is queryable by default only when its manifest validates,
it contains no legacy fallback or passthrough summaries, and there are no
pending or orphaned captures.

Each non-leaf node has one canonical LLM-generated summary: a dense,
self-contained factual trace. That same `summary_text` is validated, stored,
embedded, displayed, and supplied to the next summary level; there is no
second retrieval or stopword-filtered summary representation.

Queries execute through an adaptive **Quad-Channel Retrieval Engine**
combining flat vector search, RAPTOR summary hierarchy traversal, SQLite
FTS5 BM25 lexical matching, and HippoRAG 2 Personalized PageRank (PPR) graph
activation over deterministic sequential, hierarchical, and co-occurrence edges.
See [`docs/dev/ARCHITECTURE.md`](docs/dev/ARCHITECTURE.md) for full architectural
specifications, data models, and empirical benchmark retrospectives.

## Installation and setup

### Install the CLI

For a global installation:

~~~bash
uv tool install ".[ui]"

 uv tool install --force --editable ".[ui]"
# or: pipx install ".[ui]"
~~~

For repository development:

~~~bash
git clone https://github.com/anshumani/trace-lite.git
cd trace-lite
uv sync --all-extras
~~~

The keyring dependency is part of the core install. On Windows, setup-entered
API keys are stored in Windows Credential Manager through keyring; the runtime
does not depend on a process environment variable, an in-memory value from an
earlier process, or a plaintext key in config.json.

### Save and verify a provider

Use the CLI wizard or the loopback-only web UI Settings page:

~~~bash
tl config                    # interactive Save & Verify wizard
tl config list --json         # metadata and credential state; never the key
tl status --json
~~~

The wizard and UI action are **Save & Verify**, not an optimistic
save. The candidate credential is written to Credential Manager, read back by a
fresh runtime resolver, and used for a minimal live completion. Only then is
the provider made active. If storage, re-read, or verification fails, the
previous active provider is restored and the command returns an error.

For Ollama, verification also checks that the configured model is available.
For an API-key provider, verification performs an authenticated completion. A
provider must be saved and verified before tl organize, tl consolidate, or
tl reindex --all can build derived state.

The terminal selectors use the keyboard: Up/Down moves, Enter chooses, and
Escape or Q cancels/exits through the visible Cancel/Quit choice. Long model
catalogs scroll in place and include every eligible refreshed model from the
provider catalog, plus an Enter custom model option. Direct-provider model
names remain provider-qualified, for example
`openai/gpt-4o-mini`, `anthropic/claude-sonnet-4`, or
`cohere/command-a`; OpenRouter models use `openrouter/<raw-id>`.

Verification reports a safe category rather than LiteLLM's raw diagnostics:
unsupported model, rejected credential, unavailable endpoint, rate-limited or
temporarily unavailable provider, or empty response. A failed category keeps
the candidate inactive and restores the previous active provider and
credential.

If an older installation has plaintext provider keys in its metadata, inspect
and explicitly migrate them:

~~~bash
tl config migrate-credentials --yes
~~~

Migration is never implicit. After migration, confirm that tl config list
--json contains provider metadata and has_api_key/credential state, not an
API-key value.

### Refresh the model snapshot

The generated provider model lists can be refreshed from OpenRouter with:

~~~bash
python get_models.py
# Or write the snapshot to another registry path:
python get_models.py --output ./src/trace_lite/models.json
~~~

The updater requires a live OpenRouter API key and asks for it through a
masked interactive prompt before making the request. It does not accept a key
through `--key`, `-k`, or an environment variable, and never stores, logs, or
changes the active provider configuration. A failed fetch or validation keeps
the last-known-good `models.json` snapshot unchanged.

## Source-to-index lifecycle

Capture first, then organize explicitly:

~~~bash
# Capture is durable and source-only; it does not call the LLM.
tl ingest --file ./notes/design.md --name "Design notes"
tl ingest "A short source note" --name "Scratch"

# Inspect pending work and trust/credential state.
tl status --json

# Preflight the provider, stage routing/tree names/summaries/vectors, validate,
# and atomically activate the candidate build.
tl organize

# Query the last verified active index.
tl query "Which design decisions are documented?" --mode hybrid
~~~

tl organize is the normal incremental operation. tl consolidate is the
explicit advanced rebuild entry point (optionally for one tree), and
tl reindex --all rebuilds all derived state from the immutable Spine. All
three paths run provider preflight and use the same staged activation rules.

## Embedding model warm-up

The global provider configuration stores `auto_load_models: true` by default;
legacy configuration files missing the key also behave as enabled. Long-lived
`tl ui`, `tl chat`, and `tl watch` processes start a non-blocking warm-up of the
local sentence-transformer embedding model. This never contacts or warms the
configured LLM. Disable or re-enable it with:

~~~bash
tl config auto-load --off
tl config auto-load --on --json
tl models warmup --json
~~~

`tl ingest`, `tl status`, and `tl query` intentionally remain lazy because
they are short-lived or read-only commands. `tl models warmup` verifies and
caches the local model for that process; it cannot keep the model resident
after the process exits.

If a build fails, no default tree name, routing assignment, summary node,
vector directory, or pending-queue entry is activated or lost. New captures
that arrive during a build remain durable and pending for the next successful
organization.

## Query safety and force mode

Normal queries refuse to search when source captures are pending, legacy atoms
need recovery, or the active derived index is untrusted. The error explains
whether to run tl organize/tl reindex --all or repair provider setup.
Queries never organize implicitly and never make a provider call just to hide
an indexing problem.

When you explicitly accept an incomplete view, use force mode:

~~~bash
tl query "What is already indexed?" --force
~~~

Force mode searches only the **last verified active index**, excludes all
pending captures, uses flat vector search, makes no LLM call, and returns an
exclusion warning. It is unavailable when there is no verified index. In the
HTTP API the equivalent request is:

~~~http
POST /api/query
Content-Type: application/json

{"query_text":"What is already indexed?", "top_k":5, "mode":"flat", "force":true}
~~~

The response includes forced, warnings, and current needs_organization state
so clients can make the exclusion visible.

## CLI reference

All commands accept --data-dir <path> to use an isolated project store.

| Command | Purpose |
| :--- | :--- |
| tl project | Manage isolated project stores. |
| tl ingest [TEXT] --file PATH | Write source artifacts/atoms and queue them for organization. |
| tl organize | Preflight, route, name, summarize, embed, validate, and activate a staged build. |
| tl consolidate [--tree-id ID] | Explicit advanced/full summary-tree rebuild. |
| tl reindex --all | Rebuild all derived state from the immutable Spine. |
| tl validate [--json] | Validate source coverage, provenance, topology, vectors, and the active manifest. |
| tl query TEXT [--mode hybrid\|tree\|flat] [--force] | Query verified state; force excludes pending captures and makes no LLM call. |
| tl status [--json] | Show pending work, index trust, validation, vectors, and credential state. |
| tl config | Interactive masked Save & Verify provider setup. |
| tl config list [--json] | Show provider metadata and credential presence, never plaintext keys. |
| tl config auto-load --on\|--off [--json] | Persist the background embedding-model warm-up preference. |
| tl config migrate-credentials --yes | Explicitly move legacy plaintext keys to Credential Manager. |
| tl models warmup [--json] | Explicitly load/verify the local embedding model for one process. |
| tl chat | Interactive chat and source-capture REPL. /ingest queues source; /organize builds structure. |
| tl ui | Launch the web workspace and status dashboard. |

The canonical command groups are `project`, `config`, `query`, `trees`, and `ui`.

## Python SDK

The SDK exposes the same source-first and trust rules as the CLI:

~~~python
from trace_lite import TraceLite

db = TraceLite("./knowledge")
db.ingest("SQLite is a transactional database.", document_name="Notes")

# Requires a saved and verified provider. Failure leaves the source queued.
build = db.organize()
print(build.summaries_generated)

try:
    result = db.query("What database is described?", top_k=5, mode="hybrid")
except RuntimeError as exc:
    # QueryBlockedError is exported from trace_lite.db; handle it explicitly
    # in applications that need to distinguish pending/untrusted state.
    print(f"Index is not ready: {exc}")
else:
    for item in result.items:
        source = item.source_artifact.document_name if item.source_artifact else "Unknown"
        print(f"[{item.score:.3f}] {source}: {item.atom.content}")

# Deliberately search only the last verified build, with no LLM call.
stale_view = db.query("What is already indexed?", mode="flat", force=True)
print(stale_view.warnings)
~~~

db.status() exposes index_trusted, credential_state, needs_organization,
needs_recovery, active_build_id, validation errors, and pending counts.
db.validate_index() provides the detailed health report.

## HTTP API

Run tl ui (or the server entry point) and use the JSON API from a local web
client or the Obsidian plugin. The key lifecycle endpoints are:

| Endpoint | Behavior |
| :--- | :--- |
| GET /api/status | Current counts, pending state, index trust, validation, and credential state. |
| POST /api/ingest / POST /api/sources/text | Durable source capture; returns queued: true. |
| POST /api/organize | Provider-preflighted staged organization and atomic activation. |
| POST /api/consolidate | Explicit tree/full rebuild. |
| POST /api/reindex | Full validated rebuild from immutable Spine sources. |
| POST /api/query | Default safe query; pass force: true for an explicitly excluded pending view. |
| GET /api/index/health | Detailed index validation and trust diagnostics. |
| GET /api/config / POST /api/config | Save & Verify provider metadata and credential. |
| GET /api/models/status | Embedding warm-up state and the persisted auto-load preference. |
| POST /api/config/auto-load | Set `{ "enabled": true|false }`; enabling starts a background warm-up in the open UI. |
| POST /api/config/test | Test the already-saved runtime configuration only. |

Configuration and query failures are returned as actionable HTTP errors rather
than silently producing a fallback result. `/api/organize`, `/api/consolidate`,
and `/api/reindex` share one server build lock. A second organize request waits
for the first and then returns the normal idempotent zero-work result. Provider
configuration/model failures use 422, unavailable or rate-limited providers
use 503, and staged validation/unexpected failures include stable `error_code`
and `stage` fields instead of a generic 409.

## Legacy and recovery behavior

Older databases may contain fallback or passthrough summaries, including the
historical a1-style index. Those files are preserved for inspection but are
marked index_trusted: false and are not healthy/queryable by default. A force
query also fails when no verified active build exists. After provider setup is
verified, run:

~~~bash
tl reindex --all
tl validate --json
~~~

No legacy data is deleted automatically. Derived state can be archived and
reset with tl reset --derived --yes; the immutable Spine should be treated as
the recovery source.

## Extensions

- **[Web UI Dashboard](web-ui/README.md)**: React/TypeScript workspace for
  source capture, organization status, index health, and retrieval.
- **[Obsidian Plugin](obsidian-plugin/README.md)**: Obsidian client for the
  server API, with project switching and source-capture workflows.

## Development and verification

~~~bash
uv sync --all-extras
uv run pytest
cd web-ui && npm install && npm run build
~~~

`tl ui` logs operational events at INFO by default. LiteLLM, HTTP transport,
and FastAPI internals are held at WARNING; build failures are concise by
default. Credentials and prompt/response bodies (including curl payloads) are
redacted before anything reaches the terminal.

## Contributing and license

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the **Apache License, Version 2.0**. See [LICENSE](LICENSE)
and [NOTICE](NOTICE).
