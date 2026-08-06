"""
chat_routes.py — WebSocket chat endpoint + conversation REST API + model management for Hive.
Smart context management: truncates history to fit context window.
WebSocket micro-batching for efficient token delivery.
"""

import asyncio
import glob
import json
import os
import subprocess
import sys
import time
import traceback

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.chat_database import (
    add_message,
    create_conversation,
    delete_conversation,
    get_messages,
    list_conversations,
    update_conversation_title,
    get_conversation,
)
from backend.chat_model_manager import chat_model_manager

router = APIRouter(tags=["chat"])


# ── REST: Conversation Management ──────────────────────────────────


class CreateConversationRequest(BaseModel):
    title: str = "New Chat"


class RenameConversationRequest(BaseModel):
    title: str


@router.get("/api/chat/conversations")
async def api_list_conversations():
    """List all conversations."""
    return await list_conversations()


@router.post("/api/chat/conversations")
async def api_create_conversation(req: CreateConversationRequest):
    """Create a new conversation."""
    return await create_conversation(req.title)


@router.get("/api/chat/conversations/{conv_id}")
async def api_get_conversation(conv_id: str):
    """Get a conversation and its messages."""
    conv = await get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = await get_messages(conv_id)
    return {"conversation": conv, "messages": messages}


@router.put("/api/chat/conversations/{conv_id}")
async def api_rename_conversation(conv_id: str, req: RenameConversationRequest):
    """Rename a conversation."""
    await update_conversation_title(conv_id, req.title)
    return {"status": "ok"}


@router.delete("/api/chat/conversations/{conv_id}")
async def api_delete_conversation(conv_id: str):
    """Delete a conversation."""
    await delete_conversation(conv_id)
    return {"status": "ok"}


# ── REST: Model Management ─────────────────────────────────────────


class LoadModelRequest(BaseModel):
    path: str


@router.post("/api/chat/model/load")
async def load_model(req: LoadModelRequest):
    """Load a GGUF model from disk."""
    try:
        info = await asyncio.to_thread(
            chat_model_manager.load_model,
            model_path=req.path,
        )
        return {
            "status": "ok",
            "message": f"Model loaded: {info.filename}",
            "model": chat_model_manager.get_status(),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {e}")


@router.post("/api/chat/model/unload")
async def unload_model():
    """Unload the current model and free memory."""
    chat_model_manager.unload_model()
    return {"status": "ok", "message": "Model unloaded."}


@router.get("/api/chat/model/status")
async def model_status():
    """Get current model load status."""
    return chat_model_manager.get_status()


@router.get("/api/chat/model/list")
async def list_local_models():
    """List available .gguf files in the Hive models directory."""
    from backend.main import get_models_dir
    models_dir = get_models_dir()
    models = []
    if os.path.isdir(models_dir):
        for filepath in glob.glob(os.path.join(models_dir, "*.gguf")):
            filename = os.path.basename(filepath)
            size_mb = round(os.path.getsize(filepath) / (1024 * 1024), 1)
            models.append({
                "filename": filename,
                "path": filepath,
                "size_mb": size_mb,
            })
    # Sort by filename
    models.sort(key=lambda m: m["filename"].lower())
    return models


@router.get("/api/chat/model/pick")
async def pick_model_file():
    """Open a native OS file picker dialog for .gguf files."""
    try:
        if sys.platform == "win32":
            # Windows: use PowerShell with .NET OpenFileDialog + topmost owner form
            ps_script = (
                'Add-Type -AssemblyName System.Windows.Forms; '
                '$f = New-Object System.Windows.Forms.Form; '
                '$f.TopMost = $true; '
                '$f.ShowInTaskbar = $false; '
                '$f.WindowState = [System.Windows.Forms.FormWindowState]::Minimized; '
                '$f.Show(); $f.Hide(); '
                '$d = New-Object System.Windows.Forms.OpenFileDialog; '
                '$d.Title = "Select GGUF Model"; '
                '$d.Filter = "GGUF Models (*.gguf)|*.gguf|All Files (*.*)|*.*"; '
                '$d.Multiselect = $false; '
                '$null = $d.ShowDialog($f); '
                '$f.Dispose(); '
                'Write-Output $d.FileName'
            )
            result = await asyncio.to_thread(
                subprocess.run,
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            path = result.stdout.strip()
        else:
            # macOS / Linux: use tkinter
            script = (
                "import tkinter as tk; from tkinter import filedialog; "
                "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
                "d = filedialog.askopenfilename(parent=root, title='Select GGUF Model', "
                "filetypes=[('GGUF Models', '*.gguf'), ('All Files', '*.*')]); print(d)"
            )
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            path = result.stdout.strip()

        if not path:
            return {"selected": False, "path": ""}
        return {"selected": True, "path": path}
    except subprocess.TimeoutExpired:
        return {"selected": False, "path": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File picker failed: {e}")


# ── REST: GPU / Hardware ───────────────────────────────────────────


@router.get("/api/chat/hardware")
async def get_hardware_info(force: bool = False):
    """
    Return full hardware profile: CPU, RAM, GPU, VRAM, backend status.

    Uses a 60-second TTL cache by default. Pass ?force=true from the
    Re-detect button to bypass the cache and re-run detection.
    """
    try:
        if force:
            # Invalidate cache so get_hardware_info() re-detects
            chat_model_manager._hardware_timestamp = 0.0
        return await asyncio.to_thread(chat_model_manager.get_hardware_info)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hardware detection failed: {e}")


@router.post("/api/chat/gpu/rebuild")
async def rebuild_gpu_backend():
    """
    Rebuild llama-cpp-python with Vulkan GPU support.
    This is a long-running operation (5-15 minutes) that downloads
    build tools if needed and compiles from source.
    """
    try:
        from backend.gpu_detect import build_vulkan_llama, install_build_tools, check_build_tools

        # Install build tools first if needed
        if not check_build_tools():
            tools_result = await asyncio.to_thread(install_build_tools)
            if not tools_result["success"]:
                return {
                    "success": False,
                    "error": f"Build tools installation failed: {', '.join(tools_result['failed'])}",
                    "details": tools_result,
                }

        # Build with Vulkan
        result = await asyncio.to_thread(build_vulkan_llama)

        # O6: Update .gpu_backend marker so start.bat doesn't re-detect on next launch
        if result.get("success"):
            _update_gpu_marker("vulkan", result.get("gpu_offload", False))

            # Invalidate hardware cache so next detection picks up the new library.
            chat_model_manager._hardware_timestamp = 0.0

            # Reload llama_cpp module so the running process uses the new
            # Vulkan-backed binary instead of the stale cached CPU-only one.
            try:
                import importlib
                import llama_cpp
                importlib.reload(llama_cpp)
            except Exception:
                pass  # Will work correctly after server restart

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPU rebuild failed: {e}")


def _update_gpu_marker(backend: str, gpu_offload: bool):
    """Write the .gpu_backend marker file after a successful rebuild."""
    try:
        import pathlib
        project_root = pathlib.Path(__file__).resolve().parent.parent
        marker = project_root / ".gpu_backend"
        marker.write_text(
            f"[backend]\ntype={backend}\ngpu_offload={gpu_offload}\n",
            encoding="utf-8",
        )
    except Exception:
        pass  # Non-critical — worst case start.bat re-detects next launch


@router.get("/api/chat/gpu/verify")
async def verify_gpu_support():
    """Check if the installed llama-cpp-python supports GPU offload."""
    try:
        result = await asyncio.to_thread(chat_model_manager.verify_gpu_support)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPU verification failed: {e}")


# ── Context Management ─────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~3 characters per token (slightly over-estimates for safety)."""
    return max(1, len(text) // 3)


def truncate_messages_to_fit(
    messages: list[dict],
    max_context_tokens: int,
    reserved_for_response: int,
) -> list[dict]:
    """
    Truncate message history to fit within the context window.
    Strategy:
    - Always keep the latest user message
    - Always keep system prompt if present
    - Trim oldest messages first until total fits
    - Reserve tokens for the model's response
    """
    available = max_context_tokens - reserved_for_response
    if available <= 0:
        return messages[-1:] if messages else []

    msg_tokens = [(msg, estimate_tokens(msg.get("content", ""))) for msg in messages]
    total = sum(t for _, t in msg_tokens)

    if total <= available:
        return messages

    result = []
    has_system = messages and messages[0].get("role") == "system"

    if has_system:
        result.append(msg_tokens[0])
        msg_tokens = msg_tokens[1:]

    # Always keep the latest user message (and any trailing assistant)
    protected_tail = []
    for msg, tok in reversed(msg_tokens):
        protected_tail.insert(0, (msg, tok))
        if msg.get("role") == "user":
            break
    msg_tokens = msg_tokens[:len(msg_tokens) - len(protected_tail)]

    protected_cost = sum(t for _, t in result) + sum(t for _, t in protected_tail)
    remaining_budget = available - protected_cost

    # Fill from most recent to oldest
    middle = []
    for msg, tok in reversed(msg_tokens):
        if remaining_budget >= tok:
            middle.insert(0, (msg, tok))
            remaining_budget -= tok
        else:
            break

    final = [msg for msg, _ in result] + [msg for msg, _ in middle] + [msg for msg, _ in protected_tail]
    return final


# ── Auto Max Tokens ────────────────────────────────────────────────

SAFETY_BUFFER = 100
MIN_RESPONSE_TOKENS = 128


def calculate_max_tokens(
    messages: list[dict],
    context_window: int,
) -> int:
    """Dynamically calculate max_tokens from remaining context space."""
    history_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
    remaining = context_window - history_tokens - SAFETY_BUFFER

    if remaining <= MIN_RESPONSE_TOKENS:
        return MIN_RESPONSE_TOKENS

    history_fraction = history_tokens / context_window if context_window > 0 else 1.0
    if history_fraction < 0.15:
        ratio = 0.92
    elif history_fraction < 0.40:
        ratio = 0.85
    else:
        ratio = 0.75

    budget = int(remaining * ratio)
    return max(MIN_RESPONSE_TOKENS, budget)


# ── WebSocket: Streaming Chat ──────────────────────────────────────

WS_FLUSH_INTERVAL_MS = 30


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat with micro-batching.

    Client sends JSON:
    { "conversation_id": "...", "message": "user's message", "temperature": 0.7 }

    Or a control message:
    { "type": "cancel" }

    Server streams back JSON frames:
    { "type": "token", "content": "..." }
    { "type": "done", "full_content": "..." }
    { "type": "error", "message": "..." }
    """
    await websocket.accept()

    pending_messages: asyncio.Queue[dict | None] = asyncio.Queue()

    try:
        while True:
            data = None
            if not pending_messages.empty():
                try:
                    data = pending_messages.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            if data is None:
                raw = await websocket.receive_text()
                data = json.loads(raw)

            if data.get("type") == "cancel":
                chat_model_manager.cancel_generation()
                await websocket.send_json({"type": "cancelled"})
                continue

            conv_id = data.get("conversation_id")
            user_message = data.get("message", "").strip()
            temperature = data.get("temperature", 0.7)

            if not user_message:
                await websocket.send_json({"type": "error", "message": "Empty message."})
                continue

            if not chat_model_manager.is_loaded:
                await websocket.send_json({"type": "error", "message": "No model loaded. Load a model first from Chat settings."})
                continue

            # Create conversation if needed
            if not conv_id:
                conv = await create_conversation(user_message[:50])
                conv_id = conv["id"]
                await websocket.send_json({"type": "conversation_created", "conversation": conv})

            # Save user message
            await add_message(conv_id, "user", user_message)

            # Build message history with smart context truncation
            db_messages = await get_messages(conv_id)
            chat_messages = [{"role": m["role"], "content": m["content"]} for m in db_messages]

            # Add default system prompt if none in history
            if not chat_messages or chat_messages[0].get("role") != "system":
                chat_messages.insert(0, {
                    "role": "system",
                    "content": (
                        "You are Hive Assistant, a helpful AI assistant. "
                        "Respond thoroughly and completely — always finish your response. "
                        "Use markdown formatting when appropriate for code, lists, and emphasis."
                    ),
                })

            # Two-pass max_tokens calculation
            n_ctx = chat_model_manager.context_size
            max_tokens = calculate_max_tokens(chat_messages, n_ctx)

            chat_messages = truncate_messages_to_fit(
                chat_messages,
                max_context_tokens=n_ctx,
                reserved_for_response=max_tokens + SAFETY_BUFFER,
            )

            max_tokens = calculate_max_tokens(chat_messages, n_ctx)

            # Concurrent cancel listener
            cancel_event = asyncio.Event()

            async def _listen_for_cancel():
                try:
                    while not cancel_event.is_set():
                        try:
                            raw_msg = await asyncio.wait_for(
                                websocket.receive_text(), timeout=0.5
                            )
                        except asyncio.TimeoutError:
                            continue
                        msg = json.loads(raw_msg)
                        if msg.get("type") == "cancel":
                            chat_model_manager.cancel_generation()
                            cancel_event.set()
                            try:
                                await websocket.send_json({"type": "cancelled"})
                            except Exception:
                                pass
                        else:
                            await pending_messages.put(msg)
                except WebSocketDisconnect:
                    cancel_event.set()
                except Exception:
                    cancel_event.set()

            listener_task = asyncio.create_task(_listen_for_cancel())

            # Heartbeat during long prompt evaluation
            stream_start_time = time.monotonic()

            async def _send_heartbeat():
                while not cancel_event.is_set():
                    await asyncio.sleep(5)
                    if cancel_event.is_set():
                        break
                    try:
                        await websocket.send_json({
                            "type": "heartbeat",
                            "elapsed": round(time.monotonic() - stream_start_time, 1),
                        })
                    except Exception:
                        break

            heartbeat_task = asyncio.create_task(_send_heartbeat())

            # Stream response with micro-batching
            full_response = ""
            generation_cancelled = False
            token_buffer = ""
            last_flush = time.monotonic()

            try:
                async for token_chunk in chat_model_manager.agenerate_stream(
                    messages=chat_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    if cancel_event.is_set():
                        generation_cancelled = True
                        break

                    if token_chunk == chat_model_manager.THINKING_SENTINEL:
                        try:
                            await websocket.send_json({
                                "type": "status",
                                "message": "Thinking...",
                            })
                        except Exception:
                            pass
                        continue

                    full_response += token_chunk
                    token_buffer += token_chunk

                    now = time.monotonic()
                    if (now - last_flush) * 1000 >= WS_FLUSH_INTERVAL_MS:
                        try:
                            await websocket.send_json({"type": "token", "content": token_buffer})
                            token_buffer = ""
                            last_flush = now
                        except Exception:
                            chat_model_manager.cancel_generation()
                            generation_cancelled = True
                            break

                # Flush remaining tokens
                if token_buffer and not generation_cancelled:
                    try:
                        await websocket.send_json({"type": "token", "content": token_buffer})
                    except Exception:
                        chat_model_manager.cancel_generation()
                        generation_cancelled = True

                # Save assistant response (even partial)
                if full_response:
                    await add_message(conv_id, "assistant", full_response)

                # Auto-title on first exchange
                if len(db_messages) <= 1:
                    title = user_message[:60] + ("..." if len(user_message) > 60 else "")
                    await update_conversation_title(conv_id, title)

                if not generation_cancelled:
                    await websocket.send_json({
                        "type": "done",
                        "conversation_id": conv_id,
                        "full_content": full_response,
                    })

            except Exception as e:
                traceback.print_exc()
                chat_model_manager.cancel_generation()
                if full_response:
                    try:
                        await add_message(conv_id, "assistant", full_response)
                    except Exception:
                        pass
                try:
                    await websocket.send_json({"type": "error", "message": f"Inference error: {e}"})
                except Exception:
                    pass
            finally:
                cancel_event.set()
                listener_task.cancel()
                heartbeat_task.cancel()
                try:
                    await listener_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass

    except WebSocketDisconnect:
        chat_model_manager.cancel_generation()
    except Exception as e:
        traceback.print_exc()
        chat_model_manager.cancel_generation()
        try:
            await websocket.send_json({"type": "error", "message": f"Connection error: {e}"})
        except Exception:
            pass
