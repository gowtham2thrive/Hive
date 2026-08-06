<br/>
<div align="center">
  <a href="#">
    <img src="assets/logo.svg" alt="Hive Logo" width="120" height="120" />
  </a>

  <h1 align="center">H I V E</h1>

  <p align="center">
    <strong>Discover, manage, download, and chat with GGUF models with unparalleled elegance.</strong>
    <br/>
    <i>A beautiful, robust desktop-class web application for Hugging Face.</i>
  </p>

  <p align="center">
    <a href="#"><img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-18181b?style=for-the-badge&logo=windows&logoColor=white&labelColor=09090b" alt="Platform" style="pointer-events: none;" /></a>
    <a href="#"><img src="https://img.shields.io/badge/Python-3.8+-18181b?style=for-the-badge&logo=python&logoColor=white&labelColor=09090b" alt="Python" style="pointer-events: none;" /></a>
    <a href="#"><img src="https://img.shields.io/badge/FastAPI-Framework-18181b?style=for-the-badge&logo=fastapi&logoColor=white&labelColor=09090b" alt="FastAPI" style="pointer-events: none;" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-18181b?style=for-the-badge&labelColor=09090b" alt="License" style="pointer-events: none;" /></a>
  </p>

  <br />
  <a href="#">
    <img src="assets/hero-illustration.svg" alt="Hive Hero Illustration" width="100%" />
  </a>
</div>

---

## <a href="#"><img src="assets/icon-features.svg" width="26" align="center"></a> Features

- <a href="#"><img src="assets/icon-discover.svg" width="20" align="center"></a> **Discover Workspace**: Quickly search Hugging Face for GGUF models directly within the application.
- <a href="#"><img src="assets/icon-downloads.svg" width="20" align="center"></a> **Downloads Workspace**: Manage your active downloads and view locally downloaded models in a beautiful grid layout.
- <a href="#"><img src="assets/icon-chat.svg" width="20" align="center"></a> **Chat Workspace**: Seamlessly converse with your locally downloaded GGUF models using a beautiful, built-in chat interface with Markdown support.
- <a href="#"><img src="assets/icon-brain.svg" width="20" align="center"></a> **Local Inference**: Run AI models completely locally with complete privacy, leveraging `llama-cpp-python`.
- <a href="#"><img src="assets/icon-robust.svg" width="20" align="center"></a> **Robust Downloading**: Reliable downloading mechanism that uses chunks and supports pausing, resuming, and safe cancellation.
- <a href="#"><img src="assets/icon-folder.svg" width="20" align="center"></a> **Native OS Integration**: Instantly open the containing folder of any downloaded model in your native file explorer (Windows).
- <a href="#"><img src="assets/icon-ui.svg" width="20" align="center"></a> **Beautiful Glassmorphism UI**: A dark-mode, minimalist interface designed for speed, aesthetics, and premium user experience.

---

## <a href="#"><img src="assets/icon-rocket.svg" width="26" align="center"></a> Getting Started

### Prerequisites

- **Python 3.8+** installed on your system.
- Git (optional, for cloning).

### Installation

1. **Clone the repository:**

   ```cmd
   git clone https://github.com/gowtham2thrive/Hive.git
   cd hive
   ```

2. **Run the start script (Windows):**

   ```cmd
   start.bat
   ```

   *This script will automatically:*
   - Set up a Python virtual environment (`venv`).
   - Install the required dependencies (`FastAPI`, `llama-cpp-python`, `aiosqlite`, `huggingface_hub`, etc.).
   - Start the backend server on port `8080`.
   - Open the app in your default web browser.

---

## <a href="#"><img src="assets/icon-tech.svg" width="26" align="center"></a> Technologies Used

The application is built using a modern yet lightweight tech stack:

- **Frontend**: Vanilla HTML, CSS, JavaScript (No frameworks!) with [Feather Icons](https://feathericons.com/).
- **Backend**: [Python](https://www.python.org/), [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [llama-cpp-python](https://github.com/abetlen/llama-cpp-python), [SQLite](https://www.sqlite.org/).

---

## <a href="#"><img src="assets/icon-chat.svg" width="26" align="center"></a> Chat Feature

Hive Chat lets you have conversations with your locally downloaded GGUF models — entirely on your machine, with zero data leaving your device.

### How It Works

1. **Navigate** to the Chat tab from the sidebar
2. **Open Settings** (<a href="#"><img src="assets/icon-settings.svg" width="18" align="center"></a>) → Select your `.gguf` model from the dropdown → Click **Load Model**
3. **Start chatting** — responses stream in real-time with full markdown rendering

### Architecture

| Component | Technology | Purpose |
| --- | --- | --- |
| **Inference Engine** | `llama-cpp-python` | Loads and runs GGUF models locally with CPU auto-tuning |
| **Streaming** | WebSocket | Real-time token-by-token response streaming with micro-batching |
| **Persistence** | SQLite (async, WAL) | Stores conversations and messages across sessions |
| **Markdown** | Custom renderer | Syntax highlighting, code blocks, tables, copy-to-clipboard |
| **API** | FastAPI REST + WS | Conversation CRUD, model management, streaming chat |

### Key Capabilities

- **Real-time streaming** — Tokens stream as they're generated with adaptive render intervals
- **Conversation management** — Create, rename, search, and delete conversations
- **Model management** — Load/unload models, browse for `.gguf` files via native OS file picker
- **Markdown rendering** — Code blocks with syntax highlighting (Python, JS, CSS, etc.), tables, lists, blockquotes
- **Context truncation** — Automatically trims old messages to fit the model's context window
- **Cancel generation** — Stop responses mid-stream with visual feedback
- **Creativity slider** — Adjustable temperature from Precise (0.0) to Creative (2.0) with dynamic labels
- **Auto-scroll** — Smart scrolling that pauses when you scroll up to read
- **Inactivity watchdog** — Auto-resets if no tokens arrive for 20 seconds

---

## <a href="#"><img src="assets/icon-contribute.svg" width="26" align="center"></a> Contributing

<div align="center">
  <!-- CONTRIBUTORS_START -->
<a href="https://github.com/gowtham2thrive"><img src="assets/contributor-gowtham2thrive.svg" width="390" /></a><a href="https://github.com/preethi-beri"><img src="assets/contributor-preethi-beri.svg" width="390" /></a><a href="https://github.com/YallaDurgaPrasad65"><img src="assets/contributor-YallaDurgaPrasad65.svg" width="390" /></a>

<!-- CONTRIBUTORS_END -->
</div>
<br/>

## <a href="#"><img src="assets/icon-license.svg" width="26" align="center"></a> License

This project is licensed under the MIT License. See the `LICENSE` file for more details.

---
