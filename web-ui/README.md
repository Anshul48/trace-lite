# trace-lite Web UI Dashboard

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](../LICENSE)

An interactive, 3D visualizer and dashboard for **trace-lite**, built with **React**, **TypeScript**, **Vite**, and **Three.js**.

---

## 🌟 Features

- **3D Forest Topology Visualizer**: Render RAPTOR summary trees, cluster nodes, and atom activation graphs in real-time.
- **Knowledge Base Inspector**: Inspect stored source artifacts, atom breakdowns, and energy decay stats.
- **Interactive Query Playground**: Test hybrid, tree, and flat retrieval modes with visual traversal path traces.
- **Project Context Manager**: Monitor project statistics, active nodes, and token usage estimates.

---

## ⚡ Development & Build

### Development Server

1. Navigate to the `web-ui/` directory:
   ```bash
   cd web-ui
   ```
2. Install Node.js dependencies:
   ```bash
   npm install
   ```
3. Start the Vite development server:
   ```bash
   npm run dev
   ```
4. Open your browser at `http://localhost:5173`.

### Production Build

To compile static assets for integration with `trace-lite ui`:

```bash
npm run build
```

The compiled bundle is output to `dist/`, which is copied into `src/trace_lite/ui/static/` during Python package builds.

---

## 📄 License

Licensed under the **Apache License, Version 2.0**. See [`LICENSE`](../LICENSE) for details.
