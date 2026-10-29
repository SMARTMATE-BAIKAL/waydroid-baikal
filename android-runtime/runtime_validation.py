"""Bind ART validation results to the exact candidate generation.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import hashlib
import json
from pathlib import Path


def require_runtime_checks():
    if not __debug__:
        raise RuntimeError("Run ART build tools without Python optimization (-O)")


def file_digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_manifest(candidate, name):
    payload = (candidate / name).read_bytes()
    return json.loads(payload), hashlib.sha256(payload).hexdigest()


def boot_artifact_hashes(manifest, architectures=("arm", "arm64")):
    artifacts = {"framework.jar": manifest["aligned_framework_sha256"]}
    for arch in architectures:
        for name, info in manifest["results"][arch]["artifacts"].items():
            if Path(name).name != name:
                raise ValueError("Invalid boot artifact name: " + name)
            artifacts[arch + "/" + name] = info["sha256"]
    return artifacts


def server_artifact_hashes(manifest):
    artifacts = {"system-server/services.jar": manifest["aligned_services_sha256"]}
    for component in manifest["components"].values():
        for name, info in component["artifacts"].items():
            if Path(name).name != name:
                raise ValueError("Invalid system-server artifact name: " + name)
            artifacts["system-server/oat/arm64/" + name] = info["sha256"]
    return artifacts


def validate_server_boot_chain(boot, server):
    """Return whether a legacy build needs fresh ART compatibility evidence."""
    recorded = server.get("staged_boot_sha256")
    if recorded is None:
        return True
    if recorded != boot_artifact_hashes(boot, ("arm64",)):
        raise ValueError(
            "System-server artifacts were built for another boot generation"
        )
    return False


def capture_provenance(candidate, manifests, artifacts):
    """Verify all files and return a stable snapshot, never an inferred build record."""
    for group in (manifests, artifacts):
        for name, checksum in group.items():
            if file_digest(candidate / name) != checksum:
                raise ValueError(
                    "Candidate changed or has a checksum mismatch: " + name
                )
    return {
        "manifests_sha256": dict(manifests),
        "artifacts_sha256": dict(artifacts),
    }


def require_verification(candidate, verification, manifests, artifacts):
    current = capture_provenance(candidate, manifests, artifacts)
    if verification.get("provenance") != current:
        raise ValueError(
            "ART verification is missing or stale; verify this candidate again"
        )


def require_server_verification(candidate, verification, boot, server, manifests):
    legacy = validate_server_boot_chain(boot, server)
    artifacts = boot_artifact_hashes(boot)
    artifacts.update(server_artifact_hashes(server))
    require_verification(candidate, verification, manifests, artifacts)
    if verification.get("legacy_build_manifest_without_boot_hashes") is not legacy:
        raise ValueError(
            "System-server validation does not identify its build provenance"
        )
