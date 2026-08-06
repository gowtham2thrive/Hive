"""
chat_model_manager.py — Loads GGUF models via llama-cpp-python for Hive chat.
Full hardware auto-detection: CPU cores, RAM, GPU/VRAM.
Supports NVIDIA CUDA, Intel SYCL, Apple Metal, AMD ROCm, Vulkan, and CPU fallback.
Dynamically tunes n_threads, n_batch, flash_attn, mmap, mlock for peak performance.
"""

import asyncio
import gc
import os
import platform
import queue as _stdlib_queue
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from threading import Lock, Event
from typing import Generator, TYPE_CHECKING

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]  # Fallback handled in detect_hardware()

if TYPE_CHECKING:
    from llama_cpp import Llama

from backend.intel_gpu import detect_intel_gpu
from backend.gguf_meta import get_model_architecture, estimate_kv_cache_per_token


# ── Hardware Profile ───────────────────────────────────────────────


@dataclass
class HardwareProfile:
    """Complete hardware profile for dynamic optimization."""
    cpu_physical_cores: int = 1
    cpu_total_cores: int = 1
    ram_total_mb: int = 0
    ram_available_mb: int = 0
    gpu_backend: str = "none"
    gpu_name: str = "No GPU detected"
    vram_total_mb: int = 0
    vram_free_mb: int = 0
    recommended_threads: int = 1
    recommended_batch: int = 512
    recommended_gpu_layers: int = 0
    flash_attn_supported: bool = False
    # Multi-GPU and backend verification fields
    gpu_index: int = 0
    multi_gpu: bool = False
    all_gpus: list = field(default_factory=list)
    cuda_driver_version: str = ""
    installed_backend: str = "unknown"
    backend_mismatch: bool = False
    backend_mismatch_msg: str = ""
    # Intel GPU fields
    intel_gpu_raw_name: str = ""
    oneapi_available: bool = False


@dataclass
class ModelInfo:
    """Metadata about the currently loaded model."""
    path: str = ""
    filename: str = ""
    size_mb: float = 0.0
    n_ctx: int = 0
    n_gpu_layers: int = 0
    gpu_backend: str = "none"
    loaded: bool = False
    load_time_sec: float = 0.0
    # Performance tuning fields
    n_threads: int = 1
    n_batch: int = 512
    flash_attn: bool = False
    use_mmap: bool = True
    use_mlock: bool = False


class ChatModelManager:
    """
    Singleton manager for loading / unloading GGUF models.
    Full hardware auto-detection and dynamic performance tuning.
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
        self._model = None  # type: Llama | None
        self._info = ModelInfo()
        self._hardware = HardwareProfile()
        self._hardware_timestamp: float = 0.0  # E1/E3: TTL cache
        self._inference_lock = Lock()
        self._cancel_event = Event()
        self._initialized = True

    # TTL for hardware detection cache (seconds)
    _HARDWARE_TTL_SEC = 60

    # ── Hardware Detection ─────────────────────────────────────────

    @staticmethod
    def detect_hardware() -> HardwareProfile:
        """
        Comprehensive hardware detection.
        Scans CPU cores, RAM, GPU backend, and VRAM for optimal configuration.
        """
        profile = HardwareProfile()

        # ── CPU Detection ──────────────────────────────────────
        if psutil is not None:
            try:
                profile.cpu_physical_cores = psutil.cpu_count(logical=False) or 1
                profile.cpu_total_cores = psutil.cpu_count(logical=True) or 1
            except Exception:
                profile.cpu_physical_cores = os.cpu_count() or 1
                profile.cpu_total_cores = profile.cpu_physical_cores
        else:
            profile.cpu_physical_cores = os.cpu_count() or 1
            profile.cpu_total_cores = profile.cpu_physical_cores

        # Physical cores perform better than hyperthreads for LLM inference.
        # Leave 1 core free for the OS/event loop.
        profile.recommended_threads = max(1, profile.cpu_physical_cores - 1)

        # ── RAM Detection ──────────────────────────────────────
        if psutil is not None:
            try:
                mem = psutil.virtual_memory()
                profile.ram_total_mb = int(mem.total / (1024 * 1024))
                profile.ram_available_mb = int(mem.available / (1024 * 1024))
            except Exception:
                profile.ram_total_mb = 0
                profile.ram_available_mb = 0
        else:
            profile.ram_total_mb = 0
            profile.ram_available_mb = 0

        # Batch size: larger = faster prefill
        if profile.ram_available_mb > 8192:
            profile.recommended_batch = 1024
        else:
            profile.recommended_batch = 512

        # ── GPU Detection ──────────────────────────────────────

        # 1. NVIDIA / CUDA (with multi-GPU support)
        if shutil.which("nvidia-smi"):
            try:
                result = subprocess.run(
                    ["nvidia-smi",
                     "--query-gpu=index,name,memory.total,memory.free",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    gpus = []
                    for line in result.stdout.strip().split("\n"):
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 4:
                            try:
                                gpus.append({
                                    "index": int(parts[0]),
                                    "name": parts[1],
                                    "vram_total_mb": int(parts[2]),
                                    "vram_free_mb": int(parts[3]),
                                })
                            except (ValueError, IndexError):
                                continue
                    if gpus:
                        # Pick GPU with most free VRAM
                        best = max(gpus, key=lambda g: g["vram_free_mb"])
                        profile.gpu_backend = "cuda"
                        profile.gpu_name = best["name"]
                        profile.gpu_index = best["index"]
                        profile.vram_total_mb = best["vram_total_mb"]
                        profile.vram_free_mb = best["vram_free_mb"]
                        profile.flash_attn_supported = True  # O2: CUDA supports flash attention
                        profile.recommended_gpu_layers = -1
                        profile.multi_gpu = len(gpus) > 1
                        profile.all_gpus = gpus

                # Extract CUDA driver version from nvidia-smi header
                header_result = subprocess.run(
                    ["nvidia-smi"], capture_output=True, text=True, timeout=5,
                )
                if header_result.returncode == 0:
                    match = re.search(r"CUDA Version:\s+(\d+\.\d+)", header_result.stdout)
                    if match:
                        profile.cuda_driver_version = match.group(1)
            except Exception:
                pass

        # 2. AMD / ROCm
        if profile.gpu_backend == "none" and (shutil.which("rocm-smi") or shutil.which("rocminfo")):
            profile.gpu_backend = "rocm"
            profile.gpu_name = "AMD GPU (ROCm)"
            profile.recommended_gpu_layers = -1
            # O2: ROCm does not reliably support flash attention in llama-cpp-python

        # 3. Apple Metal (macOS)
        if profile.gpu_backend == "none" and platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True, text=True, timeout=5,
                )
                if "Metal" in result.stdout:
                    profile.gpu_backend = "metal"
                    profile.gpu_name = "Apple Silicon (Metal)"
                    profile.recommended_gpu_layers = -1
                    profile.flash_attn_supported = True  # O2: Metal supports flash attention
            except Exception:
                pass

        # 4. Intel SYCL (oneAPI)
        # Delegates to shared intel_gpu module (single PowerShell call
        # instead of 3 separate ones, shared with gpu_detect.py).
        if profile.gpu_backend == "none":
            intel = detect_intel_gpu()
            if intel["detected"]:
                profile.intel_gpu_raw_name = intel["name"]
                profile.vram_total_mb = intel["vram_mb"]
                # B5: Intel shared GPUs allocate VRAM from system RAM.
                # Estimate free VRAM as the lesser of total VRAM and half
                # of available system RAM (the rest is needed by the OS).
                profile.vram_free_mb = min(
                    intel["vram_mb"],
                    int(profile.ram_available_mb * 0.5),
                ) if profile.ram_available_mb > 0 else intel["vram_mb"]
                profile.oneapi_available = intel["has_oneapi"]

                if intel["backend"] == "sycl":
                    profile.gpu_backend = "sycl"
                    profile.gpu_name = f"{intel['name']} (SYCL/oneAPI)"
                    profile.recommended_gpu_layers = -1
                    # O2: SYCL does not reliably support flash attention in llama-cpp-python
                elif intel["backend"] == "intel_no_oneapi":
                    profile.gpu_backend = "intel_no_oneapi"
                    profile.gpu_name = f"{intel['name']} (oneAPI not installed)"
                    profile.recommended_gpu_layers = 0
            elif intel["has_oneapi"]:
                # oneAPI installed but no GPU detected (CPU-only device)
                profile.oneapi_available = True

        # 5. Vulkan (cross-platform fallback — also for Intel GPUs without oneAPI)
        if profile.gpu_backend in ("none", "intel_no_oneapi") and shutil.which("vulkaninfo"):
            # If Intel GPU without oneAPI, Vulkan is a viable acceleration path
            if profile.gpu_backend == "intel_no_oneapi":
                # Keep VRAM info and raw name from WMI detection
                profile.gpu_backend = "vulkan"
                profile.gpu_name = f"{profile.intel_gpu_raw_name or 'Intel GPU'} (Vulkan)"
                profile.recommended_gpu_layers = -1
            else:
                profile.gpu_backend = "vulkan"
                profile.gpu_name = "Vulkan-compatible GPU"
                profile.recommended_gpu_layers = -1

        # No GPU
        if profile.gpu_backend == "none":
            profile.gpu_name = "No GPU - running on CPU"
            profile.recommended_gpu_layers = 0

        # ── Verify installed backend matches detected hardware ────────
        #
        # The hardware detection above reports what the GPU *could* use
        # (e.g. "sycl" because oneAPI is installed). But the llama-cpp-python
        # binary may have been built with a different backend (e.g. Vulkan).
        # We must reconcile hardware capability with what's actually compiled.
        gpu_check = ChatModelManager.verify_gpu_support()
        profile.installed_backend = gpu_check.get("method", "unknown")
        profile._gpu_offload_supported = gpu_check.get("gpu_offload_supported", False)

        has_hw_gpu = profile.gpu_backend not in ("none", "intel_no_oneapi")
        has_sw_gpu = gpu_check.get("gpu_offload_supported", False)

        # If the library supports GPU but was built with Vulkan (not SYCL),
        # correct the reported backend to match reality.
        if has_sw_gpu and profile.gpu_backend == "sycl":
            try:
                import llama_cpp as _lc
                pkg_dir = os.path.dirname(_lc.__file__)
                has_vulkan_dll = any(
                    "vulkan" in f.lower()
                    for _, _, files in os.walk(pkg_dir)
                    for f in files
                )
                has_sycl_dll = any(
                    "sycl" in f.lower()
                    for _, _, files in os.walk(pkg_dir)
                    for f in files
                )
                if has_vulkan_dll and not has_sycl_dll:
                    # Library built with Vulkan, not SYCL — update backend
                    raw_name = profile.intel_gpu_raw_name or "Intel GPU"
                    profile.gpu_backend = "vulkan"
                    profile.gpu_name = f"{raw_name} (Vulkan)"
            except Exception:
                pass

        if has_hw_gpu and not has_sw_gpu:
            # GPU hardware detected but no GPU offload in the installed library.
            profile.backend_mismatch = True
            if profile.oneapi_available and profile.gpu_backend == "sycl":
                profile.backend_mismatch_msg = (
                    f"GPU detected ({profile.gpu_name}) but llama-cpp-python needs to be "
                    "rebuilt with GPU support. Use 'Rebuild GPU Backend' to fix."
                )
            else:
                profile.backend_mismatch_msg = (
                    f"GPU detected ({profile.gpu_name}) but llama-cpp-python was installed "
                    "without GPU support. Delete .gpu_backend and restart Hive to fix."
                )
        elif has_hw_gpu and has_sw_gpu:
            # GPU works — no mismatch
            profile.backend_mismatch = False
            profile.backend_mismatch_msg = ""
        elif profile.gpu_backend == "intel_no_oneapi":
            profile.backend_mismatch = True
            profile.backend_mismatch_msg = (
                f"{profile.gpu_name} - Install Intel oneAPI Base Toolkit "
                "for GPU acceleration, or use 'Rebuild GPU Backend' for Vulkan."
            )

        return profile

    def _calculate_gpu_layers(
        self,
        file_size_mb: float,
        profile: HardwareProfile,
        arch_info: dict,
        n_ctx: int,
    ) -> int:
        """
        Calculate optimal GPU layers using actual model metadata.

        Uses the real block_count from GGUF metadata instead of assuming
        40 layers. Accounts for KV cache VRAM usage at the chosen context.

        Edge cases:
          - No GPU → 0
          - VRAM unknown (detection failed) → try -1 (let fallback handle OOM)
          - Model + KV cache fits entirely → -1 (all layers)
          - Model partially fits → proportional layers
          - Tiny VRAM (< 1 layer) → 0 (CPU only)
        """
        # B4: intel_no_oneapi cannot offload layers either
        if profile.gpu_backend in ("none", "intel_no_oneapi"):
            return 0

        # If VRAM is unknown, try full offload — the VRAM-aware fallback
        # in load_model() will catch OOM and retry with fewer layers.
        if profile.vram_free_mb == 0:
            return -1

        # Read actual layer count from model metadata
        block_count = arch_info.get("block_count", 0)
        if block_count == 0:
            # Metadata missing — use old heuristic as fallback
            estimated_vram_needed = file_size_mb * 1.2
            if profile.vram_free_mb >= estimated_vram_needed:
                return -1
            fraction = profile.vram_free_mb / estimated_vram_needed
            return max(1, int(32 * fraction))

        # Calculate VRAM budget
        kv_per_token = estimate_kv_cache_per_token(arch_info)
        kv_cache_mb = (kv_per_token * n_ctx) / (1024 * 1024) if kv_per_token > 0 else 0

        # Estimate per-layer weight size
        # Total weights ≈ file_size_mb, distributed across block_count + 2
        # (embedding layer + output layer + transformer blocks)
        per_layer_mb = file_size_mb / (block_count + 2) if file_size_mb > 0 else 0

        if per_layer_mb == 0:
            # Zero-size file or degenerate case — try full offload
            return -1

        # Available VRAM for model weights (after KV cache + 200 MB safety margin)
        available_for_weights = profile.vram_free_mb - kv_cache_mb - 200

        if available_for_weights <= per_layer_mb:
            return 0  # Not enough VRAM for even one layer + KV cache

        # How many layers fit?
        layers_that_fit = int(available_for_weights / per_layer_mb)

        if layers_that_fit >= block_count:
            return -1  # All layers fit
        elif layers_that_fit <= 0:
            return 0   # Nothing fits

        # Minimum useful offload: if < 5% of layers fit, the per-token
        # speedup is negligible versus the CPU-GPU transfer overhead.
        min_useful = max(1, block_count // 20)
        if layers_that_fit < min_useful:
            return 0

        return layers_that_fit

    def _calculate_optimal_context(
        self,
        file_size_mb: float,
        profile: HardwareProfile,
        arch_info: dict,
        n_gpu_layers: int,
    ) -> int:
        """
        Calculate the largest safe context size for this model + hardware.

        Reads model metadata to determine native context and KV cache cost,
        then caps based on available VRAM/RAM to prevent OOM.

        Edge cases:
          - Native context fits in memory → use native (best quality)
          - Native context too large → cap to largest safe value
          - No architecture info → fallback to 4096
          - CPU-only mode → cap based on RAM
          - VRAM detection failed → conservative 4096
        """
        native_ctx = arch_info.get("context_length", 0)
        kv_per_token = estimate_kv_cache_per_token(arch_info)

        # E2: Cap context at 32K — larger contexts waste memory for typical
        # chat use cases and dramatically slow down batch processing.
        max_practical_ctx = 32768
        native_ctx = min(native_ctx, max_practical_ctx) if native_ctx > 0 else 0

        # Fallback: no metadata available
        if native_ctx == 0 or kv_per_token == 0:
            return 4096

        # Determine available memory for KV cache
        block_count = arch_info.get("block_count", 0) or 1

        if n_gpu_layers == 0:
            # CPU-only: all weights and KV cache in RAM
            available_mb = max(0, profile.ram_available_mb - 4096)
            weight_in_pool = file_size_mb
        elif n_gpu_layers == -1 or (block_count > 0 and n_gpu_layers >= block_count):
            # Full GPU offload: weights + KV cache in VRAM
            if profile.vram_free_mb > 0:
                available_mb = max(0, profile.vram_free_mb - 300)
                weight_in_pool = file_size_mb
            else:
                return min(native_ctx, 4096)
        else:
            # Partial offload: weights split between VRAM and RAM.
            # KV cache lives wherever its layer's weights are.
            # Use BOTH memory pools — take the tighter constraint.
            gpu_fraction = n_gpu_layers / block_count if block_count > 0 else 0
            cpu_fraction = 1.0 - gpu_fraction

            gpu_weight_mb = file_size_mb * gpu_fraction
            cpu_weight_mb = file_size_mb * cpu_fraction

            # VRAM budget for GPU portion of KV cache
            vram_remaining = max(0, profile.vram_free_mb - 300 - gpu_weight_mb) if profile.vram_free_mb > 0 else 0
            # RAM budget for CPU portion of KV cache
            ram_remaining = max(0, profile.ram_available_mb - 4096 - cpu_weight_mb)

            # KV cache is also split proportionally, so each pool needs
            # to handle its fraction of the per-token KV cost.
            kv_per_token_mb = kv_per_token / (1024 * 1024)
            gpu_kv_cost = kv_per_token_mb * gpu_fraction
            cpu_kv_cost = kv_per_token_mb * cpu_fraction

            # Max tokens each pool can support
            gpu_max = int(vram_remaining / gpu_kv_cost) if gpu_kv_cost > 0 else native_ctx
            cpu_max = int(ram_remaining / cpu_kv_cost) if cpu_kv_cost > 0 else native_ctx

            # Bottleneck is the tighter constraint
            max_safe_tokens = min(gpu_max, cpu_max)
            optimal = min(native_ctx, max_safe_tokens)
            optimal = max(512, (optimal // 512) * 512)
            return optimal

        # Simple case (full CPU or full GPU): subtract weights from pool
        remaining_mb = available_mb - weight_in_pool
        if remaining_mb <= 0:
            return 512  # Bare minimum

        # How many tokens of KV cache fit in remaining memory?
        kv_per_token_mb = kv_per_token / (1024 * 1024)
        max_safe_tokens = int(remaining_mb / kv_per_token_mb) if kv_per_token_mb > 0 else native_ctx

        # Clamp to [512, native_ctx] and round down to nearest 512
        optimal = min(native_ctx, max_safe_tokens)
        optimal = max(512, (optimal // 512) * 512)

        return optimal

    # ── GPU Verification ───────────────────────────────────────────

    @staticmethod
    def verify_gpu_support() -> dict:
        """
        Check if the installed llama-cpp-python binary supports GPU offload.
        Delegates to the canonical implementation in gpu_detect.py.
        """
        from backend.gpu_detect import check_installed_gpu_support
        return check_installed_gpu_support()

    # ── Model Loading ──────────────────────────────────────────────

    def load_model(
        self,
        model_path: str,
    ) -> ModelInfo:
        """
        Load a GGUF model with fully auto-tuned parameters.

        Everything is automatic:
          - Context length: read from model metadata, capped by VRAM/RAM
          - GPU layers: calculated from actual layer count + VRAM budget
          - Threads, batch, flash_attn, mmap, mlock: from hardware profile
        """
        # B1: Deferred import — llama_cpp may not be installed on first run.
        # Importing here instead of module level prevents the server from
        # crashing if the library is missing (the rest of the app still works).
        from llama_cpp import Llama  # noqa: F811

        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")
        if not model_path.lower().endswith(".gguf"):
            raise ValueError("Only .gguf model files are supported.")

        self.unload_model()

        # ── Read model architecture from GGUF metadata ──────────
        arch_info = get_model_architecture(model_path)

        # E3: Reuse hardware profile if recently detected (within TTL)
        if time.time() - self._hardware_timestamp < self._HARDWARE_TTL_SEC \
                and self._hardware.cpu_physical_cores > 0:
            pass  # Keep existing self._hardware
        else:
            self._hardware = self.detect_hardware()
            self._hardware_timestamp = time.time()
        profile = self._hardware

        file_size_mb = os.path.getsize(model_path) / (1024 * 1024)

        # ── Auto-tune parameters ───────────────────────────────
        opt_threads = profile.recommended_threads
        opt_batch = profile.recommended_batch
        opt_flash = profile.flash_attn_supported
        opt_mmap = True
        opt_mlock = profile.ram_available_mb > (file_size_mb * 1.5 + 2048)

        # ── Calculate GPU layers + context (iterative stabilization) ──
        # The calculation is circular: GPU layers depend on context (KV cache
        # size), and context depends on GPU layers (VRAM budget). We iterate
        # until both values stabilize (typically 2-3 passes).
        preliminary_ctx = arch_info.get("context_length", 4096)
        n_gpu_layers = self._calculate_gpu_layers(
            file_size_mb, profile, arch_info, preliminary_ctx,
        )
        n_ctx = preliminary_ctx

        for _ in range(3):  # Max 3 iterations to converge
            new_ctx = self._calculate_optimal_context(
                file_size_mb, profile, arch_info, n_gpu_layers,
            )
            new_layers = self._calculate_gpu_layers(
                file_size_mb, profile, arch_info, new_ctx,
            )
            if new_ctx == n_ctx and new_layers == n_gpu_layers:
                break  # Converged
            n_ctx = new_ctx
            n_gpu_layers = new_layers

        # If we ended up at 0 GPU layers with a large context, try again
        # with a VRAM-friendly context to see if partial offload is viable.
        # The initial pass often rejects GPU because the KV cache at 32K
        # context is too large — but at 4K-8K context, partial offload fits.
        if n_gpu_layers == 0 and profile.gpu_backend not in ("none", "intel_no_oneapi"):
            for trial_ctx in (8192, 4096, 2048):
                trial_layers = self._calculate_gpu_layers(
                    file_size_mb, profile, arch_info, trial_ctx,
                )
                if trial_layers > 0:
                    n_gpu_layers = trial_layers
                    n_ctx = trial_ctx
                    # One more pass to refine
                    refined_ctx = self._calculate_optimal_context(
                        file_size_mb, profile, arch_info, n_gpu_layers,
                    )
                    refined_layers = self._calculate_gpu_layers(
                        file_size_mb, profile, arch_info, refined_ctx,
                    )
                    if refined_layers > 0:
                        n_gpu_layers = refined_layers
                        n_ctx = refined_ctx
                    break

        if arch_info.get("block_count"):
            print(f"[Hive] Model: {arch_info.get('size_label', '?')} params, "
                  f"{arch_info['block_count']} layers, "
                  f"native context {arch_info.get('context_length', '?')}")
        print(f"[Hive] Hardware: {profile.cpu_physical_cores} cores, "
              f"{profile.ram_total_mb}MB RAM, "
              f"{profile.gpu_name} ({profile.vram_free_mb}MB VRAM free)")
        print(f"[Hive] Config: n_ctx={n_ctx}, gpu_layers={n_gpu_layers}, "
              f"threads={opt_threads}, batch={opt_batch}, "
              f"flash_attn={opt_flash}, mmap={opt_mmap}, mlock={opt_mlock}")

        # ── Check if library actually supports GPU ──
        # Always do a live check here — cached values can be stale after
        # a rebuild, and the cost (~1ms) is negligible vs model load time.
        if n_gpu_layers != 0:
            gpu_check = self.verify_gpu_support()
            gpu_supported = gpu_check.get("gpu_offload_supported", False)
            if not gpu_supported:
                print(f"[Hive] ⚠ GPU layers requested but library lacks GPU support. Forcing CPU mode.")
                n_gpu_layers = 0
            else:
                print(f"[Hive] [OK] GPU offload confirmed (method: {gpu_check.get('method', 'unknown')})")
                # Update cached profile to match live check
                profile._gpu_offload_supported = True
                profile.backend_mismatch = False
                profile.backend_mismatch_msg = ""

        # ── Build attempt sequence for VRAM-aware fallback ──
        if n_gpu_layers == -1 or n_gpu_layers > 0:
            # Also try reduced context if full context fails
            reduced_ctx = max(512, n_ctx // 2)
            attempts = [
                {"n_gpu_layers": n_gpu_layers, "n_ctx": n_ctx,
                 "flash_attn": opt_flash, "use_mlock": opt_mlock,
                 "label": f"full GPU, ctx={n_ctx}"},
                {"n_gpu_layers": n_gpu_layers, "n_ctx": reduced_ctx,
                 "flash_attn": opt_flash, "use_mlock": False,
                 "label": f"full GPU, reduced ctx={reduced_ctx}"},
                {"n_gpu_layers": max(1, n_gpu_layers // 2) if n_gpu_layers > 0 else 8,
                 "n_ctx": reduced_ctx, "flash_attn": False,
                 "use_mlock": False, "label": f"partial GPU, ctx={reduced_ctx}"},
                {"n_gpu_layers": 0, "n_ctx": n_ctx, "flash_attn": False,
                 "use_mlock": False, "label": f"CPU only, ctx={n_ctx}"},
                {"n_gpu_layers": 0, "n_ctx": 4096, "flash_attn": False,
                 "use_mlock": False, "label": "CPU only, safe ctx=4096"},
            ]
        else:
            attempts = [
                {"n_gpu_layers": 0, "n_ctx": n_ctx, "flash_attn": opt_flash,
                 "use_mlock": opt_mlock, "label": f"CPU, ctx={n_ctx}"},
                {"n_gpu_layers": 0, "n_ctx": 4096, "flash_attn": False,
                 "use_mlock": False, "label": "CPU (safe mode)"},
            ]

        # ── Multi-GPU: target best GPU ──
        extra_kwargs = {}
        if profile.multi_gpu and profile.gpu_index > 0:
            extra_kwargs["main_gpu"] = profile.gpu_index
            print(f"[Hive] Multi-GPU detected. Using GPU {profile.gpu_index} ({profile.gpu_name}).")

        # ── Load model with VRAM-aware fallback ──
        start_time = time.time()
        for i, attempt in enumerate(attempts):
            try:
                print(f"[Hive] Attempting load: {attempt['label']}...")

                # B8: Build kwargs dict so flash_attn can be conditionally included.
                # Older llama-cpp-python versions don't have this parameter.
                llama_kwargs = dict(
                    model_path=model_path,
                    n_ctx=attempt["n_ctx"],
                    n_gpu_layers=attempt["n_gpu_layers"],
                    n_threads=opt_threads,
                    n_batch=opt_batch,
                    use_mmap=opt_mmap,
                    use_mlock=attempt.get("use_mlock", False),
                    verbose=False,
                    **extra_kwargs,
                )
                if attempt.get("flash_attn", False):
                    llama_kwargs["flash_attn"] = True

                try:
                    self._model = Llama(**llama_kwargs)
                except TypeError as te:
                    # flash_attn not supported by this version — retry without it
                    if "flash_attn" in str(te) and "flash_attn" in llama_kwargs:
                        print(f"[Hive] flash_attn not supported by this llama-cpp-python version, retrying without...")
                        llama_kwargs.pop("flash_attn", None)
                        attempt["flash_attn"] = False
                        self._model = Llama(**llama_kwargs)
                    else:
                        raise

                # Success — update config to reflect what actually worked
                n_gpu_layers = attempt["n_gpu_layers"]
                n_ctx = attempt["n_ctx"]
                opt_flash = attempt.get("flash_attn", False)
                opt_mlock = attempt.get("use_mlock", False)
                break
            except Exception as e:
                print(f"[Hive] {attempt['label']} failed: {e}")
                if i == len(attempts) - 1:
                    raise  # Last attempt, propagate error
                continue

        load_duration = time.time() - start_time

        # Read actual context size from the loaded model
        actual_n_ctx = self._model.n_ctx()

        self._info = ModelInfo(
            path=model_path,
            filename=os.path.basename(model_path),
            size_mb=round(file_size_mb, 1),
            n_ctx=actual_n_ctx,
            n_gpu_layers=n_gpu_layers,
            gpu_backend=profile.gpu_backend,
            loaded=True,
            load_time_sec=round(load_duration, 2),
            n_threads=opt_threads,
            n_batch=opt_batch,
            flash_attn=opt_flash,
            use_mmap=opt_mmap,
            use_mlock=opt_mlock,
        )

        print(f"[Hive] Model loaded in {load_duration:.1f}s — all systems optimized.")
        return self._info

    def unload_model(self):
        """Unload the current model and free GPU/system memory."""
        self.cancel_generation()
        if self._model is not None:
            del self._model
            self._model = None
            # E7: Force garbage collection so CUDA/Vulkan/Metal releases
            # GPU memory immediately instead of waiting for GC cycle.
            gc.collect()
        self._info = ModelInfo()

    # ── Cancellation ───────────────────────────────────────────────

    def cancel_generation(self):
        """Signal the running inference to stop immediately."""
        self._cancel_event.set()

    # ── Inference ──────────────────────────────────────────────────

    # Sentinel yielded before prompt evaluation so the UI can show
    # "Thinking..." instead of appearing frozen.  Contains null bytes
    # that never appear in natural model output.
    THINKING_SENTINEL = "\x00THINKING\x00"

    # Timeout for acquiring the inference lock. If a previous generation is
    # stuck inside llama-cpp, we don't want new generations to block forever.
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
        CRITICAL: _cancel_event.clear() is INSIDE the lock to prevent
        a race condition where a stale cancel from a previous generation
        would immediately terminate this one.

        Uses a timeout on lock acquisition so new generations aren't
        blocked forever when a previous one is stuck inside llama-cpp.
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
            # Clear cancel INSIDE the lock — after any previous generation
            # has fully released. This prevents the race condition where:
            # 1. Previous gen is cancelled → event.set()
            # 2. New gen calls event.clear() OUTSIDE lock
            # 3. Previous gen hasn't exited yet, so it sets event again
            # 4. New gen acquires lock → event is set → immediately breaks
            self._cancel_event.clear()

            # Signal that prompt evaluation is starting — the consumer
            # forwards this to the frontend as a "Thinking..." status.
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
            # If inference fails, make sure lock is released cleanly
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

        Previous implementation used asyncio.Queue with fire-and-forget
        asyncio.run_coroutine_threadsafe(queue.put(...)), which leaked
        coroutines when the consumer stopped reading (disconnect/cancel).
        Now uses queue.Queue which blocks correctly in the producer thread
        and is polled from the async side.
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
                        # Consumer is gone (disconnected). Stop producing.
                        break
            except Exception as exc:
                try:
                    q.put(exc, timeout=5.0)
                except _stdlib_queue.Full:
                    pass
            finally:
                # Always send sentinel so consumer doesn't hang.
                for _ in range(50):  # ~5 seconds of retries
                    try:
                        q.put(None, timeout=0.1)
                        break
                    except _stdlib_queue.Full:
                        continue
                producer_done.set()

        future = loop.run_in_executor(None, _produce)
        consumer_interrupted = True  # Assume interrupted until normal exit

        try:
            while True:
                # Poll the stdlib queue from the async side.
                try:
                    item = await asyncio.to_thread(q.get, timeout=0.1)
                except _stdlib_queue.Empty:
                    # No token yet — check if producer is done
                    if producer_done.is_set():
                        # Drain any remaining items
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
            # Only signal cancel if consumer exited early (disconnect,
            # cancel, error). On normal completion the producer is already
            # done — setting cancel here would create a race that could
            # immediately cancel the NEXT generation.
            if consumer_interrupted:
                self._cancel_event.set()
            # Drain the queue so the producer's q.put() doesn't block
            while not q.empty():
                try:
                    q.get_nowait()
                except _stdlib_queue.Empty:
                    break

    # ── Status ─────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return current model status including performance config."""
        return {
            "loaded": self._info.loaded,
            "filename": self._info.filename,
            "path": self._info.path,
            "size_mb": self._info.size_mb,
            "n_ctx": self._info.n_ctx,
            "n_gpu_layers": self._info.n_gpu_layers,
            "gpu_backend": self._info.gpu_backend,
            "load_time_sec": self._info.load_time_sec,
            "n_threads": self._info.n_threads,
            "n_batch": self._info.n_batch,
            "flash_attn": self._info.flash_attn,
            "use_mmap": self._info.use_mmap,
            "use_mlock": self._info.use_mlock,
        }

    def get_hardware_info(self) -> dict:
        """Return full hardware profile for the UI System section.

        Uses cached profile if available and fresh (within TTL).
        Callers that need to force re-detection should call
        detect_hardware() first and assign to self._hardware.
        """
        # E1: Re-detect if stale or never populated
        if time.time() - self._hardware_timestamp > self._HARDWARE_TTL_SEC \
                or self._hardware.cpu_physical_cores <= 1:
            self._hardware = self.detect_hardware()
            self._hardware_timestamp = time.time()

        p = self._hardware
        return {
            "cpu_physical_cores": p.cpu_physical_cores,
            "cpu_total_cores": p.cpu_total_cores,
            "ram_total_mb": p.ram_total_mb,
            "ram_available_mb": p.ram_available_mb,
            "gpu_backend": p.gpu_backend,
            "gpu_name": p.gpu_name,
            "vram_total_mb": p.vram_total_mb,
            "vram_free_mb": p.vram_free_mb,
            "multi_gpu": p.multi_gpu,
            "all_gpus": p.all_gpus,
            "cuda_driver_version": p.cuda_driver_version,
            "backend_mismatch": p.backend_mismatch,
            "backend_mismatch_msg": p.backend_mismatch_msg,
            "flash_attn_supported": p.flash_attn_supported,
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
