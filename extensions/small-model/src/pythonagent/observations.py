"""Lossless table encoding and cursors. No scientific selection or verdicts."""

import json


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False))


def columnize(rows):
    """Nested field names occur once; index i addresses the same source row."""
    if not rows:
        return {}
    keys = list(rows[0])
    if any(set(row) != set(keys) for row in rows):
        raise ValueError("Column encoding requires identical row fields")
    return {
        key: columnize([row[key] for row in rows])
        if all(isinstance(row[key], dict) for row in rows)
        else [row[key] for row in rows]
        for key in keys
    }


def page_rows(rows, offset=0, limit=48, *, layout="rows", budget=8000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError("offset >= 0 and limit >= 1 must be integers")
    if layout not in ("rows", "columns"):
        raise ValueError("layout must be rows or columns")
    total = len(rows)
    selected = rows[offset : offset + limit]

    def payload(values):
        if layout == "columns":
            return {"layout": "columns-v1", "columns": columnize(values)}
        return {"layout": "rows-v1", "rows": values}

    # Only transport size limits a page. Keep all rows accessible by the cursor.
    original = len(selected)
    while len(selected) > 1 and encoded_size(payload(selected)) > budget:
        selected = selected[: max(1, len(selected) // 2)]
    stop = offset + len(selected)
    return {
        **payload(selected),
        "offset": offset,
        "requested_limit": limit,
        "returned_rows": len(selected),
        "total_rows": total,
        "next_offset": stop if stop < total else None,
        "complete": stop >= total,
        "byte_limited": len(selected) < original,
    }


def structure(value, pointer=""):
    """One level of JSON pointers into the original file, never into a wrapper."""
    return [
        {
            "pointer": pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
            "type": type(child).__name__,
            "items": len(child) if isinstance(child, (dict, list, str)) else None,
        }
        for key, child in value.items()
    ]
