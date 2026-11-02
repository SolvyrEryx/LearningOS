"""Honest refresh receipts: a viewer failure must not disguise a successful evidence save."""
_cache = {}


def signature(ws):
    paths = [ws.event_log, ws.marker]
    from pathlib import Path
    for parent in (ws.memory_global, ws.memory_subjects, ws.knowledge_subjects, ws.checkpoints, ws.session_logs, Path(__file__).resolve().parent.parent / "templates/atlas"):
        paths.extend(p for p in parent.rglob("*") if p.is_file() and p.suffix in (".json", ".md", ".txt"))
    return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(set(paths)) if p.is_file())


def refresh(ws):
    from atlas import build
    key = str(ws.root)
    try:
        token = signature(ws)
        if key in _cache and _cache[key][0] == token and (ws.graphs / "atlas/index.html").is_file():
            return dict(_cache[key][1])
        target = build(ws)
    except Exception as exc:
        # Canonical evidence has already been committed. Do not roll it back or claim it was lost.
        return {"atlas_refreshed": False, "atlas_error": str(exc), "message": "Memory is saved. Atlas refresh failed; reopen LearningOS to retry. The previous snapshot was preserved."}
    result = {"atlas_refreshed": True, "atlas_path": ws.rel(target), "message": "Memory saved and Atlas rebuilt. Reload an already-open Atlas tab."}
    _cache[key] = (token, result)
    return result
