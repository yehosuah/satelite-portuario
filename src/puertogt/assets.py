"""Bounded downloads with checksums and atomic publication of complete assets."""

import hashlib
import os
from pathlib import Path

import httpx


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_asset(
    url: str,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    expected_md5: str | None = None,
    max_bytes: int,
    timeout: float = 120,
) -> dict:
    """Download a pinned asset; incomplete or mismatched content stays unpublished.

    The caller must provide a checksum obtained independently of this download.
    MD5 is supported for GCS objects that publish a checksum and immutable generation;
    the returned SHA256 is used for subsequent local verification. No credentials are
    logged. Interrupted downloads resume from a sibling ``.part`` file.
    """
    if not expected_sha256 and not expected_md5:
        raise ValueError("a pinned SHA256 or provider MD5 is required")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def verified(path: Path) -> bool:
        return (
            (not expected_sha256 or file_hash(path) == expected_sha256.lower())
            and (not expected_md5 or file_hash(path, "md5") == expected_md5.lower())
        )

    if destination.exists():
        if not verified(destination):
            raise ValueError(f"checksum mismatch for existing asset {destination.name}")
        return {"path": str(destination), "sha256": file_hash(destination),
                "bytes": destination.stat().st_size, "downloaded_bytes": 0}

    partial = destination.with_name(destination.name + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > max_bytes:
        raise ValueError("partial asset exceeds download budget")
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    downloaded = 0
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            if offset and response.status_code != 206:
                raise ValueError("server cannot safely resume the partial download")
            if offset:
                content_range = response.headers.get("content-range", "")
                if not content_range.startswith(f"bytes {offset}-"):
                    raise ValueError("server returned an unexpected byte range")
            length = int(response.headers.get("content-length", "0"))
            if length and offset + length > max_bytes:
                raise ValueError("asset exceeds download budget; no bytes downloaded")
            with partial.open("ab" if offset else "wb") as dst:
                for chunk in response.iter_bytes(1024 * 1024):
                    downloaded += len(chunk)
                    if offset + downloaded > max_bytes:
                        raise ValueError("asset exceeds download budget")
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
    if not verified(partial):
        raise ValueError("download checksum mismatch; asset was not published")
    sha256 = file_hash(partial)
    os.replace(partial, destination)
    return {"path": str(destination), "sha256": sha256,
            "bytes": destination.stat().st_size, "downloaded_bytes": downloaded}
