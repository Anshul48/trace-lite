# trace-lite Obsidian Plugin

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](../LICENSE)

An Obsidian client for the trace-lite server API. It keeps vault notes as
durable source artifacts, then searches the server's verified derived index
with source citations.

## Features

- **Vault source capture**: ingest markdown notes into an isolated trace-lite
  project. Auto-sync writes source artifacts immediately; it does not require
  an LLM and does not pretend that a note is organized yet.
- **Verified retrieval**: query hybrid, tree, or flat modes through
  POST /api/query. The server blocks normal queries while captures are pending
  or the active index is untrusted, so a provider failure cannot produce
  fallback summaries.
- **Inline evidence links**: result cards show source names, snippets, scores,
  and hierarchy paths, and open the matching note in the vault.
- **Project-aware connection**: point the plugin at the loopback trace-lite
  server and use its active project.
- **Background sync controls**: independently enable sync on note creation and
  modification.

The plugin is a client, not the index builder. Run organization from the web
workspace or CLI after configuring and verifying a provider. Organization
preflights the provider, stages routing/tree names/LLM summaries/vectors, and
activates the new build only after validation. A failed operation leaves the
previous verified index and queued notes intact.

## Installation and development

From the repository:

~~~bash
cd obsidian-plugin
npm install
npm run build
~~~

Copy main.js, manifest.json, and styles.css into:

~~~text
<your-vault>/.obsidian/plugins/trace-lite-obsidian/
~~~

Reload Obsidian and enable **trace-lite AI & RAG Assistant** under Community
Plugins. For local plugin development, keep the generated main.js in the
plugin directory and rebuild after source changes.

## Configure the server

In Obsidian Plugin Settings:

- **trace-lite API Base URL**: the running server, normally
  http://127.0.0.1:8420.
- **Auto-sync on Note Modification**: ingest changed markdown notes as durable
  source captures.
- **Auto-sync on Note Creation**: ingest new markdown notes as durable source
  captures.
- **Default Retrieval Mode**: hybrid, tree, or flat for normal server queries.
- **Evidence Result Count**: the top-k value sent to the server.

Start the server and configure the provider outside the plugin:

~~~bash
uv run tl ui
uv run tl config
~~~

Use the server's Settings page or tl config to Save & Verify the provider.
API keys are kept in Windows Credential Manager and are never returned by the
configuration endpoint.

## Daily workflow

1. Start the trace-lite server and select the intended project.
2. Let auto-sync or the manual sync action capture vault notes.
3. Watch the status badge or open the web workspace to see pending captures
   and index trust.
4. Run Organize in the web workspace, or run tl organize, after Save & Verify.
5. Search from Obsidian once the server reports a verified, current index.

If a normal search returns a conflict because the workspace is pending or
untrusted, organize or rebuild first. Force mode is an explicit API option for
operators who need an incomplete view; it searches only the last verified flat
index, excludes pending notes, makes no LLM call, and returns a warning. The
current plugin search intentionally uses the safe default and does not enable
force implicitly.

## Troubleshooting

- **Offline**: verify the API URL and that tl ui is listening on loopback.
- **Connected but query blocked**: inspect GET /api/status; run tl organize for
  pending captures or tl reindex --all for a legacy/untrusted index.
- **Provider errors**: use Save & Verify in the web Settings page or tl config.
  A failed credential read or live verification is reported and does not
  replace the previous active provider.
- **Missing note link**: result links use the document name supplied at ingest;
  keep names aligned with vault paths when a direct click-through is needed.

## License

Licensed under the **Apache License, Version 2.0**. See LICENSE (../LICENSE).
