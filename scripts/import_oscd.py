"""Fetch a pinned OSCD subset from its primary mirror and prepare pixel pairs.

Only named rectified bands, dates and AOIs are retrieved from the ZIP by range.
TLS verification stays enabled. Every member is size/CRC verified, then SHA256
pinned. Download concurrency is one; no model or cloud inference runs here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

from puertogt.ml.oscd import prepare_oscd
from puertogt.preprocess import S2_BANDS
from puertogt.store import confined_path, json_text, sha256_file

IMAGE_URL = "https://partage.imt.fr/index.php/s/gKRaWgRnLMfwMGo/download"
ARCHIVE_BYTES = 512716711
TAIL_BYTES = 262144
TAIL_SHA256 = "95192c044dccfc0b427abd5464c177ae947dbddef66b0ad8d908844ce0a661ed"
PREFIX = "Onera Satellite Change Detection dataset - Images/"
LABELS = {
    "train": (
        "https://partage.imt.fr/index.php/s/2D6n03k58ygBSpu/download",
        "89fb54cd12ad0dbea6c447528139dec305b865294215434bf6dd170fb8fd3ca5",
    ),
    "test": (
        "https://partage.imt.fr/index.php/s/gpStKn4Mpgfnr63/download",
        "2e195eaa1b788b99fa93ea8073e3780bc0b763000b0c49dbf70548acf1e5d67d",
    ),
}


def zip_directory(tail: bytes):
    if hashlib.sha256(tail).hexdigest() != TAIL_SHA256:
        raise ValueError("Official ZIP directory changed; do not silently substitute source")
    end = tail.rfind(b"PK\x05\x06")
    signature, disk, directory_disk, count_disk, count, size, offset, comment = struct.unpack(
        "<4s4H2LH", tail[end : end + 22]
    )
    if disk or directory_disk or count != count_disk or comment or signature != b"PK\x05\x06":
        raise ValueError("Unsupported ZIP container")
    position = offset - (ARCHIVE_BYTES - len(tail))
    finish = position + size
    entries = []
    while position < finish:
        values = struct.unpack("<4s6H3L5H2L", tail[position : position + 46])
        if values[0] != b"PK\x01\x02":
            raise ValueError("Invalid ZIP central directory")
        name_length, extra_length, comment_length = values[10:13]
        name = tail[position + 46 : position + 46 + name_length].decode("utf-8")
        entries.append(
            {
                "name": name,
                "method": values[4],
                "crc32": values[7],
                "compressed": values[8],
                "size": values[9],
                "offset": values[16],
            }
        )
        position += 46 + name_length + extra_length + comment_length
    if len(entries) != count:
        raise ValueError("ZIP directory count mismatch")
    return entries


class Downloader:
    def __init__(self, root: Path, budget: int, reserve: int):
        self.root, self.budget, self.reserve = root, budget, reserve
        root.mkdir(parents=True, exist_ok=True)
        self.ledger = root / "download-ledger.json"
        self.used = json.loads(self.ledger.read_text())["bytes"] if self.ledger.exists() else 0

    def get(self, url: str, path: Path, maximum: int, byte_range: str | None = None):
        if path.exists():
            if path.stat().st_size > maximum:
                raise ValueError("Cached source exceeds its bound")
            return path.read_bytes()
        if (
            self.used + maximum > self.budget
            or shutil.disk_usage(self.root).free - maximum < self.reserve
        ):
            raise OSError("Fixed download budget or disk reserve exhausted")
        partial = path.with_suffix(path.suffix + ".part")
        command = [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--connect-timeout",
            "15",
            "--max-time",
            "240",
            "--max-filesize",
            str(maximum),
            "--output",
            str(partial),
        ]
        if byte_range:
            command += ["--range", byte_range]
        command.append(url)
        try:
            subprocess.run(command, check=True)
        finally:
            self.used += partial.stat().st_size if partial.exists() else 0
            self.ledger.write_text(json_text({"bytes": self.used, "budget": self.budget}))
        partial.replace(path)
        return path.read_bytes()


def extract_member(entry: dict, payload: bytes) -> bytes:
    values = struct.unpack("<4s5H3L2H", payload[:30])
    if values[0] != b"PK\x03\x04" or values[3] != entry["method"]:
        raise ValueError("Invalid source ZIP local header")
    name_length, extra_length = values[-2:]
    if payload[30 : 30 + name_length].decode("utf-8") != entry["name"]:
        raise ValueError("ZIP member identity mismatch")
    offset = 30 + name_length + extra_length
    raw = payload[offset : offset + entry["compressed"]]
    if entry["method"] not in (0, 8) or entry["size"] > 20 * 1024 * 1024:
        raise ValueError("Unsupported or oversized ZIP member")
    value = zlib.decompress(raw, -15) if entry["method"] == 8 else raw
    if len(value) != entry["size"] or zlib.crc32(value) != entry["crc32"]:
        raise ValueError("Source ZIP CRC/size mismatch")
    return value


def fetch(protocol_path: Path, root: Path):
    protocol = json.loads(protocol_path.read_text())
    downloader = Downloader(root, protocol["download_budget_bytes"], protocol["reserve_free_bytes"])
    tail = downloader.get(
        IMAGE_URL,
        root / "zip-directory.bin",
        TAIL_BYTES,
        f"{ARCHIVE_BYTES - TAIL_BYTES}-{ARCHIVE_BYTES - 1}",
    )
    entries = zip_directory(tail)
    cities = protocol["train"] + protocol["test"]
    selected = [
        entry
        for entry in entries
        if any(entry["name"].startswith(PREFIX + city + "/") for city in cities)
        and (
            entry["name"].endswith(("/dates.txt", ".geojson"))
            or ("_rect/" in entry["name"] and entry["name"][-7:-4] in S2_BANDS)
        )
    ]
    source = root / "source"
    source.mkdir(exist_ok=True)
    files = []
    cache = root / "ranges"
    cache.mkdir(exist_ok=True)
    for entry in selected:
        relative = entry["name"][len(PREFIX) :]
        path = confined_path(source, relative)
        if path.exists():
            value = path.read_bytes()
            if len(value) != entry["size"] or zlib.crc32(value) != entry["crc32"]:
                raise ValueError("Previously retrieved source member changed")
        else:
            start = entry["offset"]
            length = 30 + len(entry["name"].encode()) + 65535 + entry["compressed"]
            payload = downloader.get(
                IMAGE_URL, cache / f"{start}.bin", length, f"{start}-{start + length - 1}"
            )
            value = extract_member(entry, payload)
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(value)
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(value).hexdigest(),
                "zip_crc32": entry["crc32"],
                "source_offset": entry["offset"],
                "bytes": len(value),
            }
        )
    labels = {}
    for split, (url, digest) in LABELS.items():
        path = root / f"{split}-labels.zip"
        downloader.get(url, path, 1024 * 1024)
        if sha256_file(path) != digest:
            raise ValueError("Official annotation archive changed")
        labels[split] = {"path": path.name, "sha256": digest}
    original = {
        city: {
            str(side): next(
                entry["name"].rsplit("/", 1)[-1]
                for entry in entries
                if f"/{city}/imgs_{side}/" in entry["name"] and entry["name"].endswith("_B04.tif")
            )
            for side in (1, 2)
        }
        for city in cities
    }
    manifest = {
        "source_url": IMAGE_URL,
        "central_directory_sha256": TAIL_SHA256,
        "files": files,
        "selected": cities,
        "original_band_names": original,
        "labels_archives": labels,
        "downloaded_bytes": downloader.used,
    }
    output = root / "source-manifest.json"
    if not output.exists():
        output.write_text(json_text(manifest))
    else:
        existing = json.loads(output.read_text())
        if {row["path"]: row["sha256"] for row in existing["files"]} != {
            row["path"]: row["sha256"] for row in files
        }:
            raise ValueError("Pinned source manifest differs from requested source")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("reports/change/public-data/oscd-bounded-protocol-v3.json"),
    )
    parser.add_argument("--source-root", type=Path, default=Path("data/public/oscd"))
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    source = fetch(args.protocol, args.source_root)
    result = prepare_oscd(
        source,
        args.source_root / "train-labels.zip",
        args.source_root / "test-labels.zip",
        args.destination,
        args.protocol,
    )
    print(
        json_text(
            {
                "destination": str(args.destination),
                "cities": len(result["cities"]),
                "exclusions": len(result["exclusions"]),
            }
        )
    )


if __name__ == "__main__":
    main()
