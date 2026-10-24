from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RateLimit:
    min_interval_seconds: float = 0.0
    _next_allowed_at: float = field(default=0.0, init=False, repr=False)

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        now = time.monotonic()
        if now < self._next_allowed_at:
            time.sleep(self._next_allowed_at - now)
        self._next_allowed_at = time.monotonic() + self.min_interval_seconds


def _request(url: str, *, headers: dict[str, str] | None = None, timeout_seconds: int = 30) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def request_text(
    url: str,
    *,
    rate_limit: RateLimit | None = None,
    looks_valid: Callable[[str], bool] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 30,
    retries: int = 2,
) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if rate_limit is not None:
                rate_limit.wait()
            payload = _request(url, headers=headers, timeout_seconds=timeout_seconds).decode("utf-8", errors="replace")
            if looks_valid is not None and not looks_valid(payload):
                raise RuntimeError(f"Response validation failed for {url}")
            return payload
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(2 ** attempt, 3))
    raise RuntimeError(f"Request failed for {url}: {last_error}")


def request_json(
    url: str,
    *,
    rate_limit: RateLimit | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 30,
    retries: int = 2,
) -> dict[str, Any]:
    text = request_text(
        url,
        rate_limit=rate_limit,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        looks_valid=lambda payload: payload.lstrip().startswith("{") or payload.lstrip().startswith("["),
    )
    payload = json.loads(text)
    if isinstance(payload, dict):
        return payload
    return {"items": payload}
