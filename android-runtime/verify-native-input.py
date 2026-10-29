#!/usr/bin/env python3
"""Load both native InputReader ABIs with live Android dependencies privately.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from native_validation import native_artifact_hashes
from runtime_validation import capture_provenance, file_digest, read_manifest


def run(command):
    return subprocess.check_output([str(x) for x in command], text=True)


def elf_contract(path, *, platform_cxx=True):
    header = run(["llvm-readelf", "-h", path])
    dynamic = run(["llvm-readelf", "-d", path])
    symbols = run(["llvm-nm", "-D", path])
    if platform_cxx and ("__ndk1" in symbols or "ne180000" in symbols):
        raise ValueError("NDK C++ ABI leaked into platform library: " + str(path))
    if any(tag in dynamic for tag in ("(RPATH)", "(RUNPATH)", "(TEXTREL)")):
        raise ValueError("Unexpected native loader flags: " + str(path))
    return {
        "class": re.search(r"Class:\s+(\S+)", header)[1],
        "machine": re.search(r"Machine:\s+(.+)", header)[1].strip(),
        "soname": re.findall(r"\(SONAME\).*\[(.+)\]", dynamic),
        "needed": re.findall(r"\(NEEDED\).*\[(.+)\]", dynamic),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--hwc", type=Path, required=True)
    parser.add_argument("--input-init", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--compiler", default="/usr/lib/llvm18/bin/clang")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo to enter Android's private mount namespace")
    base = Path(__file__).resolve().parent
    report_path = args.candidate / "native-input-verification.json"
    report_path.unlink(missing_ok=True)
    manifest, manifest_hash = read_manifest(args.candidate, "native-input-manifest.json")
    artifacts = native_artifact_hashes(manifest)
    provenance = capture_provenance(
        args.candidate, {"native-input-manifest.json": manifest_hash}, artifacts,
    )
    root = Path("/var/lib/waydroid/rootfs")
    staging = Path("/var/lib/waydroid/data/local/tmp/baikal-input-candidate")
    staging.mkdir(parents=True, exist_ok=True)
    android_stage = "/data/local/tmp/baikal-input-candidate"
    toolchain = args.ndk / "toolchains/llvm/prebuilt/linux-x86_64"
    report = {
        "provenance": provenance, "hwc_sha256": file_digest(args.hwc),
        "input_init_sha256": file_digest(args.input_init), "results": {},
    }
    hwc_original = root / "vendor/lib64/hw/hwcomposer.waydroid.so"
    # HWC reuses its existing objects and ABI provider. Require its established
    # contract without applying the new InputReader's build policy retroactively.
    if elf_contract(args.hwc, platform_cxx=False) != elf_contract(hwc_original, platform_cxx=False):
        raise ValueError("HWC ELF dependency contract changed")
    original_undefined = set(run(["llvm-nm", "-D", "--undefined-only", hwc_original]).splitlines())
    candidate_undefined = set(run(["llvm-nm", "-D", "--undefined-only", args.hwc]).splitlines())
    added = {line.split()[-1] for line in candidate_undefined - original_undefined}
    pid = run(["lxc-info", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH"]).strip()
    bionic = Path("/proc") / pid / "root/apex/com.android.runtime/lib64/bionic/libc.so"
    exports = {
        line.split()[-1].replace("@@", "@")
        for line in run(["llvm-nm", "-D", "--defined-only", bionic]).splitlines() if line.split()
    }
    if added - exports:
        raise ValueError("HWC introduces unverified dynamic symbols: " + str(added - exports))
    report["hwc_original_sha256"] = file_digest(hwc_original)
    report["hwc_elf"] = elf_contract(args.hwc, platform_cxx=False)
    report["hwc_added_symbols"] = sorted(added)
    report["hwc_symbol_provider"] = {"path": str(bionic), "sha256": file_digest(bionic)}
    for arch, target, libdir, elfclass, machine in (
        ("arm64", "aarch64-linux-android33", "lib64", "ELF64", "AArch64"),
        ("arm", "armv7a-linux-androideabi33", "lib", "ELF32", "ARM"),
    ):
        candidate = args.candidate / arch / "libinputreader.so"
        live = root / "system" / libdir / "libinputreader.so"
        live_before = file_digest(live)
        if live_before not in {
            manifest["results"][arch]["original_sha256"],
            manifest["results"][arch]["sha256"],
        }:
            raise ValueError("Live InputReader changed since the build: " + arch)
        for name, checksum in manifest["results"][arch]["linked_libraries_sha256"].items():
            if file_digest(root / "system" / libdir / name) != checksum:
                raise ValueError("Android build dependency changed: " + name)
        contract = elf_contract(candidate)
        if (contract["class"], contract["machine"], contract["soname"]) != (
            elfclass, machine, ["libinputreader.so"],
        ) or contract["needed"] != elf_contract(live)["needed"]:
            raise ValueError("InputReader ELF contract differs: " + arch)
        staged_library = staging / (arch + "-libinputreader.so")
        shutil.copyfile(candidate, staged_library)
        staged_library.chmod(0o644)
        probe = staging / (arch + "-loader-probe")
        command = [
            args.compiler, "--target=" + target,
            "--sysroot=" + str(toolchain / "sysroot"),
            "-resource-dir=" + str(toolchain / "lib/clang/18"),
            "-march=armv8-a", "-mtune=cortex-a57", "-O2", "-fno-lto",
            "-fuse-ld=lld", "-Wl,-z,relro,-z,now",
            str(base / "native-loader-probe.c"), "-ldl", "-o", str(probe),
        ]
        subprocess.run(command, check=True)
        probe.chmod(0o755)
        consumers = ["libinputflinger.so", "libandroid_servers.so"]
        script = [
            "set -eu",
            "/system/bin/mount -o private,rec none /",
            shlex.join([
                "/system/bin/mount", "-o", "bind",
                android_stage + "/" + staged_library.name,
                "/system/" + libdir + "/libinputreader.so",
            ]),
            shlex.join([
                android_stage + "/" + probe.name,
                "libinputreader.so", *consumers,
            ]),
        ]
        process = subprocess.run([
            "lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--",
            "/system/bin/toybox", "unshare", "-m", "/system/bin/sh", "-c", "\n".join(script),
        ], capture_output=True, text=True, timeout=30)
        result = {
            "returncode": process.returncode, "stdout": process.stdout, "stderr": process.stderr,
            "elf": contract, "probe_command": command,
            "probe_sha256": file_digest(probe), "consumers": {
                name: file_digest(root / "system" / libdir / name) for name in consumers
            },
        }
        (args.candidate / (arch + "-loader.log")).write_text(json.dumps(result, indent=2) + "\n")
        if process.returncode != 0:
            raise ValueError("Android native loader rejected " + arch + ": " + process.stderr)
        if file_digest(live) != live_before:
            raise ValueError("Private native verification changed the live library")
        report["results"][arch] = result
    capture_provenance(args.candidate, provenance["manifests_sha256"], artifacts)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
