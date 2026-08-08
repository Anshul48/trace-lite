# trace-lite Obsidian Plugin

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](../LICENSE)

An official [Obsidian](https://obsidian.md/) plugin providing **hierarchical RAG knowledge search and assistant features** powered by `trace-lite`.

---

## 🌟 Features

- **Vault Search & Indexing**: Ingest vault notes directly into isolated `trace-lite` database stores.
- **Hierarchical RAPTOR Retrieval**: Query notes with top-down summary tree traversal ($O(\log N)$ scaling).
- **Interactive Chat Sidebar**: Chat with your knowledge base inside Obsidian with inline source citation links.
- **Project Switching**: Switch between multiple `trace-lite` project databases seamlessly within Obsidian settings.

---

## ⚡ Installation & Setup

### Development Build

1. Clone or navigate to the plugin folder:
   ```bash
   cd obsidian-plugin
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Build the plugin:
   ```bash
   npm run build
   ```
4. Copy `main.js`, `manifest.json`, and `styles.css` into your vault's plugin directory:
   ```text
   <your-vault>/.obsidian/plugins/trace-lite-obsidian/
   ```
5. Reload Obsidian and enable **trace-lite AI & RAG Assistant** in Community Plugins settings.

---

## ⚙️ Configuration

In Obsidian Plugin Settings:
- **Server API URL**: URL of the running `trace-lite` server (e.g., `http://127.0.0.1:8000`).
- **Active Project**: Select or create the active `trace-lite` project store.

---

## 📄 License

Licensed under the **Apache License, Version 2.0**. See [`LICENSE`](../LICENSE) for details.
