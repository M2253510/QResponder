"""Model-agnostic defensive JSON parsing (§4.6, §14).

Weak local models wrap JSON in prose or code fences. We strip fences, try a
direct parse, then fall back to extracting the outermost array/object. Callers
decide what to do on persistent failure (extract raises; answer flags
NEEDS_REVIEW / parse_error).
"""

from __future__ import annotations

import json


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # Remove the opening fence (optionally ```json) and the closing fence.
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _from_dict(data: dict) -> list:
    """A dict is either a wrapper around the array (e.g. {"questions": [...]}) or a
    single object. Unwrap a list-of-objects value if present; else treat as one item."""
    for v in data.values():
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v
    return [data]


def parse_json_array(text: str) -> list:
    """Parse a JSON array from possibly-noisy model output. Raises ValueError."""
    t = _strip_fences(text)
    try:
        data = json.loads(t)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return _from_dict(data)
    except json.JSONDecodeError:
        pass

    # Fall back: slice the outermost [...] span.
    start, end = t.find("["), t.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(t[start : end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Last resort: salvage every COMPLETE top-level {...} object. This recovers a
    # partial result when the model's output was truncated mid-array (common when a
    # provider's max_tokens — or "thinking" tokens — cut off a long list), instead of
    # throwing away everything.
    salvaged = _salvage_objects(t)
    if salvaged:
        return salvaged

    raise ValueError("Could not parse a JSON array from model output.")


def _salvage_objects(text: str) -> list:
    """Extract each complete, balanced top-level {...} object and parse it, ignoring
    an incomplete trailing object (truncated output). String-aware so braces inside
    quoted values don't confuse the depth counter."""
    out: list = []
    depth = 0
    start: int | None = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            out.append(obj)
                    except json.JSONDecodeError:
                        pass
                    start = None
    return out
