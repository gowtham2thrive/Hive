"""
gguf_meta.py — Lightweight GGUF metadata reader.

Parses only the metadata header from a .gguf file (no tensor weights)
to extract model architecture info needed for auto-tuning:
  - block_count (number of transformer layers)
  - context_length (native max context)
  - head_count / head_count_kv (attention heads)
  - embedding_length (hidden dimension)

STDLIB ONLY — no dependencies.
"""

import struct
from typing import Optional


# GGUF value type codes
_GGUF_TYPES = {
    0: ("uint8", "B", 1),
    1: ("int8", "b", 1),
    2: ("uint16", "H", 2),
    3: ("int16", "h", 2),
    4: ("uint32", "I", 4),
    5: ("int32", "i", 4),
    6: ("float32", "f", 4),
    7: ("bool", "?", 1),
    8: ("string", None, None),  # Special handling
    9: ("array", None, None),   # Special handling
    10: ("uint64", "Q", 8),
    11: ("int64", "q", 8),
    12: ("float64", "d", 8),
}


def read_gguf_metadata(filepath: str) -> dict:
    """Read metadata key-value pairs from a GGUF file header.

    Only reads the metadata section — does NOT load tensor weights,
    so it's fast even for multi-GB model files (~1ms).

    Returns a dict of metadata keys to their values.
    Raises ValueError if the file is not a valid GGUF file.
    """
    metadata = {}

    with open(filepath, "rb") as f:
        # ── Magic number ──
        magic = f.read(4)
        if magic != b"GGUF":
            raise ValueError(f"Not a GGUF file (magic: {magic!r})")

        # ── Version ──
        version = struct.unpack("<I", f.read(4))[0]
        if version not in (2, 3):
            raise ValueError(f"Unsupported GGUF version: {version}")

        # ── Counts ──
        tensor_count = struct.unpack("<Q", f.read(8))[0]
        metadata_kv_count = struct.unpack("<Q", f.read(8))[0]

        # ── Read metadata KV pairs ──
        for _ in range(metadata_kv_count):
            key = _read_string(f)
            value = _read_value(f)
            metadata[key] = value

    return metadata


def _read_string(f) -> str:
    """Read a GGUF string (uint64 length + bytes)."""
    length = struct.unpack("<Q", f.read(8))[0]
    return f.read(length).decode("utf-8", errors="replace")


def _read_value(f):
    """Read a typed GGUF value."""
    value_type = struct.unpack("<I", f.read(4))[0]
    return _read_typed_value(f, value_type)


def _read_typed_value(f, value_type: int):
    """Read a value of the given GGUF type."""
    if value_type == 8:  # string
        return _read_string(f)
    elif value_type == 9:  # array
        elem_type = struct.unpack("<I", f.read(4))[0]
        count = struct.unpack("<Q", f.read(8))[0]
        return [_read_typed_value(f, elem_type) for _ in range(count)]
    else:
        type_info = _GGUF_TYPES.get(value_type)
        if type_info is None:
            raise ValueError(f"Unknown GGUF value type: {value_type}")
        _, fmt, size = type_info
        return struct.unpack(f"<{fmt}", f.read(size))[0]


# ── High-Level Helpers ─────────────────────────────────────────────


def get_model_architecture(filepath: str) -> dict:
    """Extract architecture info needed for auto-tuning.

    Returns a dict with:
      - arch: str (e.g., "qwen2", "llama", "phi3")
      - block_count: int (number of transformer layers)
      - context_length: int (native max context in tokens)
      - embedding_length: int (hidden dimension)
      - head_count: int (attention heads)
      - head_count_kv: int (KV attention heads, for GQA/MQA)
      - size_label: str (e.g., "0.5B", "8B")

    Any missing field defaults to 0 or empty string.
    """
    try:
        meta = read_gguf_metadata(filepath)
    except (ValueError, OSError, struct.error):
        return _empty_architecture()

    # Detect architecture name (e.g., "qwen2", "llama", "phi3")
    arch = str(meta.get("general.architecture", ""))

    result = {
        "arch": arch,
        "block_count": _int_val(meta, f"{arch}.block_count"),
        "context_length": _int_val(meta, f"{arch}.context_length"),
        "embedding_length": _int_val(meta, f"{arch}.embedding_length"),
        "head_count": _int_val(meta, f"{arch}.attention.head_count"),
        "head_count_kv": _int_val(meta, f"{arch}.attention.head_count_kv"),
        "size_label": str(meta.get("general.size_label", "")),
    }

    return result


def estimate_kv_cache_per_token(arch_info: dict) -> float:
    """Estimate KV cache memory per token in bytes.

    Formula:
      kv_per_token = 2 (K+V) × n_layers × n_kv_heads × head_dim × 2 (fp16 bytes)

    Where head_dim = embedding_length / head_count.

    Returns 0 if architecture info is missing or incomplete.
    """
    n_layers = arch_info.get("block_count", 0)
    n_kv_heads = arch_info.get("head_count_kv", 0)
    embed_dim = arch_info.get("embedding_length", 0)
    n_heads = arch_info.get("head_count", 0)

    if not all([n_layers, n_kv_heads, embed_dim, n_heads]):
        return 0.0

    head_dim = embed_dim / n_heads
    # 2 (K+V matrices) × layers × kv_heads × head_dim × 2 bytes (fp16)
    kv_per_token = 2 * n_layers * n_kv_heads * head_dim * 2
    return kv_per_token


def _int_val(meta: dict, key: str) -> int:
    """Safely extract an integer value from metadata."""
    val = meta.get(key, 0)
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _empty_architecture() -> dict:
    """Return empty architecture info as fallback."""
    return {
        "arch": "",
        "block_count": 0,
        "context_length": 0,
        "embedding_length": 0,
        "head_count": 0,
        "head_count_kv": 0,
        "size_label": "",
    }
