#!/usr/bin/env python3
"""Check system-server preopt through ART in a private mount namespace.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time

from runtime_validation import (
    boot_artifact_hashes,
    capture_provenance,
    read_manifest,
    require_runtime_checks,
    server_artifact_hashes,
    validate_server_boot_chain,
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
    work = Path("/var/lib/waydroid/data") / args.staging.removeprefix("/data/")
    verification_path = work / "system-server-verification.json"
    verification_path.unlink(missing_ok=True)
    directory = work / "system-server"
    manifest, server_hash = read_manifest(work, "system-server-manifest.json")
    boot_manifest, boot_hash = read_manifest(work, "build-manifest.json")
    legacy = validate_server_boot_chain(boot_manifest, manifest)
    manifests = {
        "build-manifest.json": boot_hash,
        "system-server-manifest.json": server_hash,
    }
    artifacts_snapshot = boot_artifact_hashes(boot_manifest)
    artifacts_snapshot.update(server_artifact_hashes(manifest))
    provenance = capture_provenance(work, manifests, artifacts_snapshot)
    assert manifest["complete_systemserver_chain"]
    assert set(manifest["components"]) == set(
        manifest["systemserverclasspath"] + manifest["standalone_systemserver_jars"]
    )
    live_services = Path("/var/lib/waydroid/rootfs/system/framework/services.jar")
    before = hashlib.sha256(live_services.read_bytes()).hexdigest()
    assert before in (
        manifest["source_services_sha256"],
        manifest["aligned_services_sha256"],
    )
    stage = args.staging + "/system-server"
    actual_bootclasspath = manifest["bootclasspath"].copy()
    actual_bootclasspath[
        actual_bootclasspath.index("/system/framework/framework.jar")
    ] = (args.staging + "/framework.jar")
    # Recursively privatize first: candidate mounts cannot propagate to Android.
    script = [
        "set -eu",
        "/system/bin/mount -o private,rec none /",
        "/system/bin/mount -o bind "
        + shlex.quote(stage + "/services.jar")
        + " /system/framework/services.jar",
        "/system/bin/mount -o bind "
        + shlex.quote(stage + "/oat/arm64")
        + " /system/framework/oat/arm64",
    ]
    logs = {}
    for index, (location, component) in enumerate(manifest["components"].items()):
        for name, info in component["artifacts"].items():
            assert (
                hashlib.sha256(
                    (directory / "oat/arm64" / name).read_bytes()
                ).hexdigest()
                == info["sha256"]
            )
        log_name = "verify-" + str(index) + ".log"
        command = [
            "/system/bin/env",
            "ANDROID_ROOT=/system",
            "ANDROID_DATA=/data",
            "ANDROID_ART_ROOT=/apex/com.android.art",
            "ANDROID_I18N_ROOT=/apex/com.android.i18n",
            "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata",
            "/apex/com.android.art/bin/dexoptanalyzer",
            "--dex-file=" + location,
            "--isa=arm64",
            "--compiler-filter=speed",
            "--image=/system/framework/boot.art:"
            + args.staging
            + "/boot-framework.art",
            "--runtime-arg",
            "-Xbootclasspath:" + ":".join(actual_bootclasspath),
            "--runtime-arg",
            "-Xbootclasspath-locations:" + ":".join(manifest["bootclasspath"]),
            "--runtime-arg",
            "-Xuse-stderr-logger",
            "--runtime-arg",
            "-verbose:oat",
            "--class-loader-context=" + component["context"],
        ]
        script.append(
            shlex.join(command) + " >" + shlex.quote(stage + "/" + log_name) + " 2>&1"
        )
        logs[location] = log_name
    started = time.monotonic()
    process = subprocess.run(
        [
            "lxc-attach",
            "-P",
            "/var/lib/waydroid/lxc",
            "-n",
            "waydroid",
            "--",
            "/system/bin/toybox",
            "unshare",
            "-m",
            "/system/bin/sh",
            "-c",
            "\n".join(script),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert process.returncode == 0, (process.returncode, process.stdout, process.stderr)
    result = {
        "elapsed_seconds": time.monotonic() - started,
        "validation": "ART dexoptanalyzer with private candidate bind mounts",
        "provenance": provenance,
        "legacy_build_manifest_without_boot_hashes": legacy,
        "components": {},
    }
    for location, log_name in logs.items():
        output = (directory / log_name).read_text()
        assert "is kOatUpToDate with filter speed" in output, output
        for failure in (
            "Could not get required checksum",
            "Assuming dex checksums",
            "mismatch",
            "too short",
            "fall back",
            "Can't mmap dex",
        ):
            assert failure not in output, output
        result["components"][location] = {
            "dexopt_needed": 0,
            "compiler_filter": "speed",
            "log_sha256": hashlib.sha256(output.encode()).hexdigest(),
        }
    assert (
        hashlib.sha256(live_services.read_bytes()).hexdigest() == before
    ), "Live services.jar changed during verification"
    capture_provenance(work, manifests, artifacts_snapshot)
    verification_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
