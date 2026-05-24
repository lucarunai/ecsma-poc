from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_USER_ID = "demo-user"


@dataclass(frozen=True)
class RequestContext:
    user_id: str = DEFAULT_USER_ID


def normalize_user_id(user_id: str | None) -> str:
    candidate = (user_id or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", candidate).strip("_")
    return normalized[:80] or DEFAULT_USER_ID
