<br/>
<div align="center">
  <a href="#">
    <img src="assets/logo.svg" alt="Hive Logo" width="100" height="100" />
  </a>

  <h1 align="center">H I V E</h1>

  <p align="center">
    <strong>Discover, manage, and download GGUF models with unparalleled elegance.</strong>
    <br/>
    <i>A beautiful, robust desktop-class web application for Hugging Face.</i>
  </p>

  <p align="center">
    <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-18181b?style=for-the-badge&logo=windows&logoColor=white&labelColor=09090b" alt="Platform" />
    <img src="https://img.shields.io/badge/Python-3.8+-18181b?style=for-the-badge&logo=python&logoColor=white&labelColor=09090b" alt="Python" />
    <img src="https://img.shields.io/badge/FastAPI-Framework-18181b?style=for-the-badge&logo=fastapi&logoColor=white&labelColor=09090b" alt="FastAPI" />
    <img src="https://img.shields.io/badge/License-MIT-18181b?style=for-the-badge&labelColor=09090b" alt="License" />
  </p>

  <br />
  <img src="assets/hero-illustration.svg" alt="Hive Hero Illustration" width="100%" />
</div>

---

## ✨ Features

- 🔍 **Discover Workspace**: Quickly search Hugging Face for GGUF models directly within the application.
- 📦 **Downloads Workspace**: Manage your active downloads and view locally downloaded models in a beautiful grid layout.
- 🚀 **Robust Downloading**: Reliable downloading mechanism that uses chunks and supports pausing, resuming, and safe cancellation.
- 📂 **Native OS Integration**: Instantly open the containing folder of any downloaded model in your native file explorer (Windows).
- 🎨 **Beautiful Glassmorphism UI**: A dark-mode, minimalist interface designed for speed, aesthetics, and premium user experience.

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.8+** installed on your system.
- Git (optional, for cloning).

### Installation

1. **Clone the repository:**

   ```cmd
   git clone https://github.com/yourusername/hive.git
   cd hive
   ```

2. **Run the start script (Windows):**

   ```cmd
   start.bat
   ```

   *This script will automatically:*
   - Set up a Python virtual environment (`venv`).
   - Install the required dependencies (`FastAPI`, `Uvicorn`, `huggingface_hub`, `requests`).
   - Start the backend server on port `8080`.
   - Open the app in your default web browser.

---

## 🛠 Technologies Used

The application is built using a modern yet lightweight tech stack:

- **Frontend**: Vanilla HTML, CSS, JavaScript (No frameworks!) with [Feather Icons](https://feathericons.com/).
- **Backend**: [Python](https://www.python.org/), [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/).

---

## 📄 License

This project is licensed under the MIT License. See the `LICENSE` file for more details.
