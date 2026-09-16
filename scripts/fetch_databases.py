"""Download and extract the BIRD dev databases.

The 498 questions run against 11 SQLite databases from BIRD's development set. They are not
vendored: the archive is ~330 MB and belongs to the BIRD authors.

Two sources are tried in order. The official Aliyun OSS endpoint is canonical but measured
at roughly 34 KB/s from outside China on 2026-09-16 — around two hours — so a Hugging Face
mirror of the same archive is preferred, measured at roughly 880 KB/s. The official source
remains the fallback so this still works if the mirror disappears.

    uv run python scripts/fetch_databases.py
    uv run python scripts/fetch_databases.py --force
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path("data")

EXPECTED_DATABASES = 11


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    archive: str


SOURCES = (
    Source(
        name="Hugging Face mirror (nlile/BIRD-bench)",
        url="https://huggingface.co/datasets/nlile/BIRD-bench/resolve/main/dev_databases.zip",
        archive="dev_databases.zip",
    ),
    Source(
        name="BIRD official (Aliyun OSS)",
        url="https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip",
        archive="dev.zip",
    ),
)


def _progress(done: int, total: int) -> None:
    if total <= 0:
        sys.stdout.write(f"\r  {done / 1e6:.1f} MB")
    else:
        pct = done / total * 100
        sys.stdout.write(f"\r  {done / 1e6:.1f} / {total / 1e6:.1f} MB  ({pct:.0f}%)")
    sys.stdout.flush()


def download(source: Source, destination: Path) -> Path:
    print(f"Downloading from {source.name}")
    request = urllib.request.Request(source.url, headers={"User-Agent": "nl2sql-reliability"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length", 0))
        done = 0
        with destination.open("wb") as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)
                done += len(chunk)
                _progress(done, total)
    print()
    return destination


def extract(archive: Path, into: Path) -> None:
    print(f"Extracting {archive.name}")
    with zipfile.ZipFile(archive) as zf:
        # Refuse absolute paths and parent-directory traversal: this archive comes from a
        # third party and is unpacked into the working tree.
        for member in zf.namelist():
            target = (into / member).resolve()
            if not str(target).startswith(str(into.resolve())):
                raise ValueError(f"archive member escapes the destination: {member}")
        zf.extractall(into)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    parser.add_argument("--keep-archive", action="store_true", help="do not delete the zip")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Imported here so the script still runs its download step if the package is not yet
    # installed in the current environment.
    sys.path.insert(0, str(Path("src").resolve()))
    from nl2sql_reliability.db import available, clear_cache

    clear_cache()
    existing = available(DATA_DIR)
    if len(existing) >= EXPECTED_DATABASES and not args.force:
        print(f"{len(existing)} databases already present in {DATA_DIR}/. Use --force to refetch.")
        return 0

    last_error: Exception | None = None
    for source in SOURCES:
        archive = DATA_DIR / source.archive
        try:
            if not archive.exists() or args.force:
                download(source, archive)
            extract(archive, DATA_DIR)
            if not args.keep_archive:
                archive.unlink(missing_ok=True)
            break
        except Exception as exc:  # noqa: BLE001 - any failure should fall through to the next source
            last_error = exc
            print(f"  failed: {exc}")
            continue
    else:
        print(f"All sources failed. Last error: {last_error}", file=sys.stderr)
        return 1

    clear_cache()
    found = available(DATA_DIR)
    print(f"\n{len(found)} databases available:")
    for db_id, path in sorted(found.items()):
        print(f"  {db_id:<28} {path.stat().st_size / 1e6:>8.1f} MB")

    if len(found) < EXPECTED_DATABASES:
        print(
            f"\nExpected at least {EXPECTED_DATABASES} databases, found {len(found)}.",
            file=sys.stderr,
        )
        return 1

    total = sum(p.stat().st_size for p in found.values())
    print(f"\nTotal: {total / 1e9:.2f} GB in {DATA_DIR}/ (gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
