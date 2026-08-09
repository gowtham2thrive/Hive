import os
import threading
import requests
from huggingface_hub import hf_hub_url

# Thread lock to protect shared state
_lock = threading.Lock()

# Dictionary to keep track of download progress
# key: composite_key (repo_id/filename), value: dictionary of progress info
active_downloads = {}

# Set to keep track of requested cancellations
cancel_requests = set()

# Set to keep track of requested pauses
pause_requests = set()

def download_model_file(repo_id: str, filename: str, models_dir: str):
    composite_key = f"{repo_id}/{filename}"
    with _lock:
        if composite_key in cancel_requests:
            cancel_requests.remove(composite_key)
        if composite_key in pause_requests:
            pause_requests.remove(composite_key)

    parts = [p for p in repo_id.split('/') if p and p not in ('.', '..')]
    repo_dir = os.path.join(models_dir, *parts) if parts else models_dir
    os.makedirs(repo_dir, exist_ok=True)
    
    file_path = os.path.join(repo_dir, filename)
    temp_path = file_path + ".downloading"

    with _lock:
        active_downloads[composite_key] = {
            "total": 0,
            "completed": 0,
            "status": "starting",
            "repo_id": repo_id
        }

    response = None
    try:
        url = hf_hub_url(repo_id=repo_id, filename=filename)

        headers = {}
        resume_byte_pos = 0
        if os.path.exists(temp_path):
            resume_byte_pos = os.path.getsize(temp_path)
            headers['Range'] = f'bytes={resume_byte_pos}-'

        response = requests.get(url, headers=headers, stream=True, allow_redirects=True)

        if response.status_code not in [200, 206]:
            raise Exception(f"Failed to download: HTTP {response.status_code}")

        if response.status_code == 206:
            content_range = response.headers.get('content-range')
            if content_range and '/' in content_range:
                total_size = int(content_range.split('/')[1])
            else:
                total_size = int(response.headers.get('content-length', 0)) + resume_byte_pos
        else:
            total_size = int(response.headers.get('content-length', 0))

        if response.status_code == 200:
            resume_byte_pos = 0

        with _lock:
            active_downloads[composite_key]["total"] = total_size
            active_downloads[composite_key]["completed"] = resume_byte_pos
            active_downloads[composite_key]["status"] = "downloading"

        mode = 'ab' if response.status_code == 206 else 'wb'
        cancelled = False
        paused = False

        with open(temp_path, mode) as f:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024): # 8MB chunks
                with _lock:
                    if composite_key in cancel_requests:
                        cancelled = True
                        cancel_requests.discard(composite_key)
                        active_downloads[composite_key]["status"] = "canceled"
                        break

                    if composite_key in pause_requests:
                        paused = True
                        pause_requests.discard(composite_key)
                        active_downloads[composite_key]["status"] = "paused"
                        break

                if chunk:
                    f.write(chunk)
                    resume_byte_pos += len(chunk)
                    with _lock:
                        active_downloads[composite_key]["completed"] = resume_byte_pos

        if cancelled:
            if os.path.exists(temp_path):
                try: os.remove(temp_path)
                except OSError: pass
            return

        if paused:
            return

        if os.path.exists(file_path):
            os.remove(file_path)
        os.rename(temp_path, file_path)

        with _lock:
            active_downloads[composite_key]["completed"] = total_size
            active_downloads[composite_key]["status"] = "completed"
        return file_path

    except Exception as e:
        with _lock:
            active_downloads[composite_key]["status"] = f"error: {e}"
            cancel_requests.discard(composite_key)
        raise
    finally:
        if response is not None:
            response.close()

def cancel_download(repo_id: str, filename: str, models_dir: str):
    composite_key = f"{repo_id}/{filename}"
    temp_to_delete = None
    with _lock:
        if composite_key in active_downloads and active_downloads[composite_key]["status"] in ["starting", "downloading", "paused"]:
            if active_downloads[composite_key]["status"] == "paused":
                active_downloads[composite_key]["status"] = "canceled"
                parts = [p for p in repo_id.split('/') if p and p not in ('.', '..')] if repo_id != "local" else []
                repo_dir = os.path.join(models_dir, *parts) if parts else models_dir
                temp_to_delete = os.path.join(repo_dir, f"{filename}.downloading")
            else:
                cancel_requests.add(composite_key)
                active_downloads[composite_key]["status"] = "canceling..."
    if temp_to_delete and os.path.exists(temp_to_delete):
        try: os.remove(temp_to_delete)
        except OSError: pass

def pause_download(repo_id: str, filename: str):
    composite_key = f"{repo_id}/{filename}"
    with _lock:
        if composite_key in active_downloads and active_downloads[composite_key]["status"] in ["starting", "downloading"]:
            pause_requests.add(composite_key)
            active_downloads[composite_key]["status"] = "pausing..."

def get_active_downloads():
    with _lock:
        return {k: dict(v) for k, v in active_downloads.items()}

def clear_local_completed_files():
    with _lock:
        to_delete = [k for k, v in active_downloads.items() if v["status"] == "completed"]
        for k in to_delete:
            del active_downloads[k]

def sync_local_files(models_dir: str):
    import glob
    with _lock:
        for filepath in glob.glob(os.path.join(models_dir, "**", "*.gguf"), recursive=True):
            rel_path = os.path.relpath(filepath, models_dir)
            parts = rel_path.replace('\\', '/').split('/')
            filename = parts[-1]
            repo_id = "/".join(parts[:-1]) if len(parts) > 1 else "local"
            composite_key = f"{repo_id}/{filename}"
            size = os.path.getsize(filepath)
            
            if composite_key not in active_downloads or active_downloads[composite_key]["status"] != "downloading":
                active_downloads[composite_key] = {
                    "total": size,
                    "completed": size,
                    "status": "completed",
                    "repo_id": repo_id
                }

def delete_local_file(repo_id: str, filename: str, models_dir: str):
    composite_key = f"{repo_id}/{filename}"
    with _lock:
        if composite_key in active_downloads and active_downloads[composite_key]["status"] in ["downloading", "starting"]:
            raise Exception(f"Cannot delete '{filename}' while it is being downloaded. Cancel it first.")

    parts = [p for p in repo_id.split('/') if p and p not in ('.', '..')] if repo_id != "local" else []
    repo_dir = os.path.join(models_dir, *parts) if parts else models_dir
    file_path = os.path.join(repo_dir, filename)
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError as e:
        raise Exception(f"Failed to delete '{filename}': {e}")
    
    temp_path = file_path + ".downloading"
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except OSError:
        pass

    with _lock:
        if composite_key in active_downloads:
            del active_downloads[composite_key]

def dismiss_download(repo_id: str, filename: str):
    composite_key = f"{repo_id}/{filename}"
    with _lock:
        if composite_key in active_downloads and active_downloads[composite_key]["status"] in ["canceled", "error"]:
            del active_downloads[composite_key]
        elif composite_key in active_downloads and active_downloads[composite_key]["status"].startswith("error"):
            del active_downloads[composite_key]
