"""A minimal read-only client for a self-hosted Immich instance.

Immich is where footage already lands — phones back up to it automatically —
so pulling from an album beats uploading the same file a second time by hand.

This reads and never writes. Immich stays the inbox; NakaVid owns the library.

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

    def _get_json(self, path: str):
        try:
            with urllib.request.urlopen(self._request(path), timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = "check the API key" if exc.code in (401, 403) else exc.reason
            raise ImmichError(f"Immich {path} returned {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ImmichError(f"Immich {path} failed: {exc}") from exc

    def albums(self) -> list[dict]:
        """Albums this key can see: its own, plus ones shared with it.

        /api/albums returns only owned albums. An album shared from another
        account — the ordinary case when footage is filmed on one login and
        collected under another — is invisible without ?shared=true, so asking
        for the album by name would report that it does not exist.
        """
        found: dict[str, dict] = {}
        for path in ("/api/albums", "/api/albums?shared=true"):
            payload = self._get_json(path)
            if not isinstance(payload, list):
                raise ImmichError(f"Immich {path} did not return a list")
            for album in payload:
                album_id = str(album.get("id", ""))
                if album_id:
                    found.setdefault(album_id, album)
        return list(found.values())

    def album_named(self, name: str) -> dict:
        matches = [
            album
            for album in self.albums()
            if str(album.get("albumName", "")).casefold() == name.casefold()
        ]
        if not matches:
            available = ", ".join(sorted(str(a.get("albumName", "?")) for a in self.albums()))
            raise ImmichError(f"No Immich album named {name!r}. Available: {available or 'none'}")
        if len(matches) > 1:
            raise ImmichError(f"More than one Immich album is named {name!r}")
        return matches[0]

    def album_assets(self, album_id: str) -> list[ImmichAsset]:
        payload = self._get_json(f"/api/albums/{album_id}")
        assets = payload.get("assets") if isinstance(payload, dict) else None
        if assets is None:
            raise ImmichError(f"Immich album {album_id} returned no asset list")
        return [
            ImmichAsset(
                id=str(asset.get("id", "")),
                original_file_name=str(asset.get("originalFileName") or asset.get("id", "")),
                created_at=str(asset.get("fileCreatedAt") or asset.get("createdAt") or ""),
                is_video=str(asset.get("type", "")).upper() == "VIDEO",
            )
            for asset in assets
        ]

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
