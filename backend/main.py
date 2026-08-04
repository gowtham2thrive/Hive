import os
import re
import sys
import json
import asyncio
import logging
import platform
import threading
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from huggingface_hub import HfApi
from pydantic import BaseModel
from backend.downloader import download_model_file, get_active_downloads, cancel_download, pause_download, sync_local_files, delete_local_file, clear_local_completed_files, dismiss_download
from backend.chat_database import init_db, close_db
from backend.chat_routes import router as chat_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle for Hive."""
    # Startup
    await init_db()
    print("[Hive] Chat database initialized.")
    yield
    # Shutdown
    from backend.chat_model_manager import chat_model_manager
    chat_model_manager.unload_model()
    await close_db()
    print("[Hive] Chat model unloaded. Database closed.")


app = FastAPI(lifespan=lifespan)

# Allow CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

hf_api = HfApi()

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
DEFAULT_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load config from {CONFIG_FILE}: {e}. Using defaults.")
    return {"models_dir": DEFAULT_MODELS_DIR}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

config = load_config()
MODELS_DIR = config.get("models_dir", DEFAULT_MODELS_DIR)
_models_dir_lock = threading.Lock()

def get_models_dir() -> str:
    """Thread-safe read of MODELS_DIR."""
    with _models_dir_lock:
        return MODELS_DIR

def set_models_dir(new_dir: str):
    """Thread-safe write of MODELS_DIR."""
    global MODELS_DIR
    with _models_dir_lock:
        MODELS_DIR = new_dir

try:
    os.makedirs(MODELS_DIR, exist_ok=True)
except OSError as e:
    logger.error(f"Failed to create models directory '{MODELS_DIR}': {e}. Falling back to default.")
    MODELS_DIR = DEFAULT_MODELS_DIR
    os.makedirs(MODELS_DIR, exist_ok=True)
sync_local_files(MODELS_DIR)

# Regex for valid HuggingFace repo IDs: owner/model-name
_REPO_ID_PATTERN = re.compile(r'^[\w.-]+/[\w.-]+$')

class DownloadRequest(BaseModel):
    repo_id: str
    filename: str

class CancelRequest(BaseModel):
    filename: str

class PauseRequest(BaseModel):
    filename: str

@app.get("/api/search")
def search_models(q: str):
    if not q or not q.strip():
        return []
    try:
        # Search for models matching query. We prefer text-generation models.
        # It's best to look for GGUF tag or 'gguf' in the name.
        models = hf_api.list_models(search=q, limit=20, filter="gguf")
        result = []
        for m in models:
            result.append({
                "id": m.id,
                "author": m.author or (m.id.split("/")[0] if "/" in m.id else "Unknown"),
                "downloads": getattr(m, "downloads", 0),
                "likes": getattr(m, "likes", 0)
            })
        
        # If no GGUF specific models, fallback to general search
        if not result:
            models = hf_api.list_models(search=q, limit=20)
            for m in models:
                if 'gguf' in m.id.lower():
                    result.append({
                        "id": m.id,
                        "author": m.author or (m.id.split("/")[0] if "/" in m.id else "Unknown"),
                        "downloads": getattr(m, "downloads", 0),
                        "likes": getattr(m, "likes", 0)
                    })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/model/{repo_id:path}")
def get_model_details(repo_id: str):
    try:
        info = hf_api.model_info(repo_id)
        files = [f.rfilename for f in info.siblings if f.rfilename.endswith(".gguf")]
        return {
            "id": info.id,
            "files": files,
            "description": info.cardData if hasattr(info, "cardData") else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _validate_filename(filename: str):
    """Prevent path traversal attacks in filenames."""
    # Reject filenames with path separators or parent directory references
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filename or filename.startswith('.'):
        raise HTTPException(status_code=400, detail="Invalid filename")

def _validate_repo_id(repo_id: str):
    """Validate that repo_id looks like a valid HuggingFace model ID."""
    if not repo_id or not _REPO_ID_PATTERN.match(repo_id):
        raise HTTPException(status_code=400, detail="Invalid repo_id format. Expected 'owner/model-name'.")

@app.post("/api/download")
def start_download(req: DownloadRequest):
    _validate_filename(req.filename)
    _validate_repo_id(req.repo_id)
    # Check if already downloading or completed
    active = get_active_downloads()
    if req.filename in active and active[req.filename]["status"] in ["downloading", "starting", "completed"]:
        return {"message": "Download already in progress or completed."}
    
    # Capture current MODELS_DIR so in-flight downloads aren't affected by path changes
    target_dir = get_models_dir()
    
    # Start download in a background thread
    def download_task():
        try:
            download_model_file(req.repo_id, req.filename, target_dir)
        except Exception as e:
            logger.error(f"Download failed for {req.filename}: {e}")

    thread = threading.Thread(target=download_task)
    thread.daemon = True
    thread.start()
    
    return {"message": f"Started downloading {req.filename}"}

@app.post("/api/cancel")
def handle_cancel_download(req: CancelRequest):
    _validate_filename(req.filename)
    cancel_download(req.filename, get_models_dir())
    return {"message": f"Requested cancellation for {req.filename}"}

@app.post("/api/pause")
def handle_pause_download(req: PauseRequest):
    _validate_filename(req.filename)
    pause_download(req.filename)
    return {"message": f"Requested pause for {req.filename}"}

class FileActionRequest(BaseModel):
    filename: str

@app.post("/api/open_folder")
def handle_open_folder(req: FileActionRequest):
    _validate_filename(req.filename)
    models_dir = get_models_dir()
    file_path = os.path.join(models_dir, req.filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    norm_path = os.path.normpath(file_path)
    current_platform = platform.system()
    try:
        if current_platform == "Windows":
            subprocess.run(["explorer", f"/select,{norm_path}"])
        elif current_platform == "Darwin":
            subprocess.run(["open", "-R", norm_path])
        else:
            # Linux: open the parent directory
            subprocess.run(["xdg-open", os.path.dirname(norm_path)])
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"File explorer not available on {current_platform}")
    return {"message": "Opened folder"}

@app.post("/api/delete")
def handle_delete_file(req: FileActionRequest):
    _validate_filename(req.filename)
    try:
        delete_local_file(req.filename, get_models_dir())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"message": f"Deleted {req.filename}"}

@app.post("/api/dismiss")
def handle_dismiss_download(req: FileActionRequest):
    """Remove a stale canceled/errored entry from the downloads list."""
    _validate_filename(req.filename)
    dismiss_download(req.filename)
    return {"message": f"Dismissed {req.filename}"}

class ConfigRequest(BaseModel):
    models_dir: str

@app.get("/api/config")
def get_config():
    return {"models_dir": get_models_dir()}

@app.post("/api/config")
def update_config(req: ConfigRequest):
    new_dir = os.path.normpath(req.models_dir)
    if not new_dir:
        raise HTTPException(status_code=400, detail="models_dir cannot be empty")
    # Basic path validation — reject system-critical directories
    critical_paths = [os.path.normpath(p) for p in [os.environ.get('WINDIR', 'C:\\Windows'), os.environ.get('SYSTEMROOT', 'C:\\Windows')]]
    if any(new_dir.lower().startswith(cp.lower()) for cp in critical_paths):
        raise HTTPException(status_code=400, detail="Cannot use a system directory as models path")
    os.makedirs(new_dir, exist_ok=True)
    set_models_dir(new_dir)
    
    current_dir = get_models_dir()
    config["models_dir"] = current_dir
    save_config(config)
    
    # Refresh local files
    clear_local_completed_files()
    sync_local_files(current_dir)
    
    return {"message": "Config updated", "models_dir": current_dir}

@app.get("/api/choose_directory")
def choose_directory():
    try:
        script = "import tkinter as tk; from tkinter import filedialog; root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); d = filedialog.askdirectory(parent=root); print(d)"
        result = subprocess.check_output([sys.executable, "-c", script], text=True, timeout=120).strip()
        if result:
            return {"directory": result}
        else:
            return {"directory": ""}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Directory chooser timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/progress")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            active = get_active_downloads()
            await websocket.send_json(active)
            await asyncio.sleep(1) # send updates every second
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")

# Register chat routes (conversations, model management, WebSocket chat)
app.include_router(chat_router)

# Mount frontend static files last so API routes are matched first
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
