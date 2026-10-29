#!/usr/bin/env python3
"""Package verified Android runtime artifacts as a single managed overlay.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

from native_validation import require_native_verification
from runtime_validation import (
    boot_artifact_hashes,
    read_manifest,
    require_runtime_checks,
    require_server_verification,
    require_verification,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    require_runtime_checks()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--hwc", type=Path, required=True)
    parser.add_argument("--hwc-sha256", required=True)
    parser.add_argument("--hwc-commit", required=True)
    parser.add_argument("--native-input", type=Path, required=True)
    parser.add_argument("--input-init", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    packaging = args.output_dir or base.parent / "packaging"
    packaging.mkdir(exist_ok=True)
    recipe_path = packaging / "PKGBUILD"
    recipe = recipe_path.read_text()
    if not all(re.search(r"^" + name + r"=.*$", recipe, re.MULTILINE)
               for name in ("_runtime_payload", "_runtime_sha256")):
        raise ValueError("Unified PKGBUILD is missing runtime payload inputs")
    manifest, boot_hash = read_manifest(args.candidate, "build-manifest.json")
    verification = json.loads((args.candidate / "verification.json").read_text())
    require_verification(
        args.candidate,
        verification,
        {"build-manifest.json": boot_hash},
        boot_artifact_hashes(manifest),
    )
    assert digest(args.hwc) == args.hwc_sha256, "Unexpected HWC binary"
    native_verification = json.loads(
        (args.native_input / "native-input-verification.json").read_text()
    )
    native_manifest = require_native_verification(
        args.native_input, native_verification, args.hwc_sha256, digest(args.input_init)
    )
    assert (
        digest(args.candidate / "framework.jar") == manifest["aligned_framework_sha256"]
    )
    files = {
        "var/lib/waydroid/overlay_rw/system/system/framework/framework.jar": args.candidate
        / "framework.jar",
        "var/lib/waydroid/overlay_rw/vendor/lib64/hw/hwcomposer.waydroid.so": args.hwc,
        "var/lib/waydroid/overlay_rw/vendor/etc/init/waydroid-input.rc": args.input_init,
    }
    for arch, libdir in (("arm", "lib"), ("arm64", "lib64")):
        files["var/lib/waydroid/overlay_rw/system/system/" + libdir + "/libinputreader.so"] = (
            args.native_input / native_manifest["results"][arch]["artifact"]
        )
    expected_stems = {"boot-" + Path(item).stem for item in manifest["classpath"][5:]}
    expected_files = {
        stem + suffix for stem in expected_stems for suffix in (".art", ".oat", ".vdex")
    }
    for arch in ("arm", "arm64"):
        assert verification[arch]["loaded_extensions"] == 7
        assert not verification[arch]["compilation_enabled"]
        assert manifest["results"][arch]["returncode"] == 0
        assert set(manifest["results"][arch]["artifacts"]) == expected_files
        for name, info in manifest["results"][arch]["artifacts"].items():
            artifact = args.candidate / arch / name
            assert digest(artifact) == info["sha256"], artifact
            files[
                "var/lib/waydroid/overlay_rw/system/system/framework/"
                + arch
                + "/"
                + name
            ] = artifact
    metadata = {
        "build-manifest.json": (args.candidate / "build-manifest.json").read_bytes(),
        "verification.json": (args.candidate / "verification.json").read_bytes(),
        "hwc-provenance.json": (
            json.dumps(
                {"sha256": args.hwc_sha256, "source_commit": args.hwc_commit,
                 "input_init_sha256": digest(args.input_init)}, indent=2
            )
            + "\n"
        ).encode(),
        "README.md": (base / "README.md").read_bytes(),
    }
    for name in ("native-input-manifest.json", "native-input-verification.json"):
        metadata[name] = (args.native_input / name).read_bytes()
    for path in (base / "patches").glob("*.patch"):
        metadata["patches/" + path.name] = path.read_bytes()
    server, server_hash = read_manifest(args.candidate, "system-server-manifest.json")
    server_verification = json.loads(
        (args.candidate / "system-server-verification.json").read_text()
    )
    require_server_verification(
        args.candidate,
        server_verification,
        manifest,
        server,
        {
            "build-manifest.json": boot_hash,
            "system-server-manifest.json": server_hash,
        },
    )
    assert server["complete_systemserver_chain"]
    expected_components = set(
        server["systemserverclasspath"] + server["standalone_systemserver_jars"]
    )
    assert set(server["components"]) == expected_components
    assert set(server_verification["components"]) == expected_components
    aligned_services = args.candidate / "system-server/services.jar"
    assert digest(aligned_services) == server["aligned_services_sha256"]
    files["var/lib/waydroid/overlay_rw/system/system/framework/services.jar"] = (
        aligned_services
    )
    for location, component in server["components"].items():
        assert component["returncode"] == 0
        assert server_verification["components"][location]["dexopt_needed"] == 0
        assert server_verification["components"][location]["compiler_filter"] == "speed"
        assert len(component["artifacts"]) == 2
        for name, info in component["artifacts"].items():
            assert Path(name).name == name
            artifact = args.candidate / "system-server/oat/arm64" / name
            assert digest(artifact) == info["sha256"]
            files[
                "var/lib/waydroid/overlay_rw/system/system/framework/oat/arm64/" + name
            ] = artifact
    for name in ("system-server-manifest.json", "system-server-verification.json"):
        metadata[name] = (args.candidate / name).read_bytes()
    archive = packaging / "waydroid-android-runtime-20261028.tar.zst"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=packaging, prefix=archive.name + ".", suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            compressor = subprocess.Popen(
                ["zstd", "-q", "-T4", "-6", "-c"],
                stdin=subprocess.PIPE,
                stdout=output,
            )
            try:
                with tarfile.open(fileobj=compressor.stdin, mode="w|") as tar:
                    for name, path in sorted(files.items()):
                        info = tarfile.TarInfo(name)
                        info.size = path.stat().st_size
                        info.mode = 0o644
                        with path.open("rb") as content:
                            tar.addfile(info, content)
                    for name, payload in sorted(metadata.items()):
                        info = tarfile.TarInfo(
                            "usr/share/waydroid-baikal/android-runtime/" + name
                        )
                        info.size = len(payload)
                        info.mode = 0o644
                        tar.addfile(info, io.BytesIO(payload))
            finally:
                compressor.stdin.close()
                compression_status = compressor.wait()
            if compression_status:
                raise RuntimeError("Archive compression failed")
        # Preserve the published archive if validation fails during packing.
        require_verification(
            args.candidate,
            verification,
            {"build-manifest.json": boot_hash},
            boot_artifact_hashes(manifest),
        )
        require_server_verification(
            args.candidate,
            server_verification,
            manifest,
            server,
            {
                "build-manifest.json": boot_hash,
                "system-server-manifest.json": server_hash,
            },
        )
        if digest(args.hwc) != args.hwc_sha256:
            raise ValueError("HWC binary changed while packing")
        require_native_verification(
            args.native_input, native_verification, args.hwc_sha256, digest(args.input_init)
        )
        checksum = digest(temporary)
        temporary.chmod(0o644)
        temporary.replace(archive)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    recipe = re.sub(r"^_runtime_payload=.*$", "_runtime_payload=" + archive.name,
                    recipe, flags=re.MULTILINE)
    recipe = re.sub(r"^_runtime_sha256=.*$", "_runtime_sha256=" + checksum,
                    recipe, flags=re.MULTILINE)
    recipe_path.write_text(recipe)
    (base / "payload-sha256sums").write_text(
        "".join(
            digest(path) + "  " + name + "\n" for name, path in sorted(files.items())
        )
    )
    print(
        json.dumps(
            {
                "archive": str(archive),
                "sha256": checksum,
                "runtime_files": len(files),
                "uncompressed_runtime_bytes": sum(
                    path.stat().st_size for path in files.values()
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
