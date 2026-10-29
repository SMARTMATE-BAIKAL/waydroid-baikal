"""Bind native Android input verification to the exact packaged generation.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

from runtime_validation import read_manifest, require_verification


def native_artifact_hashes(manifest):
    results = manifest["results"]
    if set(results) != {"arm", "arm64"}:
        raise ValueError("Both Android input ABIs are required")
    artifacts = {}
    for arch in ("arm", "arm64"):
        name = arch + "/libinputreader.so"
        if results[arch]["artifact"] != name:
            raise ValueError("Invalid native input artifact path")
        artifacts[name] = results[arch]["sha256"]
    return artifacts


def require_native_verification(candidate, verification, hwc_sha256, input_init_sha256):
    manifest, checksum = read_manifest(candidate, "native-input-manifest.json")
    require_verification(
        candidate, verification,
        {"native-input-manifest.json": checksum}, native_artifact_hashes(manifest),
    )
    if verification.get("hwc_sha256") != hwc_sha256:
        raise ValueError("HWC differs from the verified native input generation")
    if verification.get("input_init_sha256") != input_init_sha256:
        raise ValueError("Android init differs from the verified native input generation")
    for arch in ("arm", "arm64"):
        if verification.get("results", {}).get(arch, {}).get("returncode") != 0:
            raise ValueError("Native Android loader verification is missing: " + arch)
    return manifest
