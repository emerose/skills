"""stable — a client + CLI for the Stable (usestable.com) virtual-mailbox API.

The importable surface is the :class:`~stable.client.StableClient` plus its error
types. The CLI lives in ``scripts/stable.py`` (run via ``uv``) and imports this
package.

    from stable import StableClient
    with StableClient(api_key) as sc:
        for item in sc.iter_mail_items(limit=20):
            print(item["from"])
"""

from __future__ import annotations

from .client import (
    DEFAULT_BASE_URL,
    StableAPIError,
    StableClient,
    StableConfigError,
    StableError,
)

__all__ = [
    "StableClient",
    "StableError",
    "StableConfigError",
    "StableAPIError",
    "DEFAULT_BASE_URL",
]

__version__ = "0.1.0"
