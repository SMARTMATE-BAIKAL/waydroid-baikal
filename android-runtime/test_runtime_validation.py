"""Reject mixed or unverified ART generations before packaging.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime_validation import (
    boot_artifact_hashes,
    capture_provenance,
    file_digest,
    require_server_verification,
    require_verification,
    server_artifact_hashes,
    validate_server_boot_chain,
)

PACKER_SPEC = importlib.util.spec_from_file_location(
    "runtime_packer", Path(__file__).with_name("pack-candidate.py")
)
PACKER = importlib.util.module_from_spec(PACKER_SPEC)
PACKER_SPEC.loader.exec_module(PACKER)


class RuntimeGenerationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.candidate = Path(self.directory.name)
        for name in (
            "framework.jar",
            "arm/boot-framework.art",
            "arm64/boot-framework.art",
            "system-server/services.jar",
            "system-server/oat/arm64/services.odex",
            "system-server/oat/arm64/services.vdex",
        ):
            path = self.candidate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("generation-one:" + name).encode())
        self.boot = {
            "aligned_framework_sha256": file_digest(self.candidate / "framework.jar"),
            "results": {
                arch: {
                    "artifacts": {
                        "boot-framework.art": {
                            "sha256": file_digest(
                                self.candidate / arch / "boot-framework.art"
                            )
                        }
                    }
                }
                for arch in ("arm", "arm64")
            },
        }
        self.server = {
            "staged_boot_sha256": boot_artifact_hashes(self.boot, ("arm64",)),
            "aligned_services_sha256": file_digest(
                self.candidate / "system-server/services.jar"
            ),
            "components": {
                "/system/framework/services.jar": {
                    "artifacts": {
                        name: {
                            "sha256": file_digest(
                                self.candidate / "system-server/oat/arm64" / name
                            )
                        }
                        for name in ("services.odex", "services.vdex")
                    }
                }
            },
        }
        self.write_manifests()

    def write_manifests(self):
        for name, data in (
            ("build-manifest.json", self.boot),
            ("system-server-manifest.json", self.server),
        ):
            (self.candidate / name).write_text(json.dumps(data))
        self.manifests = {
            name: file_digest(self.candidate / name)
            for name in ("build-manifest.json", "system-server-manifest.json")
        }

    def verification(self):
        artifacts = boot_artifact_hashes(self.boot)
        artifacts.update(server_artifact_hashes(self.server))
        return {
            "provenance": capture_provenance(self.candidate, self.manifests, artifacts),
            "legacy_build_manifest_without_boot_hashes": (
                "staged_boot_sha256" not in self.server
            ),
        }

    def require_server(self, verification):
        require_server_verification(
            self.candidate, verification, self.boot, self.server, self.manifests
        )

    def test_unchanged_generation_is_accepted(self):
        self.require_server(self.verification())

    def test_boot_rebuild_cannot_reuse_old_server_generation(self):
        verification = self.verification()
        path = self.candidate / "arm64/boot-framework.art"
        path.write_bytes(b"generation-two")
        self.boot["results"]["arm64"]["artifacts"]["boot-framework.art"]["sha256"] = (
            file_digest(path)
        )
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "another boot generation"):
            self.require_server(verification)

    def test_recompiled_matching_generation_needs_new_validation(self):
        verification = self.verification()
        path = self.candidate / "arm64/boot-framework.art"
        path.write_bytes(b"generation-two")
        self.boot["results"]["arm64"]["artifacts"]["boot-framework.art"]["sha256"] = (
            file_digest(path)
        )
        self.server["staged_boot_sha256"] = boot_artifact_hashes(self.boot, ("arm64",))
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.require_server(verification)

    def test_artifact_tampering_is_rejected(self):
        verification = self.verification()
        (self.candidate / "system-server/oat/arm64/services.odex").write_bytes(
            b"different output"
        )
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.require_server(verification)

    def test_manifest_change_during_validation_is_rejected(self):
        verification = self.verification()
        with (self.candidate / "system-server-manifest.json").open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.require_server(verification)

    def test_legacy_manifest_requires_new_explicit_validation(self):
        del self.server["staged_boot_sha256"]
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.require_server({"components": {}})
        self.require_server(self.verification())

    def test_legacy_flag_cannot_bypass_recorded_boot_mismatch(self):
        verification = self.verification()
        verification["legacy_build_manifest_without_boot_hashes"] = True
        self.server["staged_boot_sha256"]["framework.jar"] = "0" * 64
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "another boot generation"):
            self.require_server(verification)

    def test_false_legacy_claim_is_rejected(self):
        verification = self.verification()
        verification["legacy_build_manifest_without_boot_hashes"] = True
        with self.assertRaisesRegex(ValueError, "build provenance"):
            self.require_server(verification)

    def test_boot_validation_must_bind_all_candidate_artifacts(self):
        artifacts = boot_artifact_hashes(self.boot)
        manifests = {"build-manifest.json": self.manifests["build-manifest.json"]}
        verification = {
            "provenance": capture_provenance(self.candidate, manifests, artifacts)
        }
        require_verification(self.candidate, verification, manifests, artifacts)
        del verification["provenance"]["artifacts_sha256"]["arm/boot-framework.art"]
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            require_verification(self.candidate, verification, manifests, artifacts)

    def test_staged_boot_field_is_not_rewritten(self):
        original = copy.deepcopy(self.server)
        self.assertFalse(validate_server_boot_chain(self.boot, self.server))
        self.assertEqual(self.server, original)
        del self.server["staged_boot_sha256"]
        original = copy.deepcopy(self.server)
        self.assertTrue(validate_server_boot_chain(self.boot, self.server))
        self.assertEqual(self.server, original)

    def test_optimized_python_cannot_disable_art_checks(self):
        for script in (
            "verify-boot-extension.py",
            "verify-system-server.py",
            "pack-candidate.py",
        ):
            with self.subTest(script=script):
                process = subprocess.run(
                    [
                        sys.executable,
                        "-O",
                        str(Path(__file__).with_name(script)),
                        "--help",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("without Python optimization", process.stderr)


class ArchivePublicationTest(unittest.TestCase):
    def test_failed_final_validation_preserves_published_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.mkdir()
            base = root / "recipe"
            base.mkdir()
            (base / "README.md").write_text("Test package inputs\n")
            packaging = base / "packaging"
            packaging.mkdir()
            archive = packaging / "waydroid-android-runtime-20261028.tar.zst"
            archive.write_bytes(b"previously verified source archive")
            recipe = packaging / "PKGBUILD"
            previous_recipe = "# previous recipe\n_runtime_payload=previous.tar.zst\n_runtime_sha256=previous\n"
            recipe.write_text(previous_recipe)

            def artifact(name):
                path = candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
                return {"sha256": file_digest(path), "size": path.stat().st_size}

            classpath = ["/base" + str(n) + ".jar" for n in range(5)] + [
                "/system/framework/" + name + ".jar"
                for name in (
                    "framework",
                    "graphics",
                    "ext",
                    "phone",
                    "voip",
                    "ims",
                    "icu",
                )
            ]
            boot = {
                "aligned_framework_sha256": artifact("framework.jar")["sha256"],
                "classpath": classpath,
                "results": {
                    arch: {
                        "returncode": 0,
                        "artifacts": {
                            "boot-"
                            + Path(location).stem
                            + suffix: artifact(
                                arch + "/boot-" + Path(location).stem + suffix
                            )
                            for location in classpath[5:]
                            for suffix in (".art", ".oat", ".vdex")
                        },
                    }
                    for arch in ("arm", "arm64")
                },
            }
            location = "/system/framework/services.jar"
            server = {
                "staged_boot_sha256": boot_artifact_hashes(boot, ("arm64",)),
                "aligned_services_sha256": artifact("system-server/services.jar")[
                    "sha256"
                ],
                "complete_systemserver_chain": True,
                "systemserverclasspath": [location],
                "standalone_systemserver_jars": [],
                "components": {
                    location: {
                        "returncode": 0,
                        "artifacts": {
                            name: artifact("system-server/oat/arm64/" + name)
                            for name in ("services.odex", "services.vdex")
                        },
                    }
                },
            }
            for name, manifest in (
                ("build-manifest.json", boot),
                ("system-server-manifest.json", server),
            ):
                (candidate / name).write_text(json.dumps(manifest))
            manifests = {
                name: file_digest(candidate / name)
                for name in ("build-manifest.json", "system-server-manifest.json")
            }
            boot_verification = {
                "provenance": capture_provenance(
                    candidate,
                    {"build-manifest.json": manifests["build-manifest.json"]},
                    boot_artifact_hashes(boot),
                ),
                "arm": {"loaded_extensions": 7, "compilation_enabled": False},
                "arm64": {"loaded_extensions": 7, "compilation_enabled": False},
            }
            artifacts = boot_artifact_hashes(boot)
            artifacts.update(server_artifact_hashes(server))
            server_verification = {
                "provenance": capture_provenance(candidate, manifests, artifacts),
                "legacy_build_manifest_without_boot_hashes": False,
                "components": {
                    location: {"dexopt_needed": 0, "compiler_filter": "speed"}
                },
            }
            for name, report in (
                ("verification.json", boot_verification),
                ("system-server-verification.json", server_verification),
            ):
                (candidate / name).write_text(json.dumps(report))
            hwc = root / "hwcomposer.so"
            hwc.write_bytes(b"reviewed hwc")
            input_init = root / "waydroid-input.rc"
            input_init.write_text("on init\n    mkdir /dev/input 0770 system system\n")
            native = {"results": {}}
            for arch in ("arm", "arm64"):
                name = arch + "/libinputreader.so"
                native["results"][arch] = {"artifact": name, **artifact(name)}
            native_name = "native-input-manifest.json"
            (candidate / native_name).write_text(json.dumps(native))
            native_report = {
                "provenance": capture_provenance(
                    candidate, {native_name: file_digest(candidate / native_name)},
                    {item["artifact"]: item["sha256"] for item in native["results"].values()},
                ),
                "hwc_sha256": file_digest(hwc),
                "input_init_sha256": file_digest(input_init),
                "results": {arch: {"returncode": 0} for arch in ("arm", "arm64")},
            }
            (candidate / "native-input-verification.json").write_text(json.dumps(native_report))
            argv = [
                "pack-candidate.py",
                "--output-dir",
                str(packaging),
                "--candidate",
                str(candidate),
                "--input-init",
                str(input_init),
                "--hwc",
                str(hwc),
                "--hwc-sha256",
                file_digest(hwc),
                "--hwc-commit",
                "test-commit",
                "--native-input",
                str(candidate),
            ]
            real_check = PACKER.require_server_verification
            calls = 0

            def fail_second_check(*args):
                nonlocal calls
                calls += 1
                real_check(*args)
                if calls == 2:
                    raise ValueError("Candidate changed during packing")

            with (
                patch.object(PACKER, "__file__", str(base / "pack-candidate.py")),
                patch.object(PACKER, "require_server_verification", fail_second_check),
                patch.object(sys, "argv", argv),
                patch("sys.stdout", new_callable=io.StringIO),
                self.assertRaisesRegex(ValueError, "changed during packing"),
            ):
                PACKER.main()
            self.assertEqual(calls, 2)
            self.assertEqual(
                archive.read_bytes(), b"previously verified source archive"
            )
            self.assertEqual(recipe.read_text(), previous_recipe)
            self.assertFalse(list(packaging.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
