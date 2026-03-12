#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ASSET_PATTERN = re.compile(
    r"^lpac-linux-(?P<arch>[a-z0-9_]+)"
    r"(?:-(?P<os>(?!glibc)[a-z]+)(?P<os_version>[0-9.]+))?"
    r"(?:-glibc(?P<glibc>[0-9.]+))?"
    r"\.zip$"
)


def parse_asset_name(name: str) -> dict[str, str] | None:
    match = ASSET_PATTERN.match(name)
    if not match:
        return None
    payload = match.groupdict()
    result = {
        "name": name,
        "arch": payload["arch"],
    }
    if payload.get("os"):
        result["os"] = payload["os"]
    if payload.get("os_version"):
        result["os_version"] = payload["os_version"]
    if payload.get("glibc"):
        result["glibc"] = payload["glibc"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build lpac asset manifest from zipped bundles")
    parser.add_argument("--assets-dir", type=Path, required=True, help="Directory containing lpac zip bundles")
    parser.add_argument("--output", type=Path, required=True, help="Manifest output path")
    args = parser.parse_args()

    assets_dir = args.assets_dir.resolve()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    assets: list[dict[str, str]] = []
    if assets_dir.exists():
      for asset_path in sorted(assets_dir.glob("lpac-linux-*.zip")):
            parsed = parse_asset_name(asset_path.name)
            if parsed:
                assets.append(parsed)

    output_path.write_text(
        json.dumps({"assets": assets}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
