"""Idempotent patcher for moshi/server.py — server-side default text prompt with override mode.

NVIDIA's PersonaPlex moshi.server reads the system prompt from the client's
WebSocket query string (`request.query["text_prompt"]`). The PersonaPlex UI
auto-fills that field with its own default, so the user has to manually clear
or paste over it on every call.

This patch teaches the server to load the system prompt from a file specified
in `MOSHI_DEFAULT_TEXT_PROMPT_FILE`. Behavior:
  - If MOSHI_DEFAULT_TEXT_PROMPT_FILE is set AND the file exists → ALWAYS
    uses that prompt (server-side override; ignores what the client sent).
  - If env var is unset / file missing → original behavior (client's prompt
    via query string).

This is intentional: we want Vox loaded by default. Override-from-UI is a
nice-to-have that can be re-added later if needed.

Re-running this patcher is a no-op if already applied (idempotent).

Usage:
    python inject_default_prompt.py <path-to-server.py>
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "# VoxReach patch: server-side default text prompt"
LEGACY_MARKER_TEXT = (
    "VoxReach patch: server-side default text prompt"
)

OLD_PATTERN = (
    'self.lm_gen.text_prompt_tokens = '
    'self.text_tokenizer.encode(wrap_with_system_tags(request.query["text_prompt"])) '
    'if len(request.query["text_prompt"]) > 0 else None'
)

NEW_BLOCK = """        # VoxReach patch: server-side default text prompt
        # Override the client-provided text_prompt with MOSHI_DEFAULT_TEXT_PROMPT_FILE
        # when set — this prevents the PersonaPlex UI's auto-filled default
        # (teacher/assistant persona) from beating our Vox/Hearth&Pass prompt.
        # If encode fails (token overflow, bad chars), fall back to the client
        # prompt so the connection still completes — better than a silent drop.
        _default_path = os.environ.get("MOSHI_DEFAULT_TEXT_PROMPT_FILE", "")
        _client_prompt = None
        if _default_path and os.path.exists(_default_path):
            try:
                with open(_default_path) as _f:
                    _file_prompt = _f.read().strip()
                _tok = self.text_tokenizer.encode(wrap_with_system_tags(_file_prompt))
                logger.info(f"VoxReach: overriding text_prompt from {_default_path} ({len(_file_prompt)} chars, {len(_tok)} tokens)")
                self.lm_gen.text_prompt_tokens = _tok
                _client_prompt = _file_prompt
            except Exception as _e:
                logger.error(f"VoxReach: persona-prompt encode FAILED ({_e!r}); falling back to client prompt")
        if _client_prompt is None:
            _client_prompt = request.query.get("text_prompt", "") or ""
            self.lm_gen.text_prompt_tokens = self.text_tokenizer.encode(wrap_with_system_tags(_client_prompt)) if _client_prompt else None"""


def patch(server_py: Path) -> str:
    """Returns 'patched', 'already', 'updated', or raises on error."""
    if not server_py.exists():
        raise FileNotFoundError(f"server.py not found at {server_py}")

    text = server_py.read_text()

    # If our marker is present, we may have an EARLIER version of the patch
    # (fallback-only mode, or v2 override without try/except). Check the v3
    # signature; if not present, replace the legacy block with current logic.
    if MARKER in text:
        if "persona-prompt encode FAILED" in text:
            return "already"
        # Legacy / earlier patch present — locate and replace
        return _replace_legacy_block(server_py, text)

    if OLD_PATTERN not in text:
        raise ValueError(
            f"Could not find expected pattern in {server_py}. "
            f"The moshi version may have shifted and the patch needs updating."
        )

    # Compute the leading whitespace from the matched line
    idx = text.find(OLD_PATTERN)
    line_start = text.rfind("\n", 0, idx) + 1
    indent = text[line_start:idx]

    # NEW_BLOCK is written assuming 8-space indent; re-indent if needed
    new_block_lines = NEW_BLOCK.split("\n")
    base_indent_len = 8
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


def _replace_legacy_block(server_py: Path, text: str) -> str:
    """Find and replace the legacy fallback-only patch block with the override version."""
    # The legacy block was a multi-line section starting with the marker comment.
    # We find it and replace from the marker through the next `self.lm_gen.text_prompt_tokens = ...` line.
    marker_idx = text.find(MARKER)
    if marker_idx == -1:
        raise RuntimeError("legacy marker found in check but missing now (race?)")

    # Find indent
    line_start = text.rfind("\n", 0, marker_idx) + 1
    indent = text[line_start:marker_idx]

    # Find end of the legacy block — search for the closing
    # `self.lm_gen.text_prompt_tokens = ...` line AFTER the marker
    end_pattern = "self.lm_gen.text_prompt_tokens ="
    end_search_start = marker_idx
    end_idx = text.find(end_pattern, end_search_start)
    if end_idx == -1:
        raise RuntimeError("could not find end of legacy patch block")
    # Advance to end of that line
    line_end = text.find("\n", end_idx)
    if line_end == -1:
        line_end = len(text)

    # Replace [marker_idx .. line_end+1)
    new_block_lines = NEW_BLOCK.split("\n")
    base_indent_len = 8
    rewritten = []
    for i, line in enumerate(new_block_lines):
        if line.startswith(" " * base_indent_len):
            rewritten.append(indent + line[base_indent_len:])
        elif line.strip() == "":
            rewritten.append("")
        else:
            rewritten.append(indent + line if i > 0 else line)
    # Drop the leading indent from the first line because it'll be inserted at line_start
    replacement = "\n".join(rewritten)

    # Reconstruct: text before marker line + replacement + text after (line_end + 1)
    new_text = text[:line_start] + replacement + text[line_end:]
    server_py.write_text(new_text)
    return "updated"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python inject_default_prompt.py <path-to-server.py>", file=sys.stderr)
        sys.exit(2)
    result = patch(Path(sys.argv[1]))
    print(f"inject_default_prompt: {result}")
