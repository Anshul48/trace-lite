# trace-lite Web UI Dashboard

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](../LICENSE)

The web workspace is a React, TypeScript, Vite, and Three.js client for the
trace-lite HTTP API. It is an operational dashboard as well as a visualization:
it makes source captures, organization, provider state, and index trust
visible.

## What the workspace shows

- **Workspace overview**: source, tree, node, vector, pending, and recovery
  counts, plus whether the active derived index is trusted.
- **Sources**: paste, upload, or synchronize source artifacts. Ingestion is
  durable immediately and is shown as queued until organization succeeds.
- **Organize**: run the provider-preflighted staged build. Routing, tree names,
  LLM summaries, embeddings, validation, and activation happen as one safe
  operation.
- **Forest and vector views**: inspect derived tree topology and vector state
  without treating an untrusted build as healthy.
- **Query playground**: choose hybrid, tree, or flat retrieval. Normal queries
  are blocked while captures are pending or the index is untrusted; callers
  that intentionally accept an incomplete view can send force=true to the API.
  Force queries use only the last verified flat index, exclude pending captures,
  make no LLM call, and show the returned warning.
- **Settings**: Save & Verify a provider. The server stores API keys in Windows
  Credential Manager, reads them back through the runtime resolver, verifies a
  minimal completion, and activates the provider only after all checks pass.
  Settings also controls **Preload embedding model on startup** and shows the
  local model's loading, ready, or failed state. Enabling it while the UI is
  open starts warm-up immediately; disabling it does not unload a resident
  model.

The UI never performs implicit query-time organization. A failed organize or
rebuild leaves the previous verified build and all source captures available.

## Run the server

Start the Python server from the repository root:

~~~bash
uv run tl ui
~~~

By default the dashboard is served on the local loopback interface. The API
also exposes:

- GET /api/status and GET /api/index/health for trust and validation state.
- POST /api/sources/text, POST /api/sources/upload, and POST /api/ingest for
  durable source capture.
- POST /api/organize for normal pending organization.
- POST /api/consolidate or POST /api/reindex for explicit derived rebuilds.
- POST /api/query with query_text, top_k, mode, and optional force.
- GET/POST /api/config for Save & Verify and POST /api/config/test for testing
  the already-saved runtime only.
- GET /api/models/status for embedding warm-up state and POST
  /api/config/auto-load with `{ "enabled": true|false }` to change the global
  preference.

Organization, consolidation, and reindex requests are serialized by the
server. A duplicate organize click waits for the active build and then returns
the normal idempotent result. Build errors include an actionable stage and
stable error code; provider configuration errors are 422, temporary provider
failures are 503, and unexpected staged failures are not disguised as 409s.

The UI process starts embedding warm-up in the background by default. The same
preference is available from the CLI with `tl config auto-load --on|--off`.
One-shot `tl ingest`, `tl status`, and `tl query` commands stay lazy; use `tl
models warmup` when an explicit one-process model check is needed.

## Development and build

From the web-ui directory:

~~~bash
npm install
npm run dev
~~~

Open the Vite development server at http://localhost:5173. The production
bundle is built with:

~~~bash
npm run build
~~~

The bundle is emitted to dist/ and copied into
src/trace_lite/ui/static/ when package assets are refreshed.

## Troubleshooting

- **Provider setup failed**: read the actionable error in Settings. Ensure
  Windows Credential Manager is available, then run Save & Verify again. A
  failed candidate does not replace the previous active provider.
- **Ready to organize**: source captures are safe in the Spine. Configure and
  verify a provider, then select Organize.
- **Index needs reindex**: the active derived state is legacy or untrusted.
  Rebuild from the immutable Spine with Rebuild structure or tl reindex --all.
- **Query blocked**: organize pending captures first, or deliberately use the
  API force flag to search only the last verified index. Force is unavailable
  when no verified build exists.
- **Warm-up failed**: inspect the failed state in Settings and the `tl ui`
  terminal. It reports concise organizer failures at INFO while redacting
  credentials and prompt/response bodies, including curl payloads.

## License

Licensed under the **Apache License, Version 2.0**. See LICENSE (../LICENSE).
