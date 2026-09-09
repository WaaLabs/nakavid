"""A minimal client for a self-hosted Immich instance.

Immich is where footage already lands — phones back up to it automatically —
so pulling footage tagged for import there beats uploading the same file a
second time by hand.

Footage itself is read-only: nothing here ever uploads or modifies an asset's
media. Immich stays the inbox; NakaVid owns the library. The one write is
tag housekeeping — once an asset is safely imported, its tag moves from
Nakavid/add to Nakavid/imported, so it doesn't keep showing up as ready to
pull.

Privacy: the instance must be on the LAN, per the AGENTS.md rule that footage
never leaves it. configured_base_url refuses anything that does not resolve to
a private address, so a misconfigured host cannot quietly become egress.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
SEARCH_PAGE_SIZE = 250


class ImmichError(RuntimeError):
    """Raised when Immich cannot be reached or answers unusably."""


@dataclass(frozen=True)
class ImmichAsset:
    id: str
    original_file_name: str
    created_at: str
    is_video: bool


def _resolves_to_private_address(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ImmichError(f"Cannot resolve Immich host {host!r}: {exc}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    return all(
        ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback
        for address in addresses
    )


def configured_base_url() -> str:
    base_url = (getattr(settings, "NAKAVID_IMMICH_URL", "") or "").rstrip("/")
    if not base_url:
        raise ImmichError("NAKAVID_IMMICH_URL is not set")
    host = urllib.parse.urlparse(base_url).hostname
    if host is None:
        raise ImmichError(f"NAKAVID_IMMICH_URL has no host: {base_url!r}")
    if not _resolves_to_private_address(host):
        raise ImmichError(
            f"Refusing to talk to {host!r}: it does not resolve to a private address, "
            "and footage must not leave the LAN."
        )
    return base_url


def configured_api_key() -> str:
    api_key = getattr(settings, "NAKAVID_IMMICH_API_KEY", "") or ""
    if not api_key:
        raise ImmichError(
            "NAKAVID_IMMICH_API_KEY is not set. Create a key in Immich under "
            "Account Settings > API Keys."
        )
    return api_key


class ImmichClient:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = base_url if base_url is not None else configured_base_url()
        self.api_key = api_key if api_key is not None else configured_api_key()

    def _request(self, path: str) -> urllib.request.Request:
        return urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"x-api-key": self.api_key, "Accept": "application/json"},
        )

    def _send_json(self, method: str, path: str, body: dict):
        request = self._request(path)
        request.method = method
        request.data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read()
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = "check the API key" if exc.code in (401, 403) else exc.reason
            raise ImmichError(f"Immich {method} {path} returned {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ImmichError(f"Immich {method} {path} failed: {exc}") from exc

    def _post_json(self, path: str, body: dict):
        return self._send_json("POST", path, body)

    def _get_json(self, path: str):
        try:
            with urllib.request.urlopen(self._request(path), timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = "check the API key" if exc.code in (401, 403) else exc.reason
            raise ImmichError(f"Immich {path} returned {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ImmichError(f"Immich {path} failed: {exc}") from exc

    def tags(self) -> list[dict]:
        """All tags this key can see."""
        payload = self._get_json("/api/tags")
        if not isinstance(payload, list):
            raise ImmichError("Immich /api/tags did not return a list")
        return payload

    def tag_named(self, value: str) -> dict:
        """A tag by its full path, e.g. 'Nakavid/add' for a nested tag.

        Immich reports a tag's full hierarchical path in its 'value' field,
        not just the leaf name, so 'add' alone would not match a tag nested
        under 'Nakavid'.
        """
        tags = self.tags()
        matches = [
            tag for tag in tags if str(tag.get("value", "")).casefold() == value.casefold()
        ]
        if not matches:
            available = ", ".join(sorted(str(t.get("value", "?")) for t in tags))
            raise ImmichError(f"No Immich tag named {value!r}. Available: {available or 'none'}")
        if len(matches) > 1:
            raise ImmichError(f"More than one Immich tag is named {value!r}")
        return matches[0]

    def upsert_tag(self, value: str) -> dict:
        """Get-or-create a tag by its full path, e.g. 'Nakavid/imported'.

        PUT /api/tags upserts: an existing tag comes back unchanged, a
        missing one — and any missing parent segment — is created. Simpler
        and cheaper than resolving 'Nakavid' and 'imported' as two calls.
        """
        payload = self._send_json("PUT", "/api/tags", {"tags": [value]})
        if not isinstance(payload, list):
            raise ImmichError("Immich PUT /api/tags did not return a list")
        matches = [
            tag for tag in payload if str(tag.get("value", "")).casefold() == value.casefold()
        ]
        if not matches:
            raise ImmichError(f"Immich did not return the upserted tag {value!r}")
        return matches[0]

    def tag_assets(self, tag_id: str, asset_ids: list[str]) -> None:
        """Attach a tag to assets, in one call."""
        if not asset_ids:
            return
        self._send_json("PUT", f"/api/tags/{tag_id}/assets", {"ids": asset_ids})

    def untag_assets(self, tag_id: str, asset_ids: list[str]) -> None:
        """Remove a tag from assets, in one call."""
        if not asset_ids:
            return
        self._send_json("DELETE", f"/api/tags/{tag_id}/assets", {"ids": asset_ids})

    def tagged_assets(self, tag_id: str) -> list[ImmichAsset]:
        """Videos carrying a tag, via metadata search.

        Searching by tagId filters to videos server-side rather than fetching
        every photo just to discard it here — the same route album_assets
        used before tags replaced albums as the selection mechanism.
        """
        assets: list[ImmichAsset] = []
        page = 1
        while True:
            payload = self._post_json(
                "/api/search/metadata",
                {"tagIds": [tag_id], "type": "VIDEO", "size": SEARCH_PAGE_SIZE, "page": page},
            )
            block = payload.get("assets") if isinstance(payload, dict) else None
            if block is None:
                raise ImmichError(f"Immich search for tag {tag_id} returned no assets block")
            for asset in block.get("items") or []:
                assets.append(
                    ImmichAsset(
                        id=str(asset.get("id", "")),
                        original_file_name=str(
                            asset.get("originalFileName") or asset.get("id", "")
                        ),
                        created_at=str(asset.get("fileCreatedAt") or asset.get("createdAt") or ""),
                        is_video=str(asset.get("type", "")).upper() == "VIDEO",
                    )
                )
            next_page = block.get("nextPage")
            if not next_page:
                return assets
            try:
                page = int(next_page)
            except (TypeError, ValueError):
                return assets

    def download_asset(self, asset_id: str, target_path: Path) -> None:
        """Stream an original to disk. These are gigabytes; never buffer them."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        partial = target_path.with_suffix(target_path.suffix + ".part")
        try:
            with (
                urllib.request.urlopen(
                    self._request(f"/api/assets/{asset_id}/original"), timeout=TIMEOUT_SECONDS
                ) as response,
                partial.open("wb") as handle,
            ):
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    handle.write(chunk)
        except urllib.error.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise ImmichError(f"Downloading asset {asset_id} returned {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise ImmichError(f"Downloading asset {asset_id} failed: {exc}") from exc
        # Rename only once complete, so an interrupted pull leaves no file that
        # looks ingestible.
        partial.replace(target_path)
