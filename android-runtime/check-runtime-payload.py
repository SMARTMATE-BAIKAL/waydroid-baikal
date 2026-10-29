#!/usr/bin/env python3
"""Validate the complete Android payload before the unified package is built.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

from native_validation import native_artifact_hashes
from runtime_validation import boot_artifact_hashes, server_artifact_hashes, validate_server_boot_chain

OVERLAY = "var/lib/waydroid/overlay_rw/"
METADATA = "usr/share/waydroid-baikal/android-runtime/"


def scan_archive(archive):
    hashes, metadata = {}, {}
    for member in archive:
        name = member.name
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or str(path) != name or
                not member.isfile() or name in hashes or
                not name.startswith((OVERLAY, METADATA))):
            raise ValueError("Unsafe or duplicate runtime archive member: " + name)
        source = archive.extractfile(member)
        if name.startswith(METADATA):
            if member.size > 4 * 1024 * 1024:
                raise ValueError("Unexpectedly large runtime metadata: " + name)
            payload = source.read()
            metadata[name.removeprefix(METADATA)] = payload
            hashes[name] = hashlib.sha256(payload).hexdigest()
        else:
            hashes[name] = hashlib.file_digest(source, "sha256").hexdigest()
    return hashes, metadata


def validate_generation(hashes, metadata, expected):
    actual = {name: value for name, value in hashes.items() if name.startswith(OVERLAY)}
    if actual != expected:
        raise ValueError("Runtime payload differs from the reviewed file manifest")

    def read(name):
        return json.loads(metadata[name])

    def provenance(report, manifests, artifacts):
        snapshot = {
            "manifests_sha256": {name: hashes[METADATA + name] for name in manifests},
            "artifacts_sha256": artifacts,
        }
        if report.get("provenance") != snapshot:
            raise ValueError("Runtime validation report is stale or incomplete")

    def require_files(artifacts, destinations):
        for name, checksum in artifacts.items():
            if hashes[OVERLAY + destinations(name)] != checksum:
                raise ValueError("Runtime artifact differs from its build record: " + name)

    boot, server = read("build-manifest.json"), read("system-server-manifest.json")
    boot_hashes, server_hashes = boot_artifact_hashes(boot), server_artifact_hashes(server)
    require_files(boot_hashes, lambda name: "system/system/framework/" + name)
    require_files(server_hashes, lambda name: "system/system/framework/" + name.removeprefix("system-server/"))
    boot_report, server_report = read("verification.json"), read("system-server-verification.json")
    provenance(boot_report, ["build-manifest.json"], boot_hashes)
    provenance(server_report, ["build-manifest.json", "system-server-manifest.json"],
               {**boot_hashes, **server_hashes})
    if server_report.get("legacy_build_manifest_without_boot_hashes") is not validate_server_boot_chain(boot, server):
        raise ValueError("Runtime boot chain provenance differs")
    native, native_report = read("native-input-manifest.json"), read("native-input-verification.json")
    native_hashes = native_artifact_hashes(native)
    require_files(native_hashes, lambda name: "system/system/" +
                  ("lib64/" if name.startswith("arm64/") else "lib/") + "libinputreader.so")
    provenance(native_report, ["native-input-manifest.json"], native_hashes)
    for arch in ("arm", "arm64"):
        if native_report["results"][arch]["returncode"] != 0:
            raise ValueError("Native Android loader verification failed: " + arch)
    hwc = read("hwc-provenance.json")
    if (native_report["hwc_sha256"] != hwc["sha256"] or
            hashes[OVERLAY + "vendor/lib64/hw/hwcomposer.waydroid.so"] != hwc["sha256"] or
            native_report["input_init_sha256"] != hwc["input_init_sha256"] or
            hashes[OVERLAY + "vendor/etc/init/waydroid-input.rc"] != hwc["input_init_sha256"]):
        raise ValueError("HWC and Android init do not match native input verification")
    return {"runtime_files": len(actual), "abis": ["arm", "arm64"], "matched_hwc": hwc["source_commit"]}


def read_archive(path):
    # makepkg exposes local sources through symlinks, which zstd skips by default.
    source = Path(path).resolve(strict=True)
    process = subprocess.Popen(["zstd", "-q", "-d", "-c", str(source)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            hashes, metadata = scan_archive(archive)
    finally:
        process.stdout.close()
        status = process.wait()
    if status:
        raise ValueError("Runtime payload decompression failed")
    return hashes, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    expected = {}
    for line in Path(__file__).with_name("payload-sha256sums").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        if name in expected:
            raise ValueError("Duplicate runtime manifest entry")
        expected[name] = checksum
    hashes, metadata = read_archive(args.archive)
    print(json.dumps(validate_generation(hashes, metadata, expected), indent=2))


if __name__ == "__main__":
    main()
