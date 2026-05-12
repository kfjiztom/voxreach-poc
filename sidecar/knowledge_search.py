"""Semantic knowledge search for the Hearth & Pass demo.

Embeds the menu, hours, policies, banchan bar info, and FAQ into a small
sentence-transformers index at startup. Each query → top-K cosine matches.

Used to populate the right-pane "Knowledge Retrieved" panel with real semantic
hits as the conversation references things — "Our chef's picks are bulgogi
and pajeon" surfaces the bulgogi/pajeon menu entries automatically.

Lives in the sidecar venv so:
- Warmup happens once at sidecar startup (~3s for model load + ~500ms for embed)
- Per-query latency is ~10-20ms (CPU; we have ~50 docs, tiny corpus)
- No network round-trips → never blocks audio path or competes with Ollama queue

Tunables via env:
  VOXREACH_RAG_MODEL    — sentence-transformers model name (default: all-MiniLM-L6-v2)
  VOXREACH_RAG_TOP_K    — max hits per search (default: 3)
  VOXREACH_RAG_THRESHOLD — minimum cosine similarity to surface (default: 0.30)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("voxreach.rag")

KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "hearth_and_pass.json"


# ---------------------------------------------------------------------------
# Document flattening — turn the structured JSON into searchable text chunks
# ---------------------------------------------------------------------------


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _load_documents() -> list[tuple[str, str]]:
    """Return (path, text) for every searchable knowledge unit.

    Path is dotted (e.g. menu.mains.bulgogi) for the UI's retrieval log.
    """
    raw = json.loads(KNOWLEDGE_PATH.read_text())
    docs: list[tuple[str, str]] = []

    # Menu items
    for section_key, items in raw.get("menu", {}).items():
        for dish in items:
            name = dish.get("name", "")
            price = ""
            for key in ("price", "price_lunch", "price_half"):
                if key in dish:
                    price = dish[key]
                    break
            description = dish.get("description", "")
            tags = []
            if dish.get("dietary"):
                tags.append("dietary: " + ", ".join(dish["dietary"]))
            if dish.get("spice"):
                tags.append(f"spice: {dish['spice']}")
            if dish.get("chef_pick"):
                tags.append("chef's pick")
            tag_str = " | ".join(tags)
            text = f"{name} {price} — {description}" + (f" [{tag_str}]" if tag_str else "")
            docs.append((f"menu.{_slug(section_key)}.{_slug(name)}", text))

    # Hours
    for day, hrs in raw.get("hours", {}).items():
        docs.append((f"hours.{_slug(day)}", f"{day}: {hrs}"))

    # Banchan bar lunch concept
    bb = raw.get("banchan_bar", {})
    if bb:
        text = (
            f"{bb.get('name', 'Banchan Bar')}: {bb.get('description', '')} "
            f"Price {bb.get('price', '')}. Hours {bb.get('hours', '')}. "
            f"{bb.get('to_go', '')} {bb.get('kids', '')}"
        )
        docs.append(("banchan_bar", text.strip()))

    # Policies
    for policy_key, policy_text in raw.get("policies", {}).items():
        docs.append((f"policies.{_slug(policy_key)}", f"{policy_key}: {policy_text}"))

    # FAQ
    for i, faq in enumerate(raw.get("faq", [])):
        q = faq.get("q", "")
        a = faq.get("a", "")
        docs.append((f"faq.{i}", f"Q: {q} A: {a}"))

    # Restaurant info (address, phone)
    r = raw.get("restaurant", {})
    if r:
        addr = r.get("address", {})
        addr_str = f"{addr.get('street', '')}, {addr.get('city', '')}, {addr.get('state', '')} {addr.get('zip', '')}"
        docs.append((
            "restaurant.contact",
            f"{r.get('name', '')} ({r.get('name_korean', '')}) at {addr_str}. Phone {r.get('phone', '')}",
        ))
        if addr.get("directions"):
            docs.append(("restaurant.directions", f"Directions: {addr['directions']}"))

    return docs


# ---------------------------------------------------------------------------
# Search engine
# ---------------------------------------------------------------------------


class KnowledgeSearch:
    """Semantic search over the Hearth & Pass knowledge pack.

    Lazily initializes the sentence-transformers model on first use so that
    'import knowledge_search' is fast — useful when the order extractor falls
    back to RuleBasedExtractor and we don't actually need RAG.
    """

    def __init__(
        self,
        model_name: str | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
    ):
        self.model_name = model_name or os.environ.get(
            "VOXREACH_RAG_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        self.top_k = top_k or int(os.environ.get("VOXREACH_RAG_TOP_K", "3"))
        self.threshold = threshold or float(os.environ.get("VOXREACH_RAG_THRESHOLD", "0.30"))

        self.docs: list[tuple[str, str]] = _load_documents()
        self._model: Any = None  # SentenceTransformer, lazy-loaded
        self._embeddings: Any = None  # numpy array, lazy-computed
        self._ready = False

    def warmup(self) -> None:
        """Force model load + corpus embed. Safe to call multiple times."""
        if self._ready:
            return
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
        except ImportError as e:
            log.warning("sentence-transformers not installed; RAG disabled (%s)", e)
            return

        log.info("RAG: loading model %s ...", self.model_name)
        self._model = SentenceTransformer(self.model_name, device="cpu")
        log.info("RAG: embedding %d documents ...", len(self.docs))
        self._embeddings = self._model.encode(
            [text for _, text in self.docs],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        self._ready = True
        log.info("RAG: ready (%d docs, dim=%d)", len(self.docs), self._embeddings.shape[1])

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """Return up to top_k {path, snippet, score} above threshold."""
        if not self._ready:
            return []
        if not query or not query.strip():
            return []

        try:
            import numpy as np
        except ImportError:
            return []

        k = top_k if top_k is not None else self.top_k
        q_emb = self._model.encode(
            [query.strip()],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        # Both vectors normalized → dot product == cosine similarity
        scores = self._embeddings @ q_emb
        ranked = np.argsort(scores)[::-1]
        results = []
        for idx in ranked[: k * 2]:  # over-fetch in case some are below threshold
            score = float(scores[idx])
            if score < self.threshold:
                continue
            path, text = self.docs[idx]
            results.append({"path": path, "snippet": text, "score": round(score, 3)})
            if len(results) >= k:
                break
        return results

    @property
    def ready(self) -> bool:
        return self._ready


# Module-level singleton — share one instance across the FastAPI app
_search: KnowledgeSearch | None = None


def get_search() -> KnowledgeSearch:
    global _search
    if _search is None:
        _search = KnowledgeSearch()
    return _search
