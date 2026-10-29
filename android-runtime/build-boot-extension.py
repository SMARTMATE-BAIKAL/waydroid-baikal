#!/usr/bin/env python3
"""Build matched Android boot extensions without changing the running system.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import zipfile

ATTACH = ["lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--"]


def android(*args):
    return subprocess.check_output(ATTACH + list(args), text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def align_archive(source, target):
    """Align stored entries and verify all uncompressed payloads are unchanged."""
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as result:
        result.comment = original.comment
        for info in original.infolist():
            member = copy.copy(info)
            if member.compress_type == zipfile.ZIP_STORED:
                offset = (
                    result.fp.tell()
                    + 30
                    + len(member.filename.encode("utf-8"))
                    + len(member.extra)
                )
                padding = -offset % 4
                if padding:
                    member.extra += struct.pack("<HH", 0xD935, padding) + bytes(padding)
            result.writestr(member, original.read(info))
    manifest = []
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(
        target
    ) as result, target.open("rb") as raw:
        assert original.namelist() == result.namelist(), "Archive member list changed"
        for info in result.infolist():
            payload = result.read(info)
            assert payload == original.read(info.filename), info.filename
            raw.seek(info.header_offset + 26)
            name_length, extra_length = struct.unpack("<HH", raw.read(4))
            offset = info.header_offset + 30 + name_length + extra_length
            if info.compress_type == zipfile.ZIP_STORED:
                assert offset % 4 == 0, info.filename
            manifest.append(
                {
                    "name": info.filename,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                    "offset": offset,
                }
            )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", default="/data/local/tmp/baikal-art-candidate")
    parser.add_argument(
        "--architectures", nargs="+", choices=("arm64", "arm"), default=("arm64", "arm")
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error(
            "Run as root to access Android build inputs and its mount namespace"
        )
    if (
        not args.staging.startswith("/data/local/tmp/")
        or ".." in Path(args.staging).parts
    ):
        parser.error("Staging must be a dedicated /data/local/tmp directory")
    root = Path("/var/lib/waydroid/rootfs")
    data_path = Path("/var/lib/waydroid/data")
    work = data_path / args.staging.removeprefix("/data/")
    work.mkdir(parents=True, exist_ok=True)
    environ = android("/system/bin/cat", "/data/system/environ/classpath")
    classpath = next(
        line.split(" ", 2)[2]
        for line in environ.splitlines()
        if line.startswith("export DEX2OATBOOTCLASSPATH ")
    )
    locations = classpath.split(":")
    first = locations.index("/system/framework/framework.jar")
    framework = root / "system/framework/framework.jar"
    aligned = work / "framework.jar"
    members = align_archive(framework, aligned)
    actual = locations.copy()
    actual[first] = args.staging + "/framework.jar"
    provenance_paths = locations + [
        "/apex/com.android.art/bin/dex2oat32",
        "/apex/com.android.art/bin/dex2oat64",
        "/apex/com.android.art/lib/libart.so",
        "/apex/com.android.art/lib64/libart.so",
    ]
    for arch in ("arm", "arm64"):
        for stem in (
            "boot",
            "boot-core-libart",
            "boot-okhttp",
            "boot-bouncycastle",
            "boot-apache-xml",
        ):
            for suffix in ("art", "oat", "vdex"):
                provenance_paths.append(
                    "/system/framework/" + arch + "/" + stem + "." + suffix
                )
    provenance = android("/system/bin/sha256sum", *provenance_paths)
    record = {
        "source_framework_sha256": digest(framework),
        "aligned_framework_sha256": digest(aligned),
        "members": members,
        "classpath": locations,
        "commands": {},
        "results": {},
        "provenance_sha256": provenance.splitlines(),
        "android_fingerprint": android("/system/bin/getprop", "ro.build.fingerprint"),
    }
    for arch in args.architectures:
        output = work / arch
        output.mkdir(exist_ok=True)
        executable = "/apex/com.android.art/bin/dex2oat" + (
            "64" if arch == "arm64" else "32"
        )
        command = [
            "/system/bin/env",
            "ANDROID_ROOT=/system",
            "ANDROID_DATA=/data",
            "ANDROID_ART_ROOT=/apex/com.android.art",
            "ANDROID_I18N_ROOT=/apex/com.android.i18n",
            "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata",
            executable,
            "--runtime-arg",
            "-Xbootclasspath:" + ":".join(actual),
            "--runtime-arg",
            "-Xbootclasspath-locations:" + classpath,
            "--boot-image=/system/framework/boot.art",
        ]
        for path, location in zip(actual[first:], locations[first:]):
            command += ["--dex-file=" + path, "--dex-location=" + location]
        command += [
            "--image=" + args.staging + "/" + arch + "/boot.art",
            "--oat-file=" + args.staging + "/" + arch + "/boot.oat",
            "--multi-image",
            "--image-format=uncompressed",
            "--compiler-filter=speed",
            "--instruction-set=" + arch,
            "--instruction-set-variant=cortex-a57",
            "--instruction-set-features="
            + (
                "-crc,-lse,-fp16,-dotprod,-sve"
                if arch == "arm64"
                else "div,atomic_ldrd_strd,armv8a"
            ),
            "--runtime-arg",
            "-Xms64m",
            "--runtime-arg",
            "-Xmx512m",
            "--dirty-image-objects=/system/etc/dirty-image-objects",
            "--generate-mini-debug-info",
            "--generate-build-id",
            "--abort-on-hard-verifier-error",
            "-j8",
        ]
        record["commands"][arch] = command
        (work / "build-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        started = time.monotonic()
        print("Building " + arch, flush=True)
        with (work / (arch + ".log")).open("w") as log:
            process = subprocess.run(
                ATTACH + command, stdout=log, stderr=subprocess.STDOUT
            )
        record["results"][arch] = {
            "returncode": process.returncode,
            "elapsed_seconds": time.monotonic() - started,
            "artifacts": {
                path.name: {"sha256": digest(path), "size": path.stat().st_size}
                for path in sorted(output.iterdir())
                if path.is_file()
            },
        }
        (work / "build-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record["results"][arch]), flush=True)
        if process.returncode:
            raise SystemExit(process.returncode)
    print("Candidate only; no live files replaced. Staging: " + str(work), flush=True)


if __name__ == "__main__":
    main()
