#!/usr/bin/env python3
"""Verify and materialize frozen firmware engineering files vendored by YWD-1278.

The original engineering history lives in ``merberg-ai/ywd-mmdvm`` and its
commit/blob identifiers remain in the build manifests as provenance.  Runtime
firmware builds do not require that repository: the exact required blobs are
vendored inside YWD-1278 and are re-hashed before every build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise RuntimeError("unsupported firmware manifest schema")
    engineering = manifest.get("engineering")
    if not isinstance(engineering, dict):
        raise RuntimeError("firmware manifest is missing engineering provenance")
    if engineering.get("source") != "vendored":
        raise RuntimeError("engineering source is not declared vendored")
    if not engineering.get("vendored_root"):
        raise RuntimeError("engineering vendored_root is missing")
    if not isinstance(engineering.get("files"), dict) or not engineering["files"]:
        raise RuntimeError("engineering files map is empty")
    return manifest


def verify_and_materialize(manifest_path: Path, dest: Path) -> tuple[dict, int]:
    manifest = load_manifest(manifest_path)
    engineering = manifest["engineering"]
    vendor_root = (ROOT / engineering["vendored_root"]).resolve()
    try:
        vendor_root.relative_to(ROOT)
    except ValueError as exc:
        raise RuntimeError("vendored engineering root escapes YWD-1278") from exc
    if not vendor_root.is_dir():
        raise RuntimeError(f"vendored engineering root is missing: {vendor_root}")

    verified = 0
    for rel, expected_blob in engineering["files"].items():
        source = (vendor_root / rel).resolve()
        try:
            source.relative_to(vendor_root)
        except ValueError as exc:
            raise RuntimeError(f"vendored path escapes root: {rel}") from exc
        if not source.is_file():
            raise RuntimeError(f"vendored engineering file is missing: {rel}")
        data = source.read_bytes()
        actual_blob = git_blob_sha1(data)
        if actual_blob != expected_blob:
            raise RuntimeError(
                f"vendored engineering blob mismatch for {rel}: "
                f"expected={expected_blob} actual={actual_blob}"
            )
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if git_blob_sha1(target.read_bytes()) != expected_blob:
            raise RuntimeError(f"materialized engineering blob mismatch for {rel}")
        verified += 1

    return manifest, verified


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--dest", type=Path, required=True)
    args = ap.parse_args()

    manifest_path = args.manifest.resolve()
    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    manifest, verified = verify_and_materialize(manifest_path, dest)
    engineering = manifest["engineering"]

    print("VENDORED_ENGINEERING_BLOBS=PASS")
    print(f"ENGINEERING_TRANSFORM_FILES={verified}")
    print(f"ENGINEERING_PROVENANCE_REPOSITORY={engineering['repository']}")
    print(f"ENGINEERING_PROVENANCE_COMMIT={engineering['commit']}")
    print("ENGINEERING_EXTERNAL_REPO_REQUIRED=NO")
    print("ENGINEERING_NETWORK_FETCH_REQUIRED=NO")
    print("ENGINEERING_WORKTREE_USED=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
