"""
gpu_detect.py — Standalone GPU detection and install recommendation.

Detects available GPU hardware and recommends the optimal llama-cpp-python
backend. Uses only stdlib — no llama_cpp dependency — so it can run BEFORE
the library is installed.

Usage:
    python gpu_detect.py           # Human-readable console output
    python gpu_detect.py --json    # Machine-readable JSON for start.bat
    python gpu_detect.py --check   # Verify installed llama_cpp has GPU support
"""

import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    from backend.intel_gpu import (
        find_oneapi_binary as _find_oneapi_binary,
        detect_intel_gpu,
    )
except ImportError:
    # Standalone CLI execution: python backend/gpu_detect.py
    from intel_gpu import (  # type: ignore[import-untyped]
        find_oneapi_binary as _find_oneapi_binary,
        detect_intel_gpu,
    )


# ── Constants ──────────────────────────────────────────────────────

# Map CUDA driver version → best available pre-built wheel slug
CUDA_WHEEL_MAP = {
    "12.4": "cu124",
    "12.5": "cu124",
    "12.6": "cu124",
    "12.7": "cu124",
    "12.8": "cu124",
    "12.3": "cu123",
    "12.2": "cu122",
    "12.1": "cu121",
}

WHEEL_BASE_URL = "https://abetlen.github.io/llama-cpp-python/whl"


# ── Data Classes ───────────────────────────────────────────────────


@dataclass
class GPUInfo:
    """Information about a single detected GPU."""
    index: int = 0
    name: str = ""
    vram_total_mb: int = 0
    vram_free_mb: int = 0
    vendor: str = ""  # nvidia, intel, amd, apple


@dataclass
class DetectionResult:
    """Complete GPU detection and install recommendation."""
    recommended_backend: str = "cpu"
    install_method: str = "prebuilt_wheel"  # prebuilt_wheel | source_build | default
    cuda_version: str = ""                  # e.g., "cu124"
    gpu_name: str = ""
    gpu_index: int = 0
    vram_total_mb: int = 0
    vram_free_mb: int = 0
    multi_gpu: bool = False
    all_gpus: list = field(default_factory=list)
    install_command: str = ""
    can_build_from_source: bool = False
    fallback_backend: str = "cpu"
    fallback_install_command: str = ""
    cuda_driver_version: str = ""
    needs_build_tools: bool = False  # True if build tools need to be installed first
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# ── Utility Functions ──────────────────────────────────────────────


def run_cmd(cmd: list[str], timeout: int = 10) -> Optional[subprocess.CompletedProcess]:
    """Run a command safely, returning None on any failure."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def _get_local_mingw_bin() -> Optional[str]:
    """Return path to .build_tools/mingw64/bin if it contains g++."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mingw_bin = os.path.join(project_root, ".build_tools", "mingw64", "bin")
    if os.path.isfile(os.path.join(mingw_bin, "g++.exe")):
        return mingw_bin
    return None


def check_build_tools() -> bool:
    """Check if CMake + C++ compiler are available for source builds."""
    # Check cmake on PATH, in pip Scripts, and in local .build_tools
    has_cmake = shutil.which("cmake") is not None
    if not has_cmake:
        scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
        if os.path.isfile(os.path.join(scripts_dir, "cmake.exe")):
            has_cmake = True
    if not has_cmake:
        mingw_bin = _get_local_mingw_bin()
        if mingw_bin and os.path.isfile(os.path.join(mingw_bin, "cmake.exe")):
            has_cmake = True

    # Check for any C++ compiler (system or local MinGW)
    has_compiler = any(
        shutil.which(cc) is not None
        for cc in ["cl", "g++", "clang++", "c++"]
    )
    if not has_compiler:
        has_compiler = _get_local_mingw_bin() is not None

    return has_cmake and has_compiler


def get_cpu_install_command() -> str:
    """Return the pip command for CPU-only llama-cpp-python."""
    return (
        f"pip install llama-cpp-python "
        f"--extra-index-url {WHEEL_BASE_URL}/cpu "
        f"--upgrade --no-cache-dir --quiet"
    )


# ── NVIDIA / CUDA Detection ───────────────────────────────────────


def detect_nvidia() -> Optional[DetectionResult]:
    """Detect NVIDIA GPUs and determine best CUDA wheel."""
    if not shutil.which("nvidia-smi"):
        return None

    # Query GPU info (may return multiple GPUs)
    result = run_cmd([
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.free",
        "--format=csv,noheader,nounits",
    ], timeout=5)

    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None

    gpus = []
    for line in result.stdout.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            try:
                gpus.append(GPUInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    vram_total_mb=int(parts[2]),
                    vram_free_mb=int(parts[3]),
                    vendor="nvidia",
                ))
            except (ValueError, IndexError):
                continue

    if not gpus:
        return None

    # Pick GPU with most free VRAM
    best_gpu = max(gpus, key=lambda g: g.vram_free_mb)

    # Get CUDA driver version from nvidia-smi header
    header_result = run_cmd(["nvidia-smi"], timeout=5)
    cuda_driver_version = ""
    cuda_wheel = ""

    if header_result and header_result.returncode == 0:
        match = re.search(r"CUDA Version:\s+(\d+\.\d+)", header_result.stdout)
        if match:
            cuda_driver_version = match.group(1)

            # Find best matching wheel
            # Try exact match first, then try major.minor truncation
            cuda_wheel = CUDA_WHEEL_MAP.get(cuda_driver_version, "")

            # If no exact match, try matching on major version
            if not cuda_wheel:
                major_minor = cuda_driver_version.split(".")
                if len(major_minor) >= 1 and major_minor[0] == "12":
                    # Any CUDA 12.x we don't have a specific match for → use cu124
                    cuda_wheel = "cu124"
                # CUDA 11.x or older — no pre-built wheel available

    det = DetectionResult(
        recommended_backend="cuda",
        gpu_name=best_gpu.name,
        gpu_index=best_gpu.index,
        vram_total_mb=best_gpu.vram_total_mb,
        vram_free_mb=best_gpu.vram_free_mb,
        multi_gpu=len(gpus) > 1,
        all_gpus=[asdict(g) for g in gpus],
        cuda_driver_version=cuda_driver_version,
        can_build_from_source=check_build_tools(),
    )

    if cuda_wheel:
        det.cuda_version = cuda_wheel
        det.install_method = "prebuilt_wheel"
        det.install_command = (
            f"pip install llama-cpp-python "
            f"--extra-index-url {WHEEL_BASE_URL}/{cuda_wheel} "
            f"--upgrade --force-reinstall --no-cache-dir --quiet"
        )
        det.fallback_backend = "vulkan" if shutil.which("vulkaninfo") and det.can_build_from_source else "cpu"
    else:
        # CUDA too old for pre-built wheels
        det.warnings.append(
            f"CUDA driver version {cuda_driver_version} is too old for pre-built wheels (need 12.1+). "
            f"Update your NVIDIA drivers for GPU acceleration."
        )
        if det.can_build_from_source:
            det.install_method = "source_build"
            det.install_command = (
                "pip install llama-cpp-python "
                "--upgrade --force-reinstall --no-cache-dir --quiet"
            )
            # Set CMAKE_ARGS in environment before running this command
            det.warnings.append("Source build with CUDA requires CMAKE_ARGS=\"-DGGML_CUDA=on\"")
            det.fallback_backend = "cpu"
        else:
            # Can't build from source and no wheel — fall through
            det.recommended_backend = "cpu"
            det.install_method = "default"
            det.install_command = get_cpu_install_command()
            det.fallback_backend = "cpu"

    # Set fallback install command
    if det.fallback_backend == "vulkan" and det.can_build_from_source:
        det.fallback_install_command = (
            "pip install llama-cpp-python "
            "--upgrade --force-reinstall --no-cache-dir --quiet"
        )
    elif det.fallback_backend == "cpu":
        det.fallback_install_command = get_cpu_install_command()

    return det


# ── Intel / SYCL Detection ────────────────────────────────────────


def detect_intel() -> Optional[DetectionResult]:
    """Detect Intel GPUs and recommend install method.

    Delegates low-level detection (sycl-ls, xpu-smi, WMI, registry)
    to the shared intel_gpu module. This function only handles the
    DetectionResult packaging and install command logic.
    """
    intel = detect_intel_gpu()

    if not intel["detected"]:
        return None

    gpu_name = intel["name"]
    vram_mb = intel["vram_mb"]
    has_oneapi = intel["has_oneapi"]
    has_sycl_compiler = _find_oneapi_binary("icx") is not None
    can_build = check_build_tools() and has_sycl_compiler

    det = DetectionResult(
        gpu_name=gpu_name,
        vram_total_mb=vram_mb,
        can_build_from_source=can_build,
    )

    if has_oneapi:
        det.recommended_backend = "sycl"
        det.install_method = "source_build"
        det.install_command = (
            "pip install llama-cpp-python "
            "--upgrade --force-reinstall --no-cache-dir --quiet"
        )
        det.warnings.append(
            "SYCL build requires: "
            "set CMAKE_ARGS=-DGGML_SYCL=on -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx"
        )
        if not can_build:
            det.needs_build_tools = True
            det.warnings.append(
                "Build tools (CMake + C++ compiler) will be installed automatically."
            )
        det.fallback_backend = "vulkan" if check_build_tools() else "cpu"
    else:
        # Intel GPU found but no oneAPI — suggest Vulkan or CPU
        det.recommended_backend = "cpu"  # Can't use SYCL without oneAPI
        det.install_method = "default"
        det.install_command = get_cpu_install_command()

        det.warnings.append(
            f"Intel GPU detected ({gpu_name}) but Intel oneAPI Toolkit not found. "
            "Install oneAPI from https://intel.com/oneapi for GPU acceleration."
        )

        # Try Vulkan as alternative for Intel GPUs
        if shutil.which("vulkaninfo"):
            det.recommended_backend = "vulkan"
            det.install_method = "source_build"
            det.install_command = (
                "pip install llama-cpp-python "
                "--upgrade --force-reinstall --no-cache-dir --quiet"
            )
            if not check_build_tools():
                det.needs_build_tools = True
                det.warnings.append("Build tools (CMake + C++ compiler) will be installed automatically.")
            det.warnings.append("Vulkan build requires: set CMAKE_ARGS=-DGGML_VULKAN=on")
            det.fallback_backend = "cpu"
            det.fallback_install_command = get_cpu_install_command()
        else:
            det.fallback_backend = "cpu"
            det.fallback_install_command = get_cpu_install_command()

    return det


# ── Apple Metal Detection ─────────────────────────────────────────


def detect_apple_metal() -> Optional[DetectionResult]:
    """Detect Apple Silicon (Metal) support."""
    if platform.system() != "Darwin":
        return None

    is_arm = platform.machine() == "arm64"

    # Check for Metal support via system_profiler
    result = run_cmd(["system_profiler", "SPDisplaysDataType"], timeout=5)
    has_metal = result and result.returncode == 0 and "Metal" in result.stdout

    if not has_metal:
        return None

    gpu_name = "Apple Silicon (Metal)" if is_arm else "Apple GPU (Metal)"

    # Try to extract specific chip name
    if result:
        for line in result.stdout.splitlines():
            line = line.strip()
            if "Chip" in line or "Chipset Model" in line:
                chip = line.split(":")[-1].strip()
                if chip:
                    gpu_name = f"{chip} (Metal)"
                    break

    det = DetectionResult(
        recommended_backend="metal",
        install_method="prebuilt_wheel",
        gpu_name=gpu_name,
        install_command=(
            f"pip install llama-cpp-python "
            f"--extra-index-url {WHEEL_BASE_URL}/metal "
            f"--upgrade --force-reinstall --no-cache-dir --quiet"
        ),
        fallback_backend="cpu",
        fallback_install_command=get_cpu_install_command(),
    )

    if not is_arm:
        det.warnings.append(
            "Intel Mac detected. Metal support may be limited. "
            "Apple Silicon (M1/M2/M3/M4) is recommended for best performance."
        )

    return det


# ── AMD / ROCm Detection ──────────────────────────────────────────


def detect_amd_rocm() -> Optional[DetectionResult]:
    """Detect AMD GPUs with ROCm support."""
    has_rocm = shutil.which("rocm-smi") or shutil.which("rocminfo")
    if not has_rocm:
        return None

    gpu_name = "AMD GPU (ROCm)"
    vram_total = 0
    vram_free = 0

    # Try to get GPU name and VRAM from rocm-smi
    result = run_cmd(["rocm-smi", "--showproductname"], timeout=5)
    if result and result.returncode == 0:
        for line in result.stdout.splitlines():
            if "Card" in line or "GPU" in line:
                # Extract name from output
                parts = line.split(":")
                if len(parts) >= 2:
                    gpu_name = parts[-1].strip() or gpu_name
                    break

    # Try VRAM detection
    vram_result = run_cmd(["rocm-smi", "--showmeminfo", "vram"], timeout=5)
    if vram_result and vram_result.returncode == 0:
        for line in vram_result.stdout.splitlines():
            if "Total" in line:
                match = re.search(r"(\d+)", line)
                if match:
                    # rocm-smi reports in bytes typically
                    val = int(match.group(1))
                    if val > 1_000_000:
                        vram_total = val // (1024 * 1024)
                    else:
                        vram_total = val  # Already in MB
            elif "Used" in line:
                match = re.search(r"(\d+)", line)
                if match:
                    val = int(match.group(1))
                    used = val // (1024 * 1024) if val > 1_000_000 else val
                    vram_free = max(0, vram_total - used)

    can_build = check_build_tools()
    det = DetectionResult(
        gpu_name=gpu_name,
        vram_total_mb=vram_total,
        vram_free_mb=vram_free,
        can_build_from_source=can_build,
    )

    # ROCm is Linux-only for llama-cpp-python source builds
    if platform.system() == "Linux" and can_build:
        det.recommended_backend = "rocm"
        det.install_method = "source_build"
        det.install_command = (
            "pip install llama-cpp-python "
            "--upgrade --force-reinstall --no-cache-dir --quiet"
        )
        det.warnings.append("ROCm build requires: CMAKE_ARGS=\"-DGGML_HIPBLAS=on\"")
        det.fallback_backend = "cpu"
        det.fallback_install_command = get_cpu_install_command()
    else:
        # Windows AMD → try Vulkan
        if shutil.which("vulkaninfo") and can_build:
            det.recommended_backend = "vulkan"
            det.install_method = "source_build"
            det.install_command = (
                "pip install llama-cpp-python "
                "--upgrade --force-reinstall --no-cache-dir --quiet"
            )
            det.warnings.append("Vulkan build requires: set CMAKE_ARGS=-DGGML_VULKAN=on")
        else:
            det.recommended_backend = "cpu"
            det.install_method = "default"
            det.install_command = get_cpu_install_command()
        det.fallback_backend = "cpu"
        det.fallback_install_command = get_cpu_install_command()

    return det


# ── Vulkan Detection ──────────────────────────────────────────────


def detect_vulkan() -> Optional[DetectionResult]:
    """Detect Vulkan-capable GPUs as a cross-platform fallback."""
    if not shutil.which("vulkaninfo"):
        return None

    result = run_cmd(["vulkaninfo", "--summary"], timeout=5)
    if result is None or result.returncode != 0:
        # Try without --summary (older versions)
        result = run_cmd(["vulkaninfo"], timeout=10)

    if result is None or result.returncode != 0:
        return None

    # Check for compute queue support (needed for inference, not just graphics)
    has_compute = "COMPUTE" in result.stdout.upper() or "compute" in result.stdout

    # Extract GPU name
    gpu_name = "Vulkan-compatible GPU"
    for line in result.stdout.splitlines():
        if "deviceName" in line or "GPU" in line:
            parts = line.split("=")
            if len(parts) >= 2:
                gpu_name = parts[-1].strip()[:80]
                break

    can_build = check_build_tools()

    det = DetectionResult(
        recommended_backend="vulkan",
        install_method="source_build",
        gpu_name=gpu_name,
        install_command=(
            "pip install llama-cpp-python "
            "--upgrade --force-reinstall --no-cache-dir --quiet"
        ),
        can_build_from_source=can_build,
        needs_build_tools=not can_build,
        fallback_backend="cpu",
        fallback_install_command=get_cpu_install_command(),
    )

    if not can_build:
        det.warnings.append("Build tools (CMake + C++ compiler) will be installed automatically.")

    det.warnings.append("Vulkan build requires: set CMAKE_ARGS=-DGGML_VULKAN=on")

    if not has_compute:
        det.warnings.append(
            "Vulkan compute queue not detected. GPU inference may not work. "
            "Falling back to CPU may be necessary."
        )

    return det


# ── Post-Install Verification ─────────────────────────────────────


def check_installed_gpu_support() -> dict:
    """
    Check if the currently installed llama-cpp-python supports GPU offload.
    Uses multiple detection methods for robustness across library versions.
    """
    result = {
        "installed": False,
        "gpu_offload_supported": False,
        "method": "none",
        "error": None,
    }

    try:
        import llama_cpp
        result["installed"] = True

        # Method 1: Direct module-level function (newer versions)
        if hasattr(llama_cpp, "llama_supports_gpu_offload"):
            result["gpu_offload_supported"] = bool(llama_cpp.llama_supports_gpu_offload())
            result["method"] = "direct_api"
            return result

        # Method 2: Shared library function (older versions)
        try:
            from llama_cpp.llama_cpp import _load_shared_library
            lib = _load_shared_library("llama")
            if hasattr(lib, "llama_supports_gpu_offload"):
                result["gpu_offload_supported"] = bool(lib.llama_supports_gpu_offload())
                result["method"] = "shared_library"
                return result
        except Exception:
            pass

        # Method 3: Scan package directory for GPU-related shared libraries
        try:
            pkg_dir = os.path.dirname(llama_cpp.__file__)
            gpu_indicators = ["cublas", "cuda", "metal", "vulkan", "sycl", "opencl", "hipblas"]
            for root, _dirs, files in os.walk(pkg_dir):
                for f in files:
                    if any(ind in f.lower() for ind in gpu_indicators):
                        result["gpu_offload_supported"] = True
                        result["method"] = "dll_scan"
                        return result
        except Exception:
            pass

        result["method"] = "exhausted"

    except ImportError:
        result["error"] = "llama_cpp not installed"
    except Exception as e:
        result["error"] = str(e)

    return result


# ── Main Detection Pipeline ───────────────────────────────────────


def detect_best_backend() -> DetectionResult:
    """
    Run full GPU detection pipeline in priority order.
    Returns the best recommendation or CPU fallback.

    Priority: NVIDIA CUDA → Apple Metal → Intel SYCL → AMD ROCm → Vulkan → CPU
    """
    # Check Python is 64-bit (llama-cpp-python requires it)
    is_64bit = struct.calcsize("P") * 8 == 64

    # Try each backend in priority order
    detectors = [
        ("NVIDIA/CUDA", detect_nvidia),
        ("Apple/Metal", detect_apple_metal),
        ("Intel/SYCL", detect_intel),
        ("AMD/ROCm", detect_amd_rocm),
        ("Vulkan", detect_vulkan),
    ]

    for name, detector in detectors:
        try:
            result = detector()
            if result and result.recommended_backend != "cpu":
                if not is_64bit:
                    result.warnings.append(
                        "32-bit Python detected. GPU acceleration requires 64-bit Python."
                    )
                    result.recommended_backend = "cpu"
                    result.install_method = "default"
                    result.install_command = get_cpu_install_command()
                return result
        except Exception as e:
            # Never let a detection error crash the whole pipeline
            pass

    # CPU fallback
    cpu_result = DetectionResult(
        recommended_backend="cpu",
        install_method="default",
        gpu_name="No GPU - running on CPU",
        install_command=get_cpu_install_command(),
        can_build_from_source=check_build_tools(),
    )

    if not is_64bit:
        cpu_result.warnings.append(
            "32-bit Python detected. Install 64-bit Python for better performance."
        )

    return cpu_result


# ── CLI Interface ──────────────────────────────────────────────────


def print_human_readable(det: DetectionResult):
    """Print detection results in a human-readable format."""
    print()
    print("  +=========================================+")
    print("  |       Hive GPU Detection Report          |")
    print("  +=========================================+")
    print()

    backend_labels = {
        "cuda": "NVIDIA CUDA",
        "metal": "Apple Metal",
        "sycl": "Intel SYCL (oneAPI)",
        "rocm": "AMD ROCm",
        "vulkan": "Vulkan",
        "cpu": "CPU (no GPU acceleration)",
    }

    label = backend_labels.get(det.recommended_backend, det.recommended_backend.upper())
    print(f"  Recommended Backend : {label}")
    print(f"  GPU                 : {det.gpu_name or 'None'}")

    if det.vram_total_mb > 0:
        print(f"  VRAM                : {det.vram_total_mb} MB total, {det.vram_free_mb} MB free")

    if det.cuda_version:
        print(f"  CUDA Wheel          : {det.cuda_version}")

    if det.cuda_driver_version:
        print(f"  CUDA Driver         : {det.cuda_driver_version}")

    if det.multi_gpu:
        print(f"  Multi-GPU           : Yes ({len(det.all_gpus)} GPUs, using GPU {det.gpu_index})")

    print(f"  Install Method      : {det.install_method}")
    print(f"  Build Tools         : {'Available' if det.can_build_from_source else 'Not found'}")

    if det.fallback_backend != "cpu" or det.recommended_backend != "cpu":
        print(f"  Fallback            : {det.fallback_backend}")

    if det.warnings:
        print()
        for w in det.warnings:
            print(f"  [!] {w}")

    if det.errors:
        print()
        for e in det.errors:
            print(f"  [X] {e}")

    print()


# ── Build Tools Installation ──────────────────────────────────────


def get_winget_path() -> Optional[str]:
    """Find winget executable, checking PATH and common Windows install locations."""
    # Check PATH first
    winget = shutil.which("winget")
    if winget:
        return winget

    # Check common WindowsApps location
    user_home = os.path.expanduser("~")
    winget_path = os.path.join(user_home, "AppData", "Local", "Microsoft", "WindowsApps", "winget.exe")
    if os.path.isfile(winget_path):
        return winget_path

    return None


def is_winget_available() -> bool:
    """Check if winget (Windows Package Manager) is available."""
    winget = get_winget_path()
    if not winget:
        return False
    result = run_cmd([winget, "--version"], timeout=5)
    return result is not None and result.returncode == 0


def write_build_log(label: str, proc: subprocess.CompletedProcess) -> None:
    """Append build output to build_log.txt for debugging failed builds."""
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(project_root, "build_log.txt")
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            import datetime
            f.write(f"\n{'='*72}\n")
            f.write(f"[{datetime.datetime.now().isoformat()}] {label}\n")
            f.write(f"Exit code: {proc.returncode}\n")
            f.write(f"{'─'*72}\n")
            if proc.stdout:
                f.write("STDOUT:\n")
                f.write(proc.stdout[-5000:] if len(proc.stdout) > 5000 else proc.stdout)
                f.write("\n")
            if proc.stderr:
                f.write("STDERR:\n")
                f.write(proc.stderr[-5000:] if len(proc.stderr) > 5000 else proc.stderr)
                f.write("\n")
            f.write(f"{'='*72}\n")
    except Exception:
        pass  # Never let logging break the build flow


def find_vcvars() -> Optional[str]:
    """Find the Visual Studio vcvarsall.bat script to set up the MSVC environment."""
    vs_paths = [
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvarsall.bat",
    ]

    for path in vs_paths:
        if os.path.isfile(path):
            return path

    # Try vswhere as a fallback
    vswhere = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    if os.path.isfile(vswhere):
        result = run_cmd([
            vswhere, "-latest", "-property", "installationPath",
            "-products", "*", "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        ], timeout=10)
        if result and result.returncode == 0 and result.stdout.strip():
            vcvars = os.path.join(result.stdout.strip(), "VC", "Auxiliary", "Build", "vcvarsall.bat")
            if os.path.isfile(vcvars):
                return vcvars

    return None


def install_vs_build_tools() -> dict:
    """
    Install Visual Studio 2022 Build Tools with C++ workload.
    Required for SYCL builds (icx/icpx need MSVC's linker).

    Tries:
      1. winget via PowerShell (most reliable on Windows 10/11)
      2. Direct download of VS Build Tools installer from Microsoft

    Returns dict with 'success', 'method', and 'error' keys.
    """
    result = {"success": False, "method": "none", "error": None}

    # Already installed?
    vcvars = find_vcvars()
    if vcvars:
        result["success"] = True
        result["method"] = "already_installed"
        return result

    # ── Method 1: winget via PowerShell ──────────────────────────────
    print("  [*] Installing Visual Studio Build Tools via winget...")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "winget install --id Microsoft.VisualStudio.2022.BuildTools "
             "--override '--wait --passive "
             "--add Microsoft.VisualStudio.Workload.VCTools "
             "--includeRecommended' "
             "--accept-source-agreements --accept-package-agreements"],
            capture_output=True, text=True, timeout=3600,  # 60 min max
        )
        if proc.returncode == 0:
            # Verify it actually installed
            vcvars = find_vcvars()
            if vcvars:
                print(f"  [OK] VS Build Tools installed via winget ({vcvars})")
                result["success"] = True
                result["method"] = "winget"
                return result
            else:
                print("  [!] winget reported success but vcvarsall.bat not found")
                print("  [!] VS Build Tools may need a system restart to register")
        else:
            stderr_short = (proc.stderr or proc.stdout or "")[-300:]
            print(f"  [!] winget install failed (exit {proc.returncode})")
            write_build_log("VS Build Tools (winget)", proc)
    except subprocess.TimeoutExpired:
        print("  [!] winget install timed out after 60 minutes")
    except FileNotFoundError:
        print("  [!] PowerShell not found")
    except Exception as e:
        print(f"  [!] winget error: {e}")

    # ── Method 2: Direct download via PowerShell ─────────────────────
    print("  [*] Trying direct download of VS Build Tools installer...")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(project_root, ".build_tools")
    os.makedirs(tools_dir, exist_ok=True)
    installer_path = os.path.join(tools_dir, "vs_buildtools.exe")

    download_url = "https://aka.ms/vs/17/release/vs_BuildTools.exe"

    try:
        dl_cmd = (
            f"$ProgressPreference = 'SilentlyContinue'; "
            f"Invoke-WebRequest -Uri '{download_url}' "
            f"-OutFile '{installer_path}' -UseBasicParsing"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", dl_cmd],
            capture_output=True, text=True, timeout=600,
        )

        if proc.returncode != 0 or not os.path.isfile(installer_path):
            result["error"] = "Failed to download VS Build Tools installer"
            return result

        file_size = os.path.getsize(installer_path)
        if file_size < 500_000:  # Should be at least ~4 MB
            result["error"] = "Downloaded file too small — may be corrupted"
            try:
                os.remove(installer_path)
            except OSError:
                pass
            return result

        print(f"  [OK] Downloaded ({file_size // (1024*1024)} MB). Running installer...")

        # Run silent install with C++ workload
        proc = subprocess.run(
            [installer_path, "--wait", "--passive", "--norestart",
             "--add", "Microsoft.VisualStudio.Workload.VCTools",
             "--includeRecommended"],
            capture_output=True, text=True, timeout=3600,
        )
        write_build_log("VS Build Tools (direct installer)", proc)

        # Clean up installer
        try:
            if os.path.isfile(installer_path):
                os.remove(installer_path)
        except OSError:
            pass

        if proc.returncode == 0 or proc.returncode == 3010:
            # 3010 = success, reboot required
            vcvars = find_vcvars()
            if vcvars:
                print(f"  [OK] VS Build Tools installed ({vcvars})")
                result["success"] = True
                result["method"] = "direct_download"
                return result
            elif proc.returncode == 3010:
                result["error"] = (
                    "VS Build Tools installed but requires a system restart. "
                    "Please restart your computer and try again."
                )
                return result
            else:
                result["error"] = (
                    "Installer completed but vcvarsall.bat not found. "
                    "Try restarting and running again."
                )
                return result
        else:
            result["error"] = (
                f"VS Build Tools installer exited with code {proc.returncode}. "
                "Check build_log.txt for details."
            )
            return result

    except subprocess.TimeoutExpired:
        result["error"] = "VS Build Tools installer timed out after 60 minutes"
    except Exception as e:
        result["error"] = f"VS Build Tools install error: {e}"
    finally:
        try:
            if os.path.isfile(installer_path):
                os.remove(installer_path)
        except OSError:
            pass

    return result


def install_build_tools() -> dict:
    """
    Install build tools needed for Vulkan source build.
    Uses zero-admin methods: pip for cmake, w64devkit for C++ compiler.
    Returns a dict with installation results.
    """
    results = {"success": True, "installed": [], "failed": [], "skipped": []}

    # 1. CMake via pip (no admin needed)
    cmake_path = shutil.which("cmake")
    if cmake_path:
        print(f"  [OK] CMake already installed ({cmake_path})")
        results["skipped"].append("CMake")
    else:
        print("  [*] Installing CMake via pip...")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "cmake", "--quiet"],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode == 0:
                print("  [OK] CMake installed successfully")
                results["installed"].append("CMake")
            else:
                print(f"  [!] CMake install failed: {proc.stderr[:200]}")
                results["failed"].append("CMake")
                results["success"] = False
        except Exception as e:
            print(f"  [!] CMake install error: {e}")
            results["failed"].append(f"CMake ({e})")
            results["success"] = False

    # 2. C++ Compiler: check MSVC first, then w64devkit
    vcvars = find_vcvars()
    has_gcc = shutil.which("g++") or shutil.which("gcc")

    if vcvars:
        print(f"  [OK] MSVC compiler found ({vcvars})")
        results["skipped"].append("C++ Compiler (MSVC)")
    elif has_gcc:
        print(f"  [OK] GCC compiler found ({has_gcc})")
        results["skipped"].append("C++ Compiler (GCC)")
    else:
        # Download w64devkit (portable MinGW, no admin needed)
        print("  [*] Downloading portable C++ compiler (MinGW-w64)...")
        mingw_result = download_mingw()
        if mingw_result:
            print(f"  [OK] MinGW-w64 installed to {mingw_result}")
            results["installed"].append("MinGW-w64 (portable GCC)")
        else:
            print("  [!] Failed to download MinGW-w64.")
            print("      Install Visual Studio Build Tools manually, or")
            print("      download MinGW from: https://winlibs.com")
            results["failed"].append("C++ Compiler")
            results["success"] = False

    # 3. Vulkan SDK (headers, import lib, glslc)
    existing_sdk = get_vulkan_sdk_dir()
    if existing_sdk:
        print(f"  [OK] Vulkan SDK already available ({existing_sdk})")
        results["skipped"].append("Vulkan SDK")
    elif shutil.which("vulkaninfo"):
        print("  [*] Setting up portable Vulkan SDK...")
        sdk_dir = setup_vulkan_sdk()
        if sdk_dir:
            results["installed"].append("Vulkan SDK (portable)")
        else:
            print("  [!] Vulkan SDK setup failed")
            results["failed"].append("Vulkan SDK")
            results["success"] = False
    else:
        print("  [!] Vulkan runtime not found - GPU may not have Vulkan support")
        results["failed"].append("Vulkan Runtime")
        results["success"] = False

    return results


def download_mingw() -> Optional[str]:
    """
    Download and extract WinLibs MinGW-w64 (portable GCC C++ compiler).
    Uses plain .zip files — no admin, no installer, no UAC.
    Extracts to project_root/.build_tools/mingw64/
    Returns the bin directory path if successful, None otherwise.
    """
    import urllib.request
    import zipfile

    # Target directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    tools_dir = os.path.join(project_root, ".build_tools")
    mingw_bin = os.path.join(tools_dir, "mingw64", "bin")

    # Already extracted?
    if os.path.isfile(os.path.join(mingw_bin, "g++.exe")):
        os.environ["PATH"] = mingw_bin + os.pathsep + os.environ.get("PATH", "")
        return mingw_bin

    os.makedirs(tools_dir, exist_ok=True)

    # Get latest WinLibs release URL from GitHub API
    download_url = None
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/brechtsanders/winlibs_mingw/releases/latest",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            import json as _json
            data = _json.loads(resp.read())
            for asset in data.get("assets", []):
                name = asset["name"]
                # Get the x86_64 UCRT zip (not LLVM, not i686)
                if ("x86_64" in name and name.endswith(".zip")
                        and "llvm" not in name.lower() and "i686" not in name):
                    download_url = asset["browser_download_url"]
                    break
    except Exception as e:
        print(f"  [!] Could not fetch latest release: {e}")

    if not download_url:
        # Fallback to known version
        download_url = (
            "https://github.com/brechtsanders/winlibs_mingw/releases/download/"
            "16.1.0posix-14.0.0-ucrt-r3/"
            "winlibs-x86_64-posix-seh-gcc-16.1.0-mingw-w64ucrt-14.0.0-r3.zip"
        )
        print("  [*] Using known release URL as fallback")

    zip_path = os.path.join(tools_dir, "mingw64.zip")

    try:
        print("  [*] Downloading MinGW-w64 GCC (~260 MB)...")
        print("  [*] This is a one-time download for GPU compilation support.")
        urllib.request.urlretrieve(download_url, zip_path)

        print("  [*] Extracting (this may take a minute)...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tools_dir)

        # Clean up zip
        try:
            os.remove(zip_path)
        except OSError:
            pass

        # Check if extraction worked
        if os.path.isfile(os.path.join(mingw_bin, "g++.exe")):
            os.environ["PATH"] = mingw_bin + os.pathsep + os.environ.get("PATH", "")
            return mingw_bin

        # Search for it in subdirectories
        for name in os.listdir(tools_dir):
            candidate = os.path.join(tools_dir, name, "bin", "g++.exe")
            if os.path.isfile(candidate):
                bin_dir = os.path.dirname(candidate)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
                return bin_dir

        print("  [!] Extraction completed but g++.exe not found in expected location")

    except Exception as e:
        print(f"  [!] Download/extract failed: {e}")
        if os.path.isfile(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass

    return None


def get_mingw_bin() -> Optional[str]:
    """Get the MinGW-w64 bin directory if it exists."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    mingw_bin = os.path.join(project_root, ".build_tools", "mingw64", "bin")
    if os.path.isfile(os.path.join(mingw_bin, "g++.exe")):
        return mingw_bin
    return None


def get_vulkan_sdk_dir() -> Optional[str]:
    """Get the local Vulkan SDK directory if fully set up (headers + lib + glslc)."""
    # Check env var first (official SDK install)
    env_sdk = os.environ.get("VULKAN_SDK")
    if env_sdk and os.path.isdir(env_sdk):
        # Verify it has what we need
        if (os.path.isfile(os.path.join(env_sdk, "Include", "vulkan", "vulkan.h"))
                and (os.path.isfile(os.path.join(env_sdk, "Bin", "glslc.exe"))
                     or os.path.isfile(os.path.join(env_sdk, "Bin", "glslangValidator.exe")))):
            return env_sdk

    # Check our portable setup
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    sdk_dir = os.path.join(project_root, ".build_tools", "vulkan_sdk")
    if (os.path.isfile(os.path.join(sdk_dir, "Include", "vulkan", "vulkan.h"))
            and os.path.isfile(os.path.join(sdk_dir, "Lib", "vulkan-1.lib"))
            and (os.path.isfile(os.path.join(sdk_dir, "Bin", "glslc.exe"))
                 or os.path.isfile(os.path.join(sdk_dir, "Bin", "glslangValidator.exe")))):
        return sdk_dir
    return None


def setup_vulkan_sdk() -> Optional[str]:
    """
    Set up a minimal Vulkan SDK for building llama-cpp-python.
    Downloads:
    - Vulkan headers from KhronosGroup/Vulkan-Headers GitHub
    - Creates vulkan-1.lib from system DLL using MinGW tools
    - Downloads glslc shader compiler from Google shaderc releases
    All zero-admin, no installer needed.
    Returns the SDK directory path if successful, None otherwise.
    """
    import urllib.request
    import zipfile

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    tools_dir = os.path.join(project_root, ".build_tools")
    sdk_dir = os.path.join(tools_dir, "vulkan_sdk")
    include_dir = os.path.join(sdk_dir, "Include")
    lib_dir = os.path.join(sdk_dir, "Lib")
    bin_dir = os.path.join(sdk_dir, "Bin")

    # Already set up?
    if (os.path.isfile(os.path.join(include_dir, "vulkan", "vulkan.h"))
            and os.path.isfile(os.path.join(lib_dir, "vulkan-1.lib"))
            and (os.path.isfile(os.path.join(bin_dir, "glslc.exe"))
                 or os.path.isfile(os.path.join(bin_dir, "glslangValidator.exe")))):
        os.environ["VULKAN_SDK"] = sdk_dir
        return sdk_dir

    os.makedirs(include_dir, exist_ok=True)
    os.makedirs(lib_dir, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)

    # ── Step 1: Download Vulkan headers ──────────────────────────────
    if not os.path.isfile(os.path.join(include_dir, "vulkan", "vulkan.h")):
        print("  [*] Downloading Vulkan headers...")
        try:
            # Get latest Vulkan-Headers release
            headers_url = None
            try:
                req = urllib.request.Request(
                    "https://api.github.com/repos/KhronosGroup/Vulkan-Headers/releases/latest",
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    import json as _json
                    data = _json.loads(resp.read())
                    tag = data["tag_name"]
                    headers_url = f"https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/{tag}.zip"
            except Exception:
                headers_url = "https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/v1.4.318.zip"

            zip_path = os.path.join(tools_dir, "vulkan_headers.zip")
            urllib.request.urlretrieve(headers_url, zip_path)

            with zipfile.ZipFile(zip_path, "r") as zf:
                # Extract only the include/vulkan directory
                for member in zf.namelist():
                    # Files are like: Vulkan-Headers-v1.4.318/include/vulkan/vulkan.h
                    if "/include/" in member and not member.endswith("/"):
                        # Strip the top-level directory
                        parts = member.split("/include/", 1)
                        if len(parts) == 2:
                            target = os.path.join(include_dir, parts[1].replace("/", os.sep))
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(member) as src, open(target, "wb") as dst:
                                dst.write(src.read())

            os.remove(zip_path)

            if os.path.isfile(os.path.join(include_dir, "vulkan", "vulkan.h")):
                print("  [OK] Vulkan headers installed")
            else:
                print("  [!] Vulkan headers extraction failed")
                return None
        except Exception as e:
            print(f"  [!] Failed to download Vulkan headers: {e}")
            return None

    # ── Step 2: Create vulkan-1.lib from system DLL ──────────────────
    if not os.path.isfile(os.path.join(lib_dir, "vulkan-1.lib")):
        print("  [*] Creating Vulkan import library...")
        try:
            # Find vulkan-1.dll in system
            vulkan_dll = None
            for search_dir in [r"C:\Windows\System32", r"C:\Windows\SysWOW64"]:
                candidate = os.path.join(search_dir, "vulkan-1.dll")
                if os.path.isfile(candidate):
                    vulkan_dll = candidate
                    break

            if not vulkan_dll:
                print("  [!] vulkan-1.dll not found in system")
                return None

            # Use MinGW's gendef + dlltool to create import library
            mingw_bin = get_mingw_bin()
            if not mingw_bin:
                print("  [!] MinGW not found - cannot create import library")
                return None

            gendef = os.path.join(mingw_bin, "gendef.exe")
            dlltool = os.path.join(mingw_bin, "dlltool.exe")

            lib_created = False

            if os.path.isfile(gendef) and os.path.isfile(dlltool):
                # Best path: generate .def from DLL, then create .lib
                def_path = os.path.join(lib_dir, "vulkan-1.def")
                proc = subprocess.run(
                    [gendef, "-", vulkan_dll],
                    capture_output=True, text=True, timeout=30,
                )
                if proc.returncode == 0 and proc.stdout:
                    with open(def_path, "w") as f:
                        f.write(proc.stdout)

                    # Create .lib from .def
                    proc = subprocess.run(
                        [dlltool, "-d", def_path, "-l",
                         os.path.join(lib_dir, "vulkan-1.lib"),
                         "-D", "vulkan-1.dll"],
                        capture_output=True, text=True, timeout=30,
                    )
                    lib_created = os.path.isfile(os.path.join(lib_dir, "vulkan-1.lib"))

            # E5: If gendef/dlltool are missing or failed, copy the DLL directly.
            # CMake's FindVulkan will accept it as a fallback import library.
            if not lib_created:
                import shutil as _shutil
                _shutil.copy2(vulkan_dll, os.path.join(lib_dir, "vulkan-1.lib"))
                print("  [OK] Vulkan library set up (DLL copy fallback)")
            else:
                print("  [OK] Vulkan import library created")

        except Exception as e:
            print(f"  [!] Failed to create Vulkan import library: {e}")
            return None

    # ── Step 3: Download glslang shader compiler ───────────────────────
    if not (os.path.isfile(os.path.join(bin_dir, "glslc.exe")) or os.path.isfile(os.path.join(bin_dir, "glslangValidator.exe"))):
        print("  [*] Downloading glslangValidator shader compiler from KhronosGroup...")
        try:
            glslang_url = "https://github.com/KhronosGroup/glslang/releases/download/master-tot/glslang-master-windows-x64-Release.zip"
            zip_path = os.path.join(tools_dir, "glslang.zip")

            req = urllib.request.Request(glslang_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(zip_path, "wb") as f:
                    f.write(resp.read())

            print("  [*] Extracting glslangValidator...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    basename = member.split("/")[-1]
                    if basename.lower() == "glslangvalidator.exe":
                        data = zf.read(member)
                        target = os.path.join(bin_dir, "glslangValidator.exe")
                        with open(target, "wb") as f:
                            f.write(data)

            # Clean up the zip
            os.remove(zip_path)

            if os.path.isfile(os.path.join(bin_dir, "glslangValidator.exe")):
                print("  [OK] glslang shader compiler installed")
            else:
                print("  [!] glslangValidator not found in archive")
                return None

        except Exception as e:
            print(f"  [!] Failed to download glslangValidator: {e}")
            # Clean up partial download
            if os.path.isfile(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            return None

    # Set environment variable
    os.environ["VULKAN_SDK"] = sdk_dir
    print(f"  [OK] Vulkan SDK ready at {sdk_dir}")
    return sdk_dir


def build_vulkan_llama() -> dict:
    """
    Build llama-cpp-python from source with Vulkan support.
    Auto-detects compiler: MSVC (vcvarsall) or MinGW (w64devkit/system GCC).
    Returns a dict with build results.
    """
    result = {"success": False, "error": None, "gpu_offload": False}

    # Find cmake - check pip-installed location too
    cmake_path = shutil.which("cmake")
    if not cmake_path:
        # Check pip Scripts directory
        scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
        candidate = os.path.join(scripts_dir, "cmake.exe")
        if os.path.isfile(candidate):
            os.environ["PATH"] = scripts_dir + os.pathsep + os.environ.get("PATH", "")
            cmake_path = candidate
        else:
            result["error"] = "CMake not found. Run: pip install cmake"
            print(f"  [!] {result['error']}")
            return result

    print(f"  [OK] CMake found: {cmake_path}")

    # Detect compiler: MSVC or MinGW
    vcvars = find_vcvars()
    w64_bin = get_mingw_bin()
    has_system_gcc = shutil.which("g++")

    env = os.environ.copy()
    use_cmd_wrapper = False

    # Set up VULKAN_SDK if our portable SDK is available
    vulkan_sdk = get_vulkan_sdk_dir()
    if not vulkan_sdk:
        vulkan_sdk = setup_vulkan_sdk()
    if vulkan_sdk:
        env["VULKAN_SDK"] = vulkan_sdk
        print(f"  [OK] Vulkan SDK: {vulkan_sdk}")
    else:
        print("  [!] Vulkan SDK not found - build may fail")

    env["CMAKE_ARGS"] = "-DGGML_VULKAN=on"

    if vcvars:
        # MSVC path: use vcvarsall.bat
        print("  [OK] Using MSVC compiler")
        use_cmd_wrapper = True
        pip_cmd = (
            f'"{sys.executable}" -m pip install llama-cpp-python '
            f'--upgrade --force-reinstall --no-cache-dir'
        )
        full_cmd = f'call "{vcvars}" x64 && set CMAKE_ARGS=-DGGML_VULKAN=on && {pip_cmd}'
    elif w64_bin or has_system_gcc:
        # MinGW path: use w64devkit or system GCC
        compiler = w64_bin or os.path.dirname(shutil.which("g++"))
        print(f"  [OK] Using MinGW GCC compiler ({compiler})")

        # Ensure w64devkit is on PATH
        if w64_bin and w64_bin not in env.get("PATH", ""):
            env["PATH"] = w64_bin + os.pathsep + env.get("PATH", "")

        # Force MinGW Makefiles generator (otherwise cmake defaults to VS)
        env["CMAKE_ARGS"] = "-DGGML_VULKAN=on"
        env["CMAKE_GENERATOR"] = "MinGW Makefiles"
        env["FORCE_CMAKE"] = "1"
    else:
        result["error"] = "No C++ compiler found. Run: python backend/gpu_detect.py --install-tools"
        print(f"  [!] {result['error']}")
        return result

    print("  [*] Building llama-cpp-python with Vulkan support...")
    print("  [*] This will take 5-15 minutes. Please be patient...")

    try:
        if use_cmd_wrapper:
            # Must pass as string (not list) so Python doesn't re-quote
            # the compound command. Lists cause double-quoting on paths
            # with spaces, which breaks cmd.exe parsing.
            proc = subprocess.run(
                f'cmd /c {full_cmd}',
                capture_output=True, text=True, timeout=1200,
                shell=True,
            )
        else:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "llama-cpp-python",
                 "--upgrade", "--force-reinstall", "--no-cache-dir"],
                capture_output=True, text=True, timeout=1200,
                env=env,
            )

        if proc.returncode == 0:
            print("  [OK] llama-cpp-python built with Vulkan support!")
            result["success"] = True

            # Verify GPU offload works
            gpu_check = check_installed_gpu_support()
            result["gpu_offload"] = gpu_check.get("gpu_offload_supported", False)

            if result["gpu_offload"]:
                print("  [OK] GPU offload verified - your GPU will be used!")
            else:
                print("  [!] Build succeeded but GPU offload not detected.")
                print("      This may still work - try loading a model.")
        else:
            # Extract meaningful error
            stderr = proc.stderr or ""
            stdout = proc.stdout or ""
            combined = stderr + stdout

            if "cmake" in combined.lower() and "not found" in combined.lower():
                result["error"] = "CMake not found in PATH. Try: pip install cmake"
            elif "cl.exe" in combined.lower() or "compiler" in combined.lower():
                result["error"] = "C++ compiler error. Run: python backend/gpu_detect.py --install-tools"
            elif "vulkan" in combined.lower() and "not found" in combined.lower():
                result["error"] = "Vulkan SDK headers not found."
            elif "mingw" in combined.lower() and "error" in combined.lower():
                result["error"] = "MinGW build error. Check w64devkit installation."
            else:
                result["error"] = "Build failed"

            print(f"  [!] Build failed: {result['error']}")
            # Print last few lines of output for debugging
            output_lines = combined.strip().split("\n")
            if output_lines:
                print("  --- Last 10 lines of build output ---")
                for line in output_lines[-10:]:
                    line = line.strip()
                    if line:
                        print(f"  {line}")

    except subprocess.TimeoutExpired:
        result["error"] = "Build timed out after 20 minutes"
        print(f"  [!] {result['error']}")
    except Exception as e:
        result["error"] = str(e)
        print(f"  [!] Build failed: {e}")

    return result


def main():
    args = sys.argv[1:]

    if "--check" in args:
        # Post-install GPU support verification
        result = check_installed_gpu_support()
        if "--json" in args:
            print(json.dumps(result, indent=2))
        else:
            if not result["installed"]:
                print("  [!] llama-cpp-python is not installed")
            elif result["gpu_offload_supported"]:
                print(f"  [OK] GPU offload supported (detected via {result['method']})")
            else:
                print("  [!] GPU offload NOT supported in current install")
                print("      Delete .gpu_backend and restart Hive to re-detect")
        sys.exit(0 if result.get("gpu_offload_supported") else 1)

    if "--install-tools" in args:
        # Install build tools (CMake, VS Build Tools, Vulkan SDK)
        print()
        print("  [*] Installing build tools for Vulkan GPU support...")
        print()
        result = install_build_tools()
        if "--json" in args:
            print(json.dumps(result, indent=2))
        sys.exit(0 if result["success"] else 1)

    if "--build-vulkan" in args:
        # Build llama-cpp-python with Vulkan support
        print()
        result = build_vulkan_llama()
        if "--json" in args:
            print(json.dumps(result, indent=2))
        sys.exit(0 if result["success"] else 1)

    # Run full detection
    det = detect_best_backend()

    if "--json" in args:
        print(json.dumps(asdict(det), indent=2))
    else:
        print_human_readable(det)


if __name__ == "__main__":
    main()
