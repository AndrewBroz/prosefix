"""The versioned JSON document (schema 1)."""

from __future__ import annotations

from . import __version__


def utf16_offsets(text: str) -> list[int]:
    """m[i] = UTF-16 code units before code point i (len(text)+1 entries)."""
    out = [0]
    for ch in text:
        out.append(out[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return out


def file_entry(r) -> dict:
    s = r.settings
    entry = {"path": r.path, "status": r.status, "reason": r.reason,
             "settings": {"target": s.target, "style": s.style, "only": list(s.only),
                          "skip": sorted(s.skip), "target_source": s.target_source}}
    if s.detected is not None:
        entry["settings"]["detected"] = {"uk": s.detected[0], "us": s.detected[1]}
    if r.status == "error":
        return entry
    m = utf16_offsets(r.original)
    entry["changes"] = [{"id": c.id, "pass": c.pass_, "check": c.check,
                         "start": m[c.start], "end": m[c.end],
                         "line": r.original.count("\n", 0, c.start) + 1,
                         "before": c.before, "after": c.after, "reason": c.reason,
                         "decided_by": c.decided_by, "confidence": round(c.confidence, 3),
                         "depends_on": list(c.depends_on)} for c in r.changes]
    entry["review"] = [{"pass": i.pass_, "line": i.line, "kind": i.kind, "check": i.check,
                        "message": i.message} for i in r.review]
    entry["output"] = r.output
    return entry


def document(results, endpoint, skipped) -> dict:
    ep = None if endpoint is None else {"url": endpoint.url, "model": endpoint.model,
                                        "source": endpoint.sources.get("url", "")}
    return {"schema": 1, "prosefix": __version__, "endpoint": ep,
            "skipped": [{"what": s.what, "why": s.why, "fix": s.fix} for s in skipped],
            "files": [file_entry(r) for r in results]}
