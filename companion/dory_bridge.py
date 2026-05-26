"""Optional Dory integration for Elwin.

Elwin uses the published ``dory-memory`` package when installed. If the package
is missing or misconfigured, Elwin falls back to its existing memory stack
without failing requests.
"""

from __future__ import annotations

import logging
import inspect
import threading
from typing import Any

from . import config

logger = logging.getLogger(__name__)

try:
    from dory import DoryMemory
except Exception as exc:  # pragma: no cover - optional dependency path
    DoryMemory = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = ""

_lock = threading.RLock()
_memory: DoryMemory | None = None
_flush_thread: threading.Thread | None = None
_flush_errors = 0
_last_flush_stats: dict[str, Any] = {}
_init_error = ""
_dirty_turns = 0


def enabled() -> bool:
    return bool(config.DORY_ENABLED and DoryMemory is not None)


def status_reason() -> str:
    if not config.DORY_ENABLED:
        return "disabled in config"
    if _init_error:
        return _init_error
    if DoryMemory is None:
        return _IMPORT_ERROR or "Dory not importable"
    return ""


def get_memory() -> DoryMemory | None:
    global _memory, _init_error
    if not enabled():
        return None
    with _lock:
        if _memory is not None:
            return _memory
        try:
            kwargs = {
                "db_path": config.DORY_DB_PATH,
                "extract_model": config.MODEL or "local-model",
                "extract_backend": "openai",
                "extract_base_url": config.LLM_BASE_URL,
                "extract_api_key": config.LLM_API_KEY or "local",
                "session_id": "elwin-ransom",
            }
            params = inspect.signature(DoryMemory.__init__).parameters
            if "infer_implicit" in params:
                kwargs["infer_implicit"] = True
            _memory = DoryMemory(**kwargs)
            observer = getattr(_memory, "_observer", None)
            if observer is not None:
                observer.threshold = max(1, config.DORY_OBSERVER_THRESHOLD)
            _init_error = ""
            return _memory
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Failed to initialize Dory: %s", exc)
            _init_error = str(exc)
            _memory = None
            return None


def query(topic: str) -> str:
    mem = get_memory()
    if mem is None or not topic.strip():
        return ""
    try:
        return (mem.query(topic) or "").strip()
    except Exception as exc:
        logger.warning("Dory query failed: %s", exc)
        return ""


def add_turn(role: str, content: str) -> None:
    global _dirty_turns
    mem = get_memory()
    if mem is None or not content.strip():
        return
    try:
        mem.add_turn(role, content.strip())
        _dirty_turns += 1
    except Exception as exc:
        logger.warning("Dory add_turn failed: %s", exc)


def _flush_worker() -> None:
    global _flush_thread, _flush_errors, _last_flush_stats
    mem = get_memory()
    if mem is None:
        _flush_thread = None
        return
    try:
        _last_flush_stats = mem.flush()
        _flush_errors = 0
    except Exception as exc:
        _flush_errors += 1
        if _flush_errors == 1:
            logger.error("Dory flush failed — memories are not persisting: %s", exc)
        elif _flush_errors % 5 == 0:
            logger.error("Dory flush still failing (%d errors): %s", _flush_errors, exc)
        else:
            logger.warning("Dory flush failed: %s", exc)
    finally:
        _flush_thread = None


def flush_async() -> None:
    global _flush_thread
    mem = get_memory()
    if mem is None:
        return
    with _lock:
        if _flush_thread and _flush_thread.is_alive():
            return
        _flush_thread = threading.Thread(
            target=_flush_worker,
            name="elwin-dory-flush",
            daemon=True,
        )
        _flush_thread.start()


def maybe_flush(turn_threshold: int | None = None) -> None:
    global _dirty_turns
    mem = get_memory()
    if mem is None:
        return
    if turn_threshold is None:
        turn_threshold = config.DORY_FLUSH_TURN_THRESHOLD
    with _lock:
        if _dirty_turns < turn_threshold:
            return
        _dirty_turns = 0
    flush_async()


def stats() -> dict[str, Any]:
    mem = get_memory()
    if mem is None:
        return {
            "enabled": False,
            "reason": status_reason(),
            "graph": {},
            "observer": {},
            "top_memories": [],
            "last_flush": _last_flush_stats,
            "flush_errors": _flush_errors,
        }
    try:
        graph_stats = mem.graph.stats()
        top_nodes = sorted(
            mem.graph.all_nodes(zone=None),
            key=lambda n: (n.is_core, n.salience, n.last_activated),
            reverse=True,
        )[:5]
        observer = getattr(mem, "_observer", None)
        return {
            "enabled": True,
            "reason": "",
            "graph": graph_stats,
            "observer": observer.stats() if observer else {},
            "top_memories": [
                {
                    "content": n.content,
                    "type": n.type.value,
                    "zone": n.zone,
                    "salience": round(n.salience, 3),
                    "is_core": bool(n.is_core),
                }
                for n in top_nodes
            ],
            "last_flush": _last_flush_stats,
            "flush_errors": _flush_errors,
        }
    except Exception as exc:
        logger.warning("Dory stats failed: %s", exc)
        return {
            "enabled": False,
            "reason": f"stats failed: {exc}",
            "graph": {},
            "observer": {},
            "top_memories": [],
            "last_flush": _last_flush_stats,
            "flush_errors": _flush_errors,
        }


def inspect_memories(
    query: str = "",
    node_type: str = "",
    zone: str = "",
    limit: int = 40,
) -> dict[str, Any]:
    mem = get_memory()
    if mem is None:
        return {"enabled": False, "reason": status_reason(), "items": []}

    try:
        nodes = mem.graph.all_nodes(zone=None)
        q = query.strip().lower()
        type_filter = node_type.strip().upper()
        zone_filter = zone.strip().lower()

        items = []
        for node in nodes:
            if q and q not in node.content.lower() and not any(q in tag.lower() for tag in node.tags):
                continue
            if type_filter and node.type.value != type_filter:
                continue
            if zone_filter and node.zone != zone_filter:
                continue
            items.append({
                "id": node.id,
                "content": node.content,
                "type": node.type.value,
                "zone": node.zone,
                "salience": round(node.salience, 3),
                "is_core": bool(node.is_core),
                "activation_count": node.activation_count,
                "distinct_sessions": node.distinct_sessions,
                "tags": list(node.tags or []),
                "created_at": node.created_at,
                "last_activated": node.last_activated,
            })

        items.sort(
            key=lambda item: (
                item["zone"] != "active",
                not item["is_core"],
                -item["salience"],
                -item["activation_count"],
                item["content"].lower(),
            )
        )
        return {
            "enabled": True,
            "reason": "",
            "items": items[:limit],
            "total": len(items),
        }
    except Exception as exc:
        logger.warning("Dory inspect failed: %s", exc)
        return {"enabled": False, "reason": f"inspect failed: {exc}", "items": []}
