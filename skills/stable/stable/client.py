"""A thin, typed client for the Stable (usestable.com) virtual-mailbox REST API.

Stable's public API is documented at https://docs.usestable.com. It is
authenticated with an ``x-api-key`` header and rooted at ``https://api.usestable.com``.

The API is **read-heavy**: mail items, checks, companies, and locations are
retrievable, and mail can be *organized* with tags and teams (the main write
surface). The physical mail actions themselves — requesting a scan, forwarding,
shredding, depositing a check — are NOT triggerable through the public API; they
appear only as read-only status fields on a mail item (``scanDetails``,
``forwardDetails``, ``shredDetails``, ``depositDetails``) reflecting actions you
started in the Stable dashboard. This client exposes exactly what the API exposes
and does not pretend otherwise.

The client takes an injectable ``httpx.Client`` so it can be exercised in tests
against an ``httpx.MockTransport`` with no network and no API key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Optional

import httpx

DEFAULT_BASE_URL = "https://api.usestable.com"
DEFAULT_TIMEOUT = 30.0


class StableError(Exception):
    """Base class for all client-raised errors."""


class StableConfigError(StableError):
    """Missing/invalid configuration (e.g. no API key)."""


@dataclass
class StableAPIError(StableError):
    """A non-2xx response from the Stable API."""

    status_code: int
    message: str
    body: Any = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"HTTP {self.status_code}: {self.message}"

    @property
    def is_auth(self) -> bool:
        return self.status_code in (401, 403)

    @property
    def is_not_found(self) -> bool:
        return self.status_code == 404

    @property
    def is_rate_limited(self) -> bool:
        return self.status_code == 429


class StableClient:
    """Client for the Stable virtual-mailbox API.

    Parameters
    ----------
    api_key:
        The Stable API key, sent as the ``x-api-key`` header.
    base_url:
        API root; defaults to ``https://api.usestable.com``.
    http_client:
        An optional pre-built ``httpx.Client`` (used by tests to inject a
        ``MockTransport``). When supplied, ``base_url``/timeout on that client
        are respected and this class only adds the auth header per request.
    """

    def __init__(
        self,
        api_key: Optional[str],
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        if not api_key:
            raise StableConfigError(
                "No Stable API key. Set STABLE_API_KEY (or put it in ~/.env). "
                "Keys are issued by Stable — see references/setup.md."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(base_url=self.base_url, timeout=timeout)

    # -- lifecycle ---------------------------------------------------------- #
    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "StableClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level request -------------------------------------------------- #
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[Any] = None,
    ) -> Any:
        headers = {"x-api-key": self.api_key, "accept": "application/json"}
        # Drop None-valued params so callers can pass sparse filter dicts.
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self._http.request(
            method, path, params=clean or None, json=json, headers=headers
        )
        if resp.status_code >= 400:
            raise self._to_error(resp)
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            return resp.json()
        return resp.content

    @staticmethod
    def _to_error(resp: httpx.Response) -> StableAPIError:
        body: Any = None
        message = resp.reason_phrase or "error"
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("message"):
                message = str(body["message"])
        except Exception:
            text = resp.text.strip()
            if text:
                message = text[:300]
        return StableAPIError(resp.status_code, message, body)

    # -- mail items --------------------------------------------------------- #
    def list_mail_items(
        self,
        *,
        location_id: Optional[str] = None,
        created_gte: Optional[str] = None,
        created_gt: Optional[str] = None,
        created_lte: Optional[str] = None,
        created_lt: Optional[str] = None,
        scan_status: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> dict[str, Any]:
        """One page of mail items (Relay-style connection).

        Returns the raw ``MailItemsConnection`` dict: ``edges`` (each with
        ``cursor`` + ``node``), ``pageInfo`` (``endCursor``/``hasNextPage``),
        and ``totalCount``.
        """
        params = {
            "locationId": location_id,
            "createdAt_gte": created_gte,
            "createdAt_gt": created_gt,
            "createdAt_lte": created_lte,
            "createdAt_lt": created_lt,
            "scan.status": scan_status,
            "first": first,
            "after": after,
        }
        return self._request("GET", "/v1/mail-items", params=params)

    def iter_mail_items(
        self,
        *,
        limit: Optional[int] = None,
        page_size: int = 100,
        **filters: Any,
    ) -> Iterator[dict[str, Any]]:
        """Yield mail-item nodes across pages, newest-first as the API returns.

        ``limit`` caps the total yielded; ``page_size`` is the per-request
        ``first``. Any keyword in ``filters`` is forwarded to
        :meth:`list_mail_items` (e.g. ``location_id=``, ``created_gte=``).
        """
        after: Optional[str] = None
        yielded = 0
        while True:
            want = page_size
            if limit is not None:
                want = min(page_size, limit - yielded)
                if want <= 0:
                    return
            conn = self.list_mail_items(first=want, after=after, **filters)
            edges = conn.get("edges", []) or []
            for edge in edges:
                node = edge.get("node")
                if node is None:
                    continue
                yield node
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            page = conn.get("pageInfo", {}) or {}
            if not page.get("hasNextPage"):
                return
            after = page.get("endCursor")
            if not after:
                return

    def get_mail_item(self, mail_item_id: str) -> dict[str, Any]:
        """Retrieve a single mail item by id."""
        return self._request("GET", f"/v1/mail-items/{mail_item_id}")

    # -- tags --------------------------------------------------------------- #
    def list_tags(self) -> list[dict[str, Any]]:
        return _as_list(self._request("GET", "/v1/tags"), "tags")

    def create_tags(self, names: list[str]) -> list[dict[str, Any]]:
        body = {"tags": [{"name": n} for n in names]}
        return _as_list(self._request("POST", "/v1/tags", json=body), "tags")

    def delete_tags(self, ids: list[str]) -> Any:
        body = {"tags": [{"id": i} for i in ids]}
        return self._request("DELETE", "/v1/tags", json=body)

    def rename_tag(self, tag_id: str, name: str) -> Any:
        return self._request("PUT", f"/v1/tags/{tag_id}", json={"name": name})

    def set_mail_item_tags(
        self, mail_item_ids: list[str], tags: list[tuple[str, bool]]
    ) -> Any:
        """Assign/remove tags on mail items.

        ``tags`` is a list of ``(tag_id, is_applied)`` — ``True`` to assign,
        ``False`` to remove.
        """
        body = {
            "mailItemIds": mail_item_ids,
            "tags": [{"id": tid, "isApplied": applied} for tid, applied in tags],
        }
        return self._request("POST", "/v1/mail-items/tags", json=body)

    # -- teams -------------------------------------------------------------- #
    def list_teams(self) -> list[dict[str, Any]]:
        return _as_list(self._request("GET", "/v1/teams"), "teams")

    def create_teams(self, names: list[str]) -> list[dict[str, Any]]:
        body = {"teams": [{"name": n} for n in names]}
        return _as_list(self._request("POST", "/v1/teams", json=body), "teams")

    def delete_teams(self, ids: list[str]) -> Any:
        body = {"teams": [{"id": i} for i in ids]}
        return self._request("DELETE", "/v1/teams", json=body)

    def rename_team(self, team_id: str, name: str) -> Any:
        return self._request("PUT", f"/v1/teams/{team_id}", json={"name": name})

    def set_mail_item_teams(
        self, mail_item_ids: list[str], teams: list[tuple[str, bool]]
    ) -> Any:
        body = {
            "mailItemIds": mail_item_ids,
            "teams": [{"id": tid, "isApplied": applied} for tid, applied in teams],
        }
        return self._request("POST", "/v1/mail-items/teams", json=body)

    # -- locations (read helper) ------------------------------------------- #
    def list_locations(self) -> list[dict[str, Any]]:
        return _as_list(self._request("GET", "/v1/locations"), "locations")

    # -- binary download ---------------------------------------------------- #
    def download(self, url: str) -> bytes:
        """GET an arbitrary (already-signed) Stable URL and return raw bytes.

        Mail-item ``imageUrl`` / ``scanDetails.imageUrl`` / ``ocrResultUrls``
        are short-lived signed URLs; no auth header is required (and sending one
        can break the signature), so this uses a bare request.
        """
        resp = self._http.get(url)
        if resp.status_code >= 400:
            raise self._to_error(resp)
        return resp.content


def _as_list(payload: Any, key: str) -> list[dict[str, Any]]:
    """Normalize a list response that may be bare ``[...]`` or ``{key: [...]}``."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        val = payload.get(key)
        if isinstance(val, list):
            return val
        # Some list endpoints wrap in {data: [...]}
        if isinstance(payload.get("data"), list):
            return payload["data"]
    return []
