"""Idempotent patcher for moshi/server.py — adds server-side default text prompt.

NVIDIA's PersonaPlex moshi.server reads the system prompt from the client's
WebSocket query string (`request.query["text_prompt"]`). The user has to paste
the prompt into the UI on every call. This patch teaches the server to fall
back to a file specified in `MOSHI_DEFAULT_TEXT_PROMPT_FILE` env var when the
client sends an empty prompt, so Vox-the-Hearth-&-Pass-host loads by default
on every connection.

The patch is idempotent — it checks for a marker comment before modifying.
Re-running has no effect if already applied.

Usage:
    python inject_default_prompt.py <path-to-server.py>
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "# VoxReach patch: server-side default text prompt"

OLD_PATTERN = (
    'self.lm_gen.text_prompt_tokens = '
    'self.text_tokenizer.encode(wrap_with_system_tags(request.query["text_prompt"])) '
    'if len(request.query["text_prompt"]) > 0 else None'
)

NEW_BLOCK = """        # VoxReach patch: server-side default text prompt
        # When the client sends an empty text_prompt, fall back to the file
        # at MOSHI_DEFAULT_TEXT_PROMPT_FILE so the operator can pre-load a
        # persona (Hearth & Pass receptionist) without touching the UI.
        _client_prompt = request.query.get("text_prompt", "") or ""
        if not _client_prompt:
            _default_path = os.environ.get("MOSHI_DEFAULT_TEXT_PROMPT_FILE", "")
            if _default_path and os.path.exists(_default_path):
                with open(_default_path) as _f:
                    _client_prompt = _f.read().strip()
                logger.info(f"loaded default text prompt from {_default_path} ({len(_client_prompt)} chars)")
        self.lm_gen.text_prompt_tokens = self.text_tokenizer.encode(wrap_with_system_tags(_client_prompt)) if _client_prompt else None"""


def patch(server_py: Path) -> str:
    """Returns 'patched', 'already', or raises on error."""
    if not server_py.exists():
        raise FileNotFoundError(f"server.py not found at {server_py}")

    text = server_py.read_text()
    if MARKER in text:
        return "already"

    if OLD_PATTERN not in text:
        raise ValueError(
            f"Could not find expected pattern in {server_py}. The moshi "
            f"version may have shifted and the patch needs updating. "
            f"Looked for:\n  {OLD_PATTERN}"
        )

    # Compute the leading whitespace from the matched line so the replacement
    # block keeps the right indentation
    idx = text.find(OLD_PATTERN)
    line_start = text.rfind("\n", 0, idx) + 1
    indent = text[line_start:idx]

    # NEW_BLOCK is written assuming 8-space indent; re-indent if needed
    new_block_lines = NEW_BLOCK.split("\n")
    base_indent_len = 8  # NEW_BLOCK's expected indent
    rewritten = []
    for line in new_block_lines:
        if line.startswith(" " * base_indent_len):
            rewritten.append(indent + line[base_indent_len:])
        elif line.strip() == "":
            rewritten.append("")
        else:
            rewritten.append(indent + line)
    replacement = "\n".join(rewritten)

    new_text = text.replace(indent + OLD_PATTERN, replacement, 1)
    server_py.write_text(new_text)
    return "patched"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python inject_default_prompt.py <path-to-server.py>", file=sys.stderr)
        sys.exit(2)
    result = patch(Path(sys.argv[1]))
    print(f"inject_default_prompt: {result}")
