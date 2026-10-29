#!/usr/bin/env python3
"""Validate staged ART images by loading both VMs without image compilation.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from runtime_validation import (
    boot_artifact_hashes,
    capture_provenance,
    read_manifest,
    require_runtime_checks,
)


def main():
    require_runtime_checks()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", default="/data/local/tmp/baikal-art-candidate")
    args = parser.parse_args()
    if (
        not args.staging.startswith("/data/local/tmp/")
        or ".." in Path(args.staging).parts
    ):
        parser.error("Staging must be a dedicated /data/local/tmp directory")
    path = Path("/var/lib/waydroid/data") / args.staging.removeprefix("/data/")
    verification_path = path / "verification.json"
    verification_path.unlink(missing_ok=True)
    manifest, manifest_hash = read_manifest(path, "build-manifest.json")
    manifests = {"build-manifest.json": manifest_hash}
    artifacts_snapshot = boot_artifact_hashes(manifest)
    provenance = capture_provenance(path, manifests, artifacts_snapshot)
    expected_stems = {"boot-" + Path(item).stem for item in manifest["classpath"][5:]}
    expected_files = {
        stem + suffix for stem in expected_stems for suffix in (".art", ".oat", ".vdex")
    }
    result = {"provenance": provenance}
    for arch, bits in (("arm64", "64"), ("arm", "32")):
        artifacts = manifest["results"][arch]["artifacts"]
        assert manifest["results"][arch]["returncode"] == 0
        assert set(artifacts) == expected_files, "Unexpected boot component set"
        for name, info in artifacts.items():
            artifact = path / arch / name
            assert artifact.stat().st_size == info["size"]
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == info["sha256"]
        actual = manifest["classpath"].copy()
        actual[5] = args.staging + "/framework.jar"
        command = [
            "lxc-attach",
            "-P",
            "/var/lib/waydroid/lxc",
            "-n",
            "waydroid",
            "--",
            "/system/bin/env",
            "ANDROID_ROOT=/system",
            "ANDROID_DATA=/data",
            "ANDROID_ART_ROOT=/apex/com.android.art",
            "ANDROID_I18N_ROOT=/apex/com.android.i18n",
            "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata",
            "/apex/com.android.art/bin/dalvikvm" + bits,
            "-Xbootclasspath:" + ":".join(actual),
            "-Xbootclasspath-locations:" + ":".join(manifest["classpath"]),
            "-Ximage:/system/framework/boot.art:"
            + args.staging
            + "/boot-framework.art",
            "-Xnoimage-dex2oat",
            "-Xuse-stderr-logger",
            "-verbose:image",
            "-cp",
            actual[5],
            "java.lang.Object",
        ]
        started = time.monotonic()
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        elapsed = time.monotonic() - started
        output = process.stdout + process.stderr
        (path / ("probe-" + arch + ".log")).write_text(output)
        # java.lang.Object has no main; this error occurs after VM initialization.
        assert (
            process.returncode == 1 and "Unable to find static main(String[])" in output
        ), output
        for failure in (
            "fall back",
            "Could not create image space",
            "checksum",
            "Can't mmap dex",
            "Unknown argument",
        ):
            assert failure not in output, output
        for stem in expected_stems:
            assert (
                "image_filename=" + args.staging + "/" + arch + "/" + stem + ".art"
                in output
            ), output
        assert "BootImageLoader::LoadFromSystem exiting" in output, output
        result[arch] = {
            "loaded_extensions": len(expected_stems),
            "elapsed_seconds": elapsed,
            "compilation_enabled": False,
            "command": command,
            "log_sha256": hashlib.sha256(output.encode()).hexdigest(),
        }
    capture_provenance(path, manifests, artifacts_snapshot)
    verification_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
