#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


INCLUDE_PATHS = [
    Path("README.md"),
    Path("deploy"),
]
SKIP_DIR_NAMES = {"__pycache__"}
SKIP_SUFFIXES = {".pyc"}


def iter_files(repo_root: Path):
    for rel_path in INCLUDE_PATHS:
        source = repo_root / rel_path
        if source.is_file():
            yield source, rel_path
            continue
        if source.is_dir():
            for child in source.rglob("*"):
                if child.is_dir():
                    continue
                relative = child.relative_to(repo_root)
                if any(part in SKIP_DIR_NAMES for part in relative.parts):
                    continue
                if child.suffix in SKIP_SUFFIXES:
                    continue
                yield child, relative


def build_package(repo_root: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with ZipFile(output_path, "w", ZIP_DEFLATED) as archive:
        for source, relative in iter_files(repo_root):
            archive.write(source, relative.as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Debian deployment zip package")
    parser.add_argument(
        "--repo-root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Repository root path",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output zip file path",
    )
    parser.add_argument(
        "--copy-to",
        type=Path,
        help="Optional second location to copy the finished zip to",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    output_path = args.output.resolve()
    build_package(repo_root, output_path)

    if args.copy_to:
        args.copy_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, args.copy_to.resolve())

    print(output_path)
    print(output_path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
