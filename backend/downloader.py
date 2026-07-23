import os
import threading
import requests
from huggingface_hub import hf_hub_url

# Thread lock to protect shared state
_lock = threading.Lock()

# Dictionary to keep track of download progress
# key: filename, value: dictionary of progress info
active_downloads = {}

# Set to keep track of requested cancellations
cancel_requests = set()

# Set to keep track of requested pauses
pause_requests = set()

def download_model_file(repo_id: str, filename: str, models_dir: str):
    """
    Downloads a file from hugging face to the local directory directly using requests.
    This bypasses huggingface_hub's cache and avoids file locking and tqdm issues.
    """
    with _lock:
        if filename in cancel_requests:
            cancel_requests.remove(filename)
        if filename in pause_requests:
            pause_requests.remove(filename)

    file_path = os.path.join(models_dir, filename)
    temp_path = file_path + ".downloading"

    with _lock:
        active_downloads[filename] = {
            "total": 0,
            "completed": 0,
            "status": "starting",
            "repo_id": repo_id
        }

    response = None
    try:
        url = hf_hub_url(repo_id=repo_id, filename=filename)

        # Check if we have a partial file for resuming
        headers = {}

        resume_byte_pos = 0
        if os.path.exists(temp_path):
            resume_byte_pos = os.path.getsize(temp_path)
            headers['Range'] = f'bytes={resume_byte_pos}-'

        response = requests.get(url, headers=headers, stream=True, allow_redirects=True)

        if response.status_code not in [200, 206]:
            raise Exception(f"Failed to download: HTTP {response.status_code}")

        # Total size from Content-Range or Content-Length
        if response.status_code == 206:
            content_range = response.headers.get('content-range')
            if content_range and '/' in content_range:
                total_size = int(content_range.split('/')[1])
            else:
                # Fallback: use content-length + resume position
                total_size = int(response.headers.get('content-length', 0)) + resume_byte_pos
        else:
            total_size = int(response.headers.get('content-length', 0))

        if response.status_code == 200:
            resume_byte_pos = 0

        with _lock:
            active_downloads[filename]["total"] = total_size
            active_downloads[filename]["completed"] = resume_byte_pos
            active_downloads[filename]["status"] = "downloading"

        mode = 'ab' if response.status_code == 206 else 'wb'

        cancelled = False
        paused = False

        with open(temp_path, mode) as f:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024): # 8MB chunks
                with _lock:
                    if filename in cancel_requests:
                        cancelled = True
                        cancel_requests.discard(filename)
                        active_downloads[filename]["status"] = "canceled"
                        break

                    if filename in pause_requests:
                        paused = True
                        pause_requests.discard(filename)
                        active_downloads[filename]["status"] = "paused"
                        break

                if chunk:
                    f.write(chunk)
                    resume_byte_pos += len(chunk)
                    with _lock:
                        active_downloads[filename]["completed"] = resume_byte_pos

        # Handle cancel cleanup after the file is properly closed
        if cancelled:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return

        if paused:
            return  # Keep the partial file for resume

        # Download finished completely
        if os.path.exists(file_path):
            os.remove(file_path)
        os.rename(temp_path, file_path)

        with _lock:
            active_downloads[filename]["completed"] = total_size
            active_downloads[filename]["status"] = "completed"
        return file_path

    except Exception as e:
        with _lock:
            active_downloads[filename]["status"] = f"error: {e}"
            cancel_requests.discard(filename)
        raise
    finally:
        # Always close the response to release the HTTP connection
        if response is not None:
            response.close()

def cancel_download(filename: str, models_dir: str):
    temp_to_delete = None
    with _lock:
        if filename in active_downloads and active_downloads[filename]["status"] in ["starting", "downloading", "paused"]:
            if active_downloads[filename]["status"] == "paused":
                # If it's already paused, we can just cancel it immediately and delete the temp file
                active_downloads[filename]["status"] = "canceled"
                temp_to_delete = os.path.join(models_dir, f"{filename}.downloading")
            else:
                cancel_requests.add(filename)
                active_downloads[filename]["status"] = "canceling..."
    # File I/O outside the lock to avoid blocking other threads
    if temp_to_delete and os.path.exists(temp_to_delete):
        try:
            os.remove(temp_to_delete)
        except OSError:
            pass

def pause_download(filename: str):
    with _lock:
        if filename in active_downloads and active_downloads[filename]["status"] in ["starting", "downloading"]:
            pause_requests.add(filename)
            active_downloads[filename]["status"] = "pausing..."

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
        for filepath in glob.glob(os.path.join(models_dir, "*.gguf")):
            filename = os.path.basename(filepath)
            size = os.path.getsize(filepath)
            if filename not in active_downloads or active_downloads[filename]["status"] != "downloading":
                active_downloads[filename] = {
                    "total": size,
                    "completed": size,
                    "status": "completed",
                    "repo_id": "local" # We may not know the exact repo for locally found files
                }

def delete_local_file(filename: str, models_dir: str):
    # Check under lock that we're not deleting an actively downloading file
    with _lock:
        if filename in active_downloads and active_downloads[filename]["status"] in ["downloading", "starting"]:
            raise Exception(f"Cannot delete '{filename}' while it is being downloaded. Cancel it first.")

    file_path = os.path.join(models_dir, filename)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError as e:
        raise Exception(f"Failed to delete '{filename}': {e}")
    # Also clean up any lingering partial downloads
    temp_path = file_path + ".downloading"
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except OSError:
        pass  # Best-effort cleanup of temp file

    with _lock:
        if filename in active_downloads:
            del active_downloads[filename]

def dismiss_download(filename: str):
    """Remove a stale (canceled/errored) entry from active_downloads."""
    with _lock:
        if filename in active_downloads and active_downloads[filename]["status"] in ["canceled", "error"]:
            del active_downloads[filename]
        elif filename in active_downloads and active_downloads[filename]["status"].startswith("error"):
            del active_downloads[filename]
