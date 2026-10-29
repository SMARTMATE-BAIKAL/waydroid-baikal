#!/usr/bin/env python3
"""Precompile the preserved custom system-server DEX against matched boot images.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time

SPEC = importlib.util.spec_from_file_location(
    "boot_extension_builder", Path(__file__).with_name("build-boot-extension.py")
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", default="/data/local/tmp/baikal-art-candidate")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Compile the complete system-server dependency chain",
    )
    args = parser.parse_args()
    if (
        not args.staging.startswith("/data/local/tmp/")
        or ".." in Path(args.staging).parts
    ):
        parser.error("Staging must be a dedicated /data/local/tmp directory")
    work = Path("/var/lib/waydroid/data") / args.staging.removeprefix("/data/")
    directory = work / "system-server"
    directory.mkdir(exist_ok=True)
    output = directory / "oat/arm64"
    output.mkdir(parents=True, exist_ok=True)
    exports = BUILDER.android("/system/bin/cat", "/data/system/environ/classpath")
    environment = {
        line.split(" ", 2)[1]: line.split(" ", 2)[2] for line in exports.splitlines()
    }
    bootclasspath = environment["BOOTCLASSPATH"]
    boot_manifest = json.loads((work / "build-manifest.json").read_text())
    staged_boot = {"framework.jar": boot_manifest["aligned_framework_sha256"]}
    for name, info in boot_manifest["results"]["arm64"]["artifacts"].items():
        staged_boot["arm64/" + name] = info["sha256"]
    for name, checksum in staged_boot.items():
        assert BUILDER.digest(work / name) == checksum, name
    actual_bootclasspath = bootclasspath.split(":")
    actual_bootclasspath[
        actual_bootclasspath.index("/system/framework/framework.jar")
    ] = (args.staging + "/framework.jar")
    systemclasspath = environment["SYSTEMSERVERCLASSPATH"].split(":")
    standalone = environment["STANDALONE_SYSTEMSERVER_JARS"].split(":")
    source = Path("/var/lib/waydroid/rootfs/system/framework/services.jar")
    target = directory / "services.jar"
    members = BUILDER.align_archive(source, target)
    prefix = [
        "/system/bin/env",
        "ANDROID_ROOT=/system",
        "ANDROID_DATA=/data",
        "ANDROID_ART_ROOT=/apex/com.android.art",
        "ANDROID_I18N_ROOT=/apex/com.android.i18n",
        "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata",
        "/apex/com.android.art/bin/dex2oat64",
        "--runtime-arg",
        "-Xbootclasspath:" + ":".join(actual_bootclasspath),
        "--runtime-arg",
        "-Xbootclasspath-locations:" + bootclasspath,
        "--boot-image=/system/framework/boot.art:"
        + args.staging
        + "/boot-framework.art",
    ]
    suffix = [
        "--compiler-filter=speed",
        "--instruction-set=arm64",
        "--instruction-set-variant=cortex-a57",
        "--instruction-set-features=-crc,-lse,-fp16,-dotprod,-sve",
        "--runtime-arg",
        "-Xms64m",
        "--runtime-arg",
        "-Xmx512m",
        "--generate-mini-debug-info",
        "--generate-build-id",
        "--abort-on-hard-verifier-error",
        "--compilation-reason=prebuilt",
        "-j8",
    ]
    provenance_paths = bootclasspath.split(":") + systemclasspath + standalone
    manifest_path = work / "system-server-manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    record = {
        "source_services_sha256": BUILDER.digest(source),
        "aligned_services_sha256": BUILDER.digest(target),
        "members": members,
        "bootclasspath": bootclasspath.split(":"),
        "staged_boot_sha256": staged_boot,
        "systemserverclasspath": systemclasspath,
        "standalone_systemserver_jars": standalone,
        "provenance_sha256": BUILDER.android(
            "/system/bin/sha256sum", *provenance_paths
        ).splitlines(),
        "components": {},
    }
    inputs_match = (
        previous.get("source_services_sha256") == record["source_services_sha256"]
        and previous.get("staged_boot_sha256") == staged_boot
        and set(previous.get("provenance_sha256", []))
        == set(record["provenance_sha256"])
    )
    selected = (
        systemclasspath + standalone if args.all else ["/system/framework/services.jar"]
    )
    for location in selected:
        if location.startswith("/apex/"):
            stem = location.removeprefix("/").replace("/", "@") + "@classes"
        else:
            stem = Path(location).stem
        if location in systemclasspath:
            context = (
                "PCL["
                + ":".join(systemclasspath[: systemclasspath.index(location)])
                + "]"
            )
        else:
            context = "PCL[];PCL[" + ":".join(systemclasspath) + "]"
        dex_file = (
            args.staging + "/system-server/services.jar"
            if location == "/system/framework/services.jar"
            else location
        )
        command = (
            prefix
            + [
                "--dex-file=" + dex_file,
                "--dex-location=" + location,
                "--oat-file="
                + args.staging
                + "/system-server/oat/arm64/"
                + stem
                + ".odex",
                "--class-loader-context=" + context,
                "--stored-class-loader-context=" + context,
            ]
            + suffix
        )
        old = previous.get("components", {}).get(location, {})
        if (
            not old
            and location == "/system/framework/services.jar"
            and previous.get("returncode") == 0
        ):
            old = previous
        artifacts = old.get("artifacts", {})
        reusable = (
            inputs_match
            and old.get("returncode") == 0
            and old.get("command") == command
            and len(artifacts) == 2
            and all(
                (output / name).is_file()
                and BUILDER.digest(output / name) == info["sha256"]
                for name, info in artifacts.items()
            )
        )
        if reusable:
            result = {
                key: old[key] for key in ("returncode", "elapsed_seconds", "artifacts")
            }
            result.update({"command": command, "context": context, "reused": True})
            print("Reusing " + location, flush=True)
        else:
            print("Compiling " + location, flush=True)
            started = time.monotonic()
            with (directory / (stem + ".log")).open("w") as log:
                process = subprocess.run(
                    BUILDER.ATTACH + command, stdout=log, stderr=subprocess.STDOUT
                )
            result = {
                "returncode": process.returncode,
                "elapsed_seconds": time.monotonic() - started,
                "command": command,
                "context": context,
                "reused": False,
                "artifacts": {},
            }
            for extension in (".odex", ".vdex"):
                artifact = output / (stem + extension)
                if artifact.is_file():
                    result["artifacts"][artifact.name] = {
                        "sha256": BUILDER.digest(artifact),
                        "size": artifact.stat().st_size,
                    }
        record["components"][location] = result
        manifest_path.write_text(json.dumps(record, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "location": location,
                    "returncode": result["returncode"],
                    "elapsed_seconds": result["elapsed_seconds"],
                }
            ),
            flush=True,
        )
        if result["returncode"]:
            raise SystemExit(result["returncode"])
    record["complete_systemserver_chain"] = args.all
    manifest_path.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
