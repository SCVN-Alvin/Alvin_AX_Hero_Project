"""Small input validators that raise ValidationError with a field name."""

import re
from datetime import date, datetime

from .errors import ValidationError

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def text(data, field, *, max_len, required=False, default=None, strip=True):
    if field not in data:
        if required:
            raise ValidationError(f"{field} is required", field=field)
        return default
    value = data[field]
    if value is None:
        if required:
            raise ValidationError(f"{field} is required", field=field)
        return "" if default is None else default
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text", field=field)
    if strip:
        value = value.strip()
    if required and not value:
        raise ValidationError(f"{field} cannot be empty", field=field)
    if len(value) > max_len:
        raise ValidationError(f"{field} is too long (max {max_len} characters)", field=field)
    return value


def choice(data, field, allowed, *, nullable=False, default=None):
    if field not in data:
        return default
    value = data[field]
    if value is None and nullable:
        return None
    if value not in allowed:
        raise ValidationError(f"{field} must be one of: {', '.join(allowed)}", field=field)
    return value


def boolean(data, field, default=None):
    if field not in data:
        return default
    value = data[field]
    if isinstance(value, bool):
        return value
    raise ValidationError(f"{field} must be true or false", field=field)


def date_or_datetime(data, field):
    """Nullable due date. Date-only values stay date-only (no invented time)."""
    if field not in data:
        return ...
    value = data[field]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a date (YYYY-MM-DD) or ISO date-time", field=field)
    value = value.strip()
    try:
        if _DATE_RE.match(value):
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(f"{field} must be a date (YYYY-MM-DD) or ISO date-time", field=field)
    return value


def string_list(data, field, *, max_items, max_len):
    if field not in data:
        return None
    value = data[field]
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValidationError(f"{field} must be a list of text values", field=field)
    out, seen = [], set()
    for raw in value:
        item = " ".join(raw.split())
        if not item:
            continue
        if len(item) > max_len:
            raise ValidationError(f"Each item in {field} must be at most {max_len} characters", field=field)
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    if len(out) > max_items:
        raise ValidationError(f"{field} allows at most {max_items} items", field=field)
    return out
