# Contributing to trace-lite

Thank you for your interest in contributing to **trace-lite**! We welcome contributions from developers, researchers, and technical writers of all experience levels.

---

## 📜 Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

---

## ⚖️ License & Developer Certificate of Origin (DCO)

By contributing to this repository, you agree that your contributions will be licensed under the **Apache License, Version 2.0**.

---

## 🛠️ Local Development Setup

### Prerequisites

- **Python**: 3.11 or higher
- **Node.js**: v20 or higher (for Web UI or Obsidian plugin development)
- **uv** (recommended Python package manager) or standard `pip`

### Initial Setup

1. Fork and clone the repository:
   ```bash
   git clone https://github.com/your-username/trace-lite.git
   cd trace-lite
   ```

2. Set up virtual environment and install development dependencies:
   ```bash
   uv venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   uv pip install -e ".[dev,ui]"
   ```

---

## 🧪 Running Tests

Before submitting changes, run the pytest suite to ensure all tests pass:

```bash
uv run pytest
```

---

## 🎨 Code Style & Formatting

- **Python**: Follow PEP 8 guidelines. Use clear type hints (`typing` module) for all public functions and classes. Include Google-style docstrings for modules, classes, and public methods.
- **TypeScript / React**: Use standard ES module conventions, explicit interface definitions, and clean component structures.

---

## 🔀 Pull Request Process

1. **Create a Feature Branch**:
   ```bash
   git checkout -b feature/my-amazing-feature
   ```
2. **Commit Your Changes**: Keep commits focused and provide clear, descriptive commit messages.
3. **Run Test Verification**: Ensure all automated unit tests pass locally.
4. **Push & Open PR**: Push to your fork and submit a Pull Request targeting the `main` branch. Complete the PR template description.

---

## 📄 License

Licensed under the **Apache License, Version 2.0**. See [`LICENSE`](LICENSE) for details.
