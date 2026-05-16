"""Hardware autodetect for the VoxReach POC.

Run before launching services. Detects the GPU via nvidia-smi, maps it to a
known profile, and writes tuned env vars to /workspace/.voxreach-hw.env that
all services source on startup.

Usage:
    python3 detect_hw.py [--out PATH] [--quiet]

Exits non-zero with a clear message if:
- nvidia-smi is missing
- no GPU detected
- detected GPU is in our "blocked" list (will not give acceptable demo latency)

If the GPU is unknown (not in our table), we don't fail — we apply conservative
defaults and warn loudly. The user can override any of the written vars by
exporting them before sourcing the env file.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Tuned profiles per GPU. Lookup is substring-based against the nvidia-smi
# product name (case-insensitive).
#
# Fields:
#   name           — human label
#   vram_gb        — declared VRAM (sanity-check only)
#   torch_arch     — TORCH_CUDA_ARCH_LIST value (semicolon-separated capabilities)
#   alloc_conf     — PYTORCH_CUDA_ALLOC_CONF (or empty)
#   extract_timeout — VOXREACH_EXTRACT_TIMEOUT seconds for Gemma/Qwen extractor
#   ollama_model   — recommended VOXREACH_ORDER_MODEL
#   warn           — non-empty warning to print (and embed in env file) for marginal cards
#   block          — non-empty reason to ABORT for unsuitable cards
PROFILES = [
    {
        "match": "A100",
        "name": "NVIDIA A100",
        "vram_gb": 40,  # 40 or 80 — both work
        "torch_arch": "8.0",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 30,
        "ollama_model": "qwen3.5:4b",
        "warn": "",
        "block": "",
    },
    {
        "match": "L40",
        "name": "NVIDIA L40",
        "vram_gb": 48,
        "torch_arch": "8.9",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 45,
        "ollama_model": "qwen3.5:4b",
        "warn": (
            "L40 has ~1/3 the FP16 throughput of A100. Moshi's 80ms audio frame "
            "budget is tight on this card; expect occasional response slowness, "
            "not glitches. Set MOSHI_NO_COMPILE=1 if warmup hangs."
        ),
        "block": "",
    },
    {
        "match": "L4",  # Watch for "L40" matching first; order matters
        "name": "NVIDIA L4",
        "vram_gb": 24,
        "torch_arch": "8.9",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 60,
        "ollama_model": "qwen3.5:2b",
        "warn": "L4 has 24 GB. Switching extractor to qwen3.5:2b to leave headroom for moshi.",
        "block": "",
    },
    {
        "match": "RTX A6000",
        "name": "NVIDIA RTX A6000",
        "vram_gb": 48,
        "torch_arch": "8.6",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 60,
        "ollama_model": "qwen3.5:2b",
        "warn": (
            "RTX A6000 has ~1/8 the FP16 throughput of A100. Moshi may glitch under "
            "real-time load. Acceptable for development; not recommended for live demos."
        ),
        "block": "",
    },
    {
        "match": "A40",
        "name": "NVIDIA A40",
        "vram_gb": 48,
        "torch_arch": "8.6",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 45,
        "ollama_model": "qwen3.5:4b",
        "warn": "A40 works but is slower than A100. Extraction timeout set higher.",
        "block": "",
    },
    {
        "match": "H100",
        "name": "NVIDIA H100",
        "vram_gb": 80,
        "torch_arch": "9.0",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 20,
        "ollama_model": "qwen3.5:4b",
        "warn": "",
        "block": "",
    },
    {
        "match": "H200",
        "name": "NVIDIA H200",
        "vram_gb": 141,
        "torch_arch": "9.0",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 20,
        "ollama_model": "qwen3.5:4b",
        "warn": "",
        "block": "",
    },
    # Blocked — VRAM too low for our stack even with qwen3:1.7b
    {
        "match": "RTX 3090",
        "name": "NVIDIA RTX 3090",
        "vram_gb": 24,
        "torch_arch": "8.6",
        "alloc_conf": "",
        "extract_timeout": 60,
        "ollama_model": "qwen3.5:2b",
        "warn": "",
        "block": (
            "RTX 3090 (24 GB) cannot host PersonaPlex + Ollama + faster-whisper "
            "concurrently. Need 40+ GB VRAM. Pick A100, L40, A40, or A6000."
        ),
    },
]


def detect_gpu():
    """Return (product_name, total_vram_mib) for GPU 0, or raise on failure."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise SystemExit(
            "ERROR: nvidia-smi not found. This script requires an NVIDIA GPU + drivers."
        )
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"ERROR: nvidia-smi failed: {e.output.strip()}")
    except subprocess.TimeoutExpired:
        raise SystemExit("ERROR: nvidia-smi timed out (driver hung?).")

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        raise SystemExit("ERROR: nvidia-smi returned no GPUs.")

    # Take first GPU only (POC is single-GPU)
    first = lines[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 2:
        raise SystemExit(f"ERROR: unexpected nvidia-smi format: {first!r}")
    name = parts[0]
    try:
        vram_mib = int(parts[1])
    except ValueError:
        raise SystemExit(f"ERROR: could not parse VRAM from {parts[1]!r}")
    return name, vram_mib


def match_profile(gpu_name: str) -> dict:
    """Pick the first profile whose match string appears in the GPU name (case-insensitive)."""
    upper = gpu_name.upper()
    for p in PROFILES:
        if p["match"].upper() in upper:
            return p
    return {
        "match": "",
        "name": "UNKNOWN GPU",
        "vram_gb": 0,
        "torch_arch": "",
        "alloc_conf": "expandable_segments:True",
        "extract_timeout": 60,
        "ollama_model": "qwen3.5:2b",
        "warn": (
            f"GPU '{gpu_name}' is not in our known-good profile list. Applying "
            "conservative defaults: qwen3:1.7b extractor, 60s timeout, no torch arch "
            "hint. Expect degraded performance until profile is added."
        ),
        "block": "",
    }


def render_env(gpu_name: str, vram_mib: int, profile: dict) -> str:
    lines = [
        "# Auto-generated by runpod/detect_hw.py — DO NOT edit by hand.",
        f"# Detected GPU: {gpu_name} ({vram_mib} MiB)",
        f"# Matched profile: {profile['name']}",
    ]
    if profile.get("warn"):
        lines.append(f"# WARNING: {profile['warn']}")
    lines.append("")
    if profile["torch_arch"]:
        lines.append(f'export TORCH_CUDA_ARCH_LIST="{profile["torch_arch"]}"')
    if profile["alloc_conf"]:
        lines.append(f'export PYTORCH_CUDA_ALLOC_CONF="{profile["alloc_conf"]}"')
    lines.append(f'export VOXREACH_EXTRACT_TIMEOUT="{profile["extract_timeout"]}"')
    lines.append(f'export VOXREACH_ORDER_MODEL="{profile["ollama_model"]}"')
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default="/workspace/.voxreach-hw.env",
        help="Path to write the env file (default: /workspace/.voxreach-hw.env)",
    )
    ap.add_argument("--quiet", action="store_true", help="Suppress stdout banner")
    args = ap.parse_args()

    gpu_name, vram_mib = detect_gpu()
    profile = match_profile(gpu_name)

    if profile.get("block"):
        sys.stderr.write(
            f"BLOCKED: {gpu_name} is not suitable for this stack.\n"
            f"REASON: {profile['block']}\n"
        )
        sys.exit(2)

    env_text = render_env(gpu_name, vram_mib, profile)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(env_text)

    if not args.quiet:
        print("=" * 68)
        print(f"  GPU detected: {gpu_name}  ({vram_mib} MiB)")
        print(f"  Profile:      {profile['name']}")
        print(f"  Extractor:    {profile['ollama_model']}")
        print(f"  Timeout:      {profile['extract_timeout']}s")
        if profile.get("warn"):
            print(f"  WARNING:      {profile['warn']}")
        print(f"  Wrote env:    {out_path}")
        print("  Source it before launching services:")
        print(f"      source {out_path}")
        print("=" * 68)


if __name__ == "__main__":
    main()
