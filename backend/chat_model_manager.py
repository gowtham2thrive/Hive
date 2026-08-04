"""
chat_model_manager.py — Loads GGUF models via llama-cpp-python for Hive chat.
Auto-detects CPU cores and RAM for optimal thread/batch configuration.
Supports streaming inference with cancellation.
"""

import asyncio
import os
import struct
import queue as _stdlib_queue
import time
from dataclasses import dataclass
from threading import Lock, Event
from typing import Generator


# ── GGUF Metadata Reader ──────────────────────────────────────────
# Lightweight parser — reads only the header, not tensor weights.

_GGUF_TYPES = {
    0: ("uint8", "B", 1),
    1: ("int8", "b", 1),
    2: ("uint16", "H", 2),
    3: ("int16", "h", 2),
    4: ("uint32", "I", 4),
    5: ("int32", "i", 4),
    6: ("float32", "f", 4),
    7: ("bool", "?", 1),
    8: ("string", None, None),
    9: ("array", None, None),
    10: ("uint64", "Q", 8),
    11: ("int64", "q", 8),
    12: ("float64", "d", 8),
}


def _read_gguf_string(f) -> str:
    length = struct.unpack("<Q", f.read(8))[0]
    return f.read(length).decode("utf-8", errors="replace")


def _read_gguf_typed_value(f, value_type: int):
    if value_type == 8:
        return _read_gguf_string(f)
    elif value_type == 9:
        elem_type = struct.unpack("<I", f.read(4))[0]
        count = struct.unpack("<Q", f.read(8))[0]
        return [_read_gguf_typed_value(f, elem_type) for _ in range(count)]
    else:
        type_info = _GGUF_TYPES.get(value_type)
        if type_info is None:
            raise ValueError(f"Unknown GGUF value type: {value_type}")
        _, fmt, size = type_info
        return struct.unpack(f"<{fmt}", f.read(size))[0]


def _read_gguf_value(f):
    value_type = struct.unpack("<I", f.read(4))[0]
    return _read_gguf_typed_value(f, value_type)


def read_gguf_metadata(filepath: str) -> dict:
    """Read metadata key-value pairs from a GGUF file header (~1ms)."""
    metadata = {}
    with open(filepath, "rb") as f:
        magic = f.read(4)
        if magic != b"GGUF":
            raise ValueError(f"Not a GGUF file (magic: {magic!r})")
        version = struct.unpack("<I", f.read(4))[0]
        if version not in (2, 3):
            raise ValueError(f"Unsupported GGUF version: {version}")
        _tensor_count = struct.unpack("<Q", f.read(8))[0]
        metadata_kv_count = struct.unpack("<Q", f.read(8))[0]
        for _ in range(metadata_kv_count):
            key = _read_gguf_string(f)
            value = _read_gguf_value(f)
            metadata[key] = value
    return metadata


def get_model_architecture(filepath: str) -> dict:
    """Extract architecture info needed for auto-tuning."""
    try:
        meta = read_gguf_metadata(filepath)
    except (ValueError, OSError, struct.error):
        return {"arch": "", "block_count": 0, "context_length": 0,
                "embedding_length": 0, "head_count": 0, "head_count_kv": 0, "size_label": ""}

    arch = str(meta.get("general.architecture", ""))

    def _int_val(key):
        val = meta.get(key, 0)
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    return {
        "arch": arch,
        "block_count": _int_val(f"{arch}.block_count"),
        "context_length": _int_val(f"{arch}.context_length"),
        "embedding_length": _int_val(f"{arch}.embedding_length"),
        "head_count": _int_val(f"{arch}.attention.head_count"),
        "head_count_kv": _int_val(f"{arch}.attention.head_count_kv"),
        "size_label": str(meta.get("general.size_label", "")),
    }


# ── Data Classes ───────────────────────────────────────────────────

@dataclass
class ModelInfo:
    """Metadata about the currently loaded model."""
    path: str = ""
    filename: str = ""
    size_mb: float = 0.0
    n_ctx: int = 0
    n_gpu_layers: int = 0
    loaded: bool = False
    load_time_sec: float = 0.0
    n_threads: int = 1
    n_batch: int = 512


# ── Model Manager ─────────────────────────────────────────────────

class ChatModelManager:
    """
    Singleton manager for loading/unloading GGUF models in Hive.
    Auto-tunes threads and batch size based on hardware.
    """

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._model = None
        self._info = ModelInfo()
        self._inference_lock = Lock()
        self._cancel_event = Event()
        self._initialized = True

    # ── Hardware Detection (simplified) ────────────────────────────

    @staticmethod
    def _detect_threads_and_batch() -> tuple[int, int]:
        """Detect optimal thread count and batch size."""
        try:
            import psutil
            physical_cores = psutil.cpu_count(logical=False) or 1
            mem = psutil.virtual_memory()
            ram_available_mb = int(mem.available / (1024 * 1024))
        except ImportError:
            physical_cores = os.cpu_count() or 1
            ram_available_mb = 8192  # assume 8GB fallback

        threads = max(1, physical_cores - 1)
        batch = 1024 if ram_available_mb > 8192 else 512
        return threads, batch

    # ── Model Loading ──────────────────────────────────────────────

    def load_model(self, model_path: str) -> ModelInfo:
        """Load a GGUF model with auto-tuned parameters."""
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
        if not model_path.lower().endswith(".gguf"):
            raise ValueError("Only .gguf model files are supported.")

        self.unload_model()

        from llama_cpp import Llama

        # Read model metadata for context length
        arch_info = get_model_architecture(model_path)
        native_ctx = arch_info.get("context_length", 0) or 4096
        # Cap context to a reasonable maximum for RAM safety
        n_ctx = min(native_ctx, 8192)

        opt_threads, opt_batch = self._detect_threads_and_batch()
        file_size_mb = os.path.getsize(model_path) / (1024 * 1024)

        print(f"[Hive Chat] Loading model: {os.path.basename(model_path)}")
        print(f"[Hive Chat] Config: n_ctx={n_ctx}, threads={opt_threads}, batch={opt_batch}")

        start_time = time.time()

        # Try loading with progressively safer configs
        attempts = [
            {"n_ctx": n_ctx, "label": f"ctx={n_ctx}"},
            {"n_ctx": 4096, "label": "safe ctx=4096"},
            {"n_ctx": 2048, "label": "minimal ctx=2048"},
        ]

        for i, attempt in enumerate(attempts):
            try:
                print(f"[Hive Chat] Attempting load: {attempt['label']}...")
                self._model = Llama(
                    model_path=model_path,
                    n_ctx=attempt["n_ctx"],
                    n_gpu_layers=0,  # CPU-only for now
                    n_threads=opt_threads,
                    n_batch=opt_batch,
                    use_mmap=True,
                    use_mlock=False,
                    verbose=False,
                )
                n_ctx = attempt["n_ctx"]
                break
            except Exception as e:
                print(f"[Hive Chat] {attempt['label']} failed: {e}")
                if i == len(attempts) - 1:
                    raise

        load_duration = time.time() - start_time
        actual_n_ctx = self._model.n_ctx()

        self._info = ModelInfo(
            path=model_path,
            filename=os.path.basename(model_path),
            size_mb=round(file_size_mb, 1),
            n_ctx=actual_n_ctx,
            n_gpu_layers=0,
            loaded=True,
            load_time_sec=round(load_duration, 2),
            n_threads=opt_threads,
            n_batch=opt_batch,
        )

        print(f"[Hive Chat] Model loaded in {load_duration:.1f}s")
        return self._info

    def unload_model(self):
        """Unload the current model and free memory."""
        self.cancel_generation()
        if self._model is not None:
            del self._model
            self._model = None
        self._info = ModelInfo()

    # ── Cancellation ───────────────────────────────────────────────

    def cancel_generation(self):
        """Signal the running inference to stop immediately."""
        self._cancel_event.set()

    # ── Inference ──────────────────────────────────────────────────

    THINKING_SENTINEL = "\x00THINKING\x00"
    _LOCK_TIMEOUT_SEC = 15

    def generate_stream(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> Generator[str, None, None]:
        """
        Stream tokens with optimized sampling.
        _cancel_event.clear() is INSIDE the lock to prevent race conditions.
        """
        if self._model is None:
            raise RuntimeError("No model is loaded. Load a model first.")

        acquired = self._inference_lock.acquire(timeout=self._LOCK_TIMEOUT_SEC)
        if not acquired:
            raise RuntimeError(
                "Inference timeout — previous generation is still running. "
                "Try again in a moment."
            )
        try:
            self._cancel_event.clear()
            yield self.THINKING_SENTINEL

            stream = self._model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=40,
                repeat_penalty=1.03,
                stream=True,
            )

            for chunk in stream:
                if self._cancel_event.is_set():
                    break
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
        except Exception:
            raise
        finally:
            self._inference_lock.release()

    async def agenerate_stream(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ):
        """
        Async streaming bridge: runs inference in a thread, delivers
        tokens to the async consumer via a stdlib thread-safe Queue.
        """
        loop = asyncio.get_running_loop()
        q: _stdlib_queue.Queue[str | Exception | None] = _stdlib_queue.Queue(maxsize=64)
        producer_done = Event()

        def _produce():
            try:
                for token in self.generate_stream(messages, max_tokens, temperature, top_p):
                    if self._cancel_event.is_set():
                        break
                    try:
                        q.put(token, timeout=5.0)
                    except _stdlib_queue.Full:
                        break
            except Exception as exc:
                try:
                    q.put(exc, timeout=5.0)
                except _stdlib_queue.Full:
                    pass
            finally:
                for _ in range(50):
                    try:
                        q.put(None, timeout=0.1)
                        break
                    except _stdlib_queue.Full:
                        continue
                producer_done.set()

        future = loop.run_in_executor(None, _produce)
        consumer_interrupted = True

        try:
            while True:
                try:
                    item = await asyncio.to_thread(q.get, timeout=0.1)
                except _stdlib_queue.Empty:
                    if producer_done.is_set():
                        while not q.empty():
                            item = q.get_nowait()
                            if item is None:
                                consumer_interrupted = False
                                return
                            if isinstance(item, Exception):
                                raise item
                            yield item
                        consumer_interrupted = False
                        return
                    continue

                if item is None:
                    consumer_interrupted = False
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if consumer_interrupted:
                self._cancel_event.set()
            while not q.empty():
                try:
                    q.get_nowait()
                except _stdlib_queue.Empty:
                    break

    # ── Status ─────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return current model status."""
        return {
            "loaded": self._info.loaded,
            "filename": self._info.filename,
            "path": self._info.path,
            "size_mb": self._info.size_mb,
            "n_ctx": self._info.n_ctx,
            "n_gpu_layers": self._info.n_gpu_layers,
            "load_time_sec": self._info.load_time_sec,
            "n_threads": self._info.n_threads,
            "n_batch": self._info.n_batch,
        }

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def context_size(self) -> int:
        """Return the context window size of the loaded model."""
        return self._info.n_ctx if self._info.loaded else 4096


# Global instance
chat_model_manager = ChatModelManager()
