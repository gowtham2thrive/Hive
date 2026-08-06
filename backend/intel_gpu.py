"""
intel_gpu.py — Shared Intel GPU detection utilities.

Single source of truth for all Intel-specific GPU detection logic.
Used by both model_manager (runtime hardware profiling) and
gpu_detect.py (pre-install backend recommendation).

STDLIB ONLY — no llama_cpp or third-party dependencies — so this
module can be imported before llama-cpp-python is installed.
"""

import os
import platform
import re
import shutil
import subprocess
from typing import Optional


# ── oneAPI Binary Discovery ────────────────────────────────────────


def find_oneapi_binary(binary_name: str) -> Optional[str]:
    """Search PATH and known Intel oneAPI install directories for a binary.

    oneAPI doesn't add itself to PATH by default on Windows — it requires
    running setvars.bat. This function searches known install locations
    directly so detection works without manual environment setup.
    """
    # Check PATH first
    found = shutil.which(binary_name)
    if found:
        return found

    # Known Intel oneAPI install locations on Windows
    search_dirs = [
        r"C:\Program Files (x86)\Intel\oneAPI\compiler",
        r"C:\Program Files\Intel\oneAPI\compiler",
    ]
    for base_dir in search_dirs:
        if not os.path.isdir(base_dir):
            continue
        # Walk version subdirectories (e.g., 2026.1, latest)
        try:
            for version_dir in os.listdir(base_dir):
                bin_path = os.path.join(base_dir, version_dir, "bin", binary_name)
                # Try with and without .exe extension on Windows
                for candidate in [bin_path, bin_path + ".exe"]:
                    if os.path.isfile(candidate):
                        return candidate
        except OSError:
            continue
    return None


# ── Windows WMI / Registry Helpers ─────────────────────────────────


def _run_cmd(cmd: list[str], timeout: int = 5) -> Optional[subprocess.CompletedProcess]:
    """Run a command safely, returning None on any failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


def _detect_intel_via_wmi_combined() -> dict:
    """Detect Intel GPU name and VRAM in a SINGLE PowerShell call.

    Combines three previously separate subprocess calls into one:
    1. GPU name from Win32_VideoController
    2. 64-bit VRAM from registry (HardwareInformation.qwMemorySize)
    3. Fallback 32-bit VRAM from WMI AdapterRAM

    Returns dict with keys: name, vram_mb
    """
    result = {"name": "", "vram_mb": 0}

    if platform.system() != "Windows":
        return result

    # Single combined PowerShell command that returns:
    #   GPU_NAME|REGISTRY_VRAM_BYTES|WMI_ADAPTER_RAM_BYTES
    ps_script = (
        "$gpu = Get-CimInstance Win32_VideoController | "
        "Where-Object { $_.Name -like '*Intel*' -and "
        "($_.Name -like '*Arc*' -or $_.Name -like '*Iris*' -or "
        "$_.Name -like '*Xe*' -or $_.Name -like '*UHD*' -or "
        "$_.Name -like '*Graphics*') } | Select-Object -First 1; "
        "if (-not $gpu) { exit 1 }; "
        "$name = $gpu.Name; "
        "$adapterRam = $gpu.AdapterRAM; "
        # Registry path for 64-bit VRAM (accurate for modern GPUs)
        "$regVram = 0; "
        "Get-ChildItem "
        "'HKLM:\\SYSTEM\\ControlSet001\\Control\\Class"
        "\\{4d36e968-e325-11ce-bfc1-08002be10318}' "
        "-EA SilentlyContinue | ForEach-Object { "
        "$p = Get-ItemProperty $_.PSPath -EA SilentlyContinue; "
        "if ($p.DriverDesc -and $p.DriverDesc -eq $name) { "
        "$regVram = $p.'HardwareInformation.qwMemorySize' "
        "} }; "
        "Write-Output \"$name|$regVram|$adapterRam\""
    )

    proc = _run_cmd(
        ["powershell", "-NoProfile", "-Command", ps_script],
        timeout=8,
    )
    if not proc or proc.returncode != 0 or not proc.stdout.strip():
        return result

    line = proc.stdout.strip().splitlines()[0]
    parts = line.split("|", 2)
    if len(parts) < 3:
        return result

    name = parts[0].strip()
    reg_vram_str = parts[1].strip()
    adapter_ram_str = parts[2].strip()

    result["name"] = name

    # Prefer 64-bit registry VRAM (accurate for GPUs > 4 GB)
    if reg_vram_str and reg_vram_str != "0":
        try:
            vram_bytes = int(reg_vram_str)
            if vram_bytes > 0:
                result["vram_mb"] = vram_bytes // (1024 * 1024)
        except (ValueError, TypeError):
            pass

    # Fallback to WMI AdapterRAM (32-bit, caps at ~4 GB)
    if result["vram_mb"] == 0 and adapter_ram_str:
        try:
            vram_bytes = int(adapter_ram_str)
            if vram_bytes > 0:
                result["vram_mb"] = vram_bytes // (1024 * 1024)
        except (ValueError, TypeError):
            pass

    return result


# ── sycl-ls Detection ──────────────────────────────────────────────


def _detect_intel_via_sycl_ls() -> dict:
    """Detect Intel GPU via sycl-ls (requires oneAPI installed).

    Returns dict with keys: name, has_oneapi, detected
    """
    result = {"name": "", "has_oneapi": False, "detected": False}

    sycl_ls_path = find_oneapi_binary("sycl-ls")
    if not sycl_ls_path:
        # Check for icx compiler as secondary oneAPI indicator
        if find_oneapi_binary("icx"):
            result["has_oneapi"] = True
        return result

    proc = _run_cmd([sycl_ls_path], timeout=5)
    if proc is None:
        # sycl-ls binary exists but crashed — still means oneAPI installed
        result["has_oneapi"] = True
        return result

    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if "Intel" in line and "gpu" in line.lower():
                # sycl-ls lines look like:
                # [opencl:gpu][opencl:0] Intel(R) OpenCL Graphics, Intel(R) Arc(TM) ...
                raw = line.strip()
                if "," in raw:
                    name = raw.rsplit(",", 1)[-1].strip()
                elif "]" in raw:
                    name = raw.rsplit("]", 1)[-1].strip()
                else:
                    name = raw[:120]
                # Strip OpenCL driver suffix like "OpenCL 3.0 NEO [32.0.101.8801]"
                name = re.sub(r'\s+OpenCL\s+[\d.]+.*$', '', name).strip()
                result["name"] = name or raw[:80]
                result["has_oneapi"] = True
                result["detected"] = True
                break

        # sycl-ls found but no GPU line matched — oneAPI installed, maybe CPU-only
        if not result["detected"]:
            result["has_oneapi"] = True
            # Check for icx as secondary confirmation
            if find_oneapi_binary("icx"):
                result["has_oneapi"] = True

    return result


# ── xpu-smi Detection ─────────────────────────────────────────────


def _detect_intel_via_xpu_smi() -> dict:
    """Detect Intel GPU via xpu-smi tool.

    Returns dict with keys: name, detected
    """
    result = {"name": "", "detected": False}

    if not shutil.which("xpu-smi"):
        return result

    proc = _run_cmd(["xpu-smi", "discovery"], timeout=5)
    if proc and proc.returncode == 0 and "Intel" in (proc.stdout or ""):
        for line in proc.stdout.splitlines():
            if "Device Name" in line:
                result["name"] = line.split(":")[-1].strip()[:80]
                result["detected"] = True
                break

    return result


# ── Main Detection Orchestrator ────────────────────────────────────


def detect_intel_gpu() -> dict:
    """Full Intel GPU detection: name, VRAM, oneAPI status, recommended backend.

    Detection priority:
    1. sycl-ls (native oneAPI tool, most reliable when available)
    2. xpu-smi (Intel GPU management tool)
    3. Windows WMI (works without any Intel SDK installed)

    VRAM is always enriched via the Windows registry (64-bit accurate)
    when available, regardless of which detection method found the GPU.

    Returns:
        dict with keys:
        - name: str — Clean GPU name (e.g., "Intel(R) Arc(TM) A530M Graphics")
        - vram_mb: int — VRAM in megabytes (0 if unknown)
        - has_oneapi: bool — Whether Intel oneAPI toolkit is available
        - backend: str — Recommended backend: "sycl", "intel_no_oneapi", or ""
        - detected: bool — Whether any Intel GPU was found
    """
    result = {
        "name": "",
        "vram_mb": 0,
        "has_oneapi": False,
        "backend": "",
        "detected": False,
    }

    # ── Step 1: Try sycl-ls (best method when oneAPI is installed) ──
    sycl_info = _detect_intel_via_sycl_ls()
    if sycl_info["has_oneapi"]:
        result["has_oneapi"] = True
    if sycl_info["detected"]:
        result["name"] = sycl_info["name"]
        result["detected"] = True

    # ── Step 2: Try xpu-smi ──
    if not result["detected"]:
        xpu_info = _detect_intel_via_xpu_smi()
        if xpu_info["detected"]:
            result["name"] = xpu_info["name"]
            result["detected"] = True
            # Check oneAPI availability separately
            if not result["has_oneapi"] and find_oneapi_binary("icx"):
                result["has_oneapi"] = True

    # ── Step 3: Try Windows WMI + Registry (works without oneAPI) ──
    if platform.system() == "Windows":
        wmi_info = _detect_intel_via_wmi_combined()

        if not result["detected"] and wmi_info["name"]:
            result["name"] = wmi_info["name"]
            result["detected"] = True

        # Always enrich VRAM from WMI/registry if we have a GPU
        # (sycl-ls doesn't report VRAM, so this fills the gap)
        if result["detected"]:
            # Prefer WMI name over sycl-ls raw name (cleaner)
            if wmi_info["name"]:
                result["name"] = wmi_info["name"]
            if wmi_info["vram_mb"] > 0:
                result["vram_mb"] = wmi_info["vram_mb"]

    # ── Step 4: Determine backend ──
    if result["detected"]:
        if result["has_oneapi"]:
            result["backend"] = "sycl"
        else:
            result["backend"] = "intel_no_oneapi"

    return result
