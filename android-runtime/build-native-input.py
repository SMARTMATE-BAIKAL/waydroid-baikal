#!/usr/bin/env python3
"""Build matched Android 13 InputReader libraries without changing the live system.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess

from runtime_validation import file_digest, require_runtime_checks


def output(command):
    return subprocess.check_output([str(x) for x in command], text=True).strip()


def source_record(path, expected_patch=None):
    patch = subprocess.check_output(["git", "-C", str(path), "diff", "--binary"])
    if expected_patch is not None and patch != expected_patch.read_bytes():
        raise ValueError("Source tree does not match the reviewed patch: " + str(path))
    return {
        "path": str(path),
        "commit": output(["git", "-C", path, "rev-parse", "HEAD"]),
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
    }


def main():
    require_runtime_checks()
    parser = argparse.ArgumentParser(description=__doc__)
    cache = Path.home() / ".cache/waydroid-native-build"
    parser.add_argument("--source", type=Path, default=cache / "frameworks-native-lineage-20")
    parser.add_argument("--libcxx", type=Path, default=cache / "libcxx-lineage-20")
    parser.add_argument("--deps", type=Path, required=True)
    parser.add_argument("--fmt", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, default=cache / "android-ndk-r27c")
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=cache / "inputreader-build")
    parser.add_argument("--compiler", type=Path, default=Path("/usr/lib/llvm18/bin/clang++"))
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    args.output.mkdir(parents=True, exist_ok=True)
    toolchain = args.ndk / "toolchains/llvm/prebuilt/linux-x86_64"
    sysroot = toolchain / "sysroot"
    reader = args.source / "services/inputflinger/reader"
    includes = [base / "native-headers", sysroot / "usr/include"]
    includes += [reader / name for name in ("include", "mapper", "mapper/accumulator", "controller")]
    includes += [args.source / name for name in (
        "services/inputflinger/include", "include", "libs/input", "libs/arect/include",
        "libs/math/include", "libs/ui/include", "libs/gui/include", "libs/binder/include",
        "services/batteryservice/include",
    )]
    includes += [args.fmt / "include"]
    includes += [args.deps / name for name in (
        "libbase/include", "core/libcutils/include", "core/libutils/include",
        "core/libsystem/include", "logging/liblog/include",
    )]
    sources = re.findall(r'"([^"\n]+\.cpp)"', (reader / "Android.bp").read_text())
    sources = sorted(set(sources))
    if len(sources) != 22:
        raise ValueError("Unexpected libinputreader source list")
    manifest = {
        "source": source_record(args.source, base / "patches/inputreader-wayland-fifo.patch"),
        "libcxx": source_record(args.libcxx, base / "patches/libcxx-bionic-locale.patch"),
        "dependencies": {name: source_record(args.deps / name) for name in ("core", "libbase", "logging")},
        "fmt": source_record(args.fmt),
        "compiler": output([args.compiler, "--version"]),
        "compiler_sha256": file_digest(args.compiler.resolve()),
        "sysroot": str(sysroot),
        "ndk_revision": (args.ndk / "source.properties").read_text(),
        "build_headers_sha256": {
            str(p.relative_to(base)): file_digest(p)
            for p in sorted((base / "native-headers").rglob("*")) if p.is_file()
        },
        "results": {},
    }
    header_inputs = []
    for inc in includes + [args.libcxx / "include"]:
        header_inputs += [p for p in inc.rglob("*") if p.is_file()]
    common_stamp = hashlib.sha256()
    for p in sorted(set(header_inputs)):
        common_stamp.update(str(p).encode())
        common_stamp.update(bytes.fromhex(file_digest(p)))
    for arch, target, libdir in (
        ("arm64", "aarch64-linux-android33", "lib64"),
        ("arm", "armv7a-linux-androideabi33", "lib"),
    ):
        dest = args.output / arch
        dest.mkdir(exist_ok=True)
        flags = [
            "--target=" + target, "--sysroot=" + str(sysroot),
            "-resource-dir=" + str(toolchain / "lib/clang/18"),
            "-nostdinc++", "-isystem", str(args.libcxx / "include"),
            "-std=c++20", "-march=armv8-a", "-mtune=cortex-a57", "-O2", "-fno-lto",
            "-fPIC", "-fno-exceptions", "-fno-rtti", "-DANDROID", "-DNDEBUG",
        ] + ["-I" + str(p) for p in includes]
        commands = []

        def compile_source(job):
            source, obj, fs = job
            command = [str(args.compiler), *flags]
            if fs:
                # Match external/libcxx's libc++fs Android.bp build settings.
                command += ["-fexceptions", "-frtti", "-D_LIBCPP_BUILDING_LIBRARY", "-DLIBCXX_BUILDING_LIBCXXABI"]
                if arch == "arm":
                    command += ["-D_FILE_OFFSET_BITS=64"]
            command += ["-c", str(source), "-o", str(obj)]
            commands.append(command)
            stamp = hashlib.sha256(common_stamp.digest() + json.dumps(command).encode() + source.read_bytes()).hexdigest()
            stampfile = obj.with_suffix(".sha256")
            if obj.exists() and stampfile.exists() and stampfile.read_text() == stamp:
                print("CACHED", arch, source.name, flush=True)
                return
            with obj.with_suffix(".log").open("w") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
            stampfile.write_text(stamp)
            print("CXX", arch, source.name, flush=True)

        jobs = [(reader / name, dest / (name.replace("/", "_") + ".o"), False) for name in sources]
        fsobjects = []
        for name in ("directory_iterator.cpp", "operations.cpp"):
            obj = dest / ("libcxxfs_" + name + ".o")
            fsobjects.append(obj)
            jobs.append((args.libcxx / "src/filesystem" / name, obj, True))
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(compile_source, jobs))
        fsarchive = dest / "libcxxfs.a"
        fsarchive.unlink(missing_ok=True)
        subprocess.run(["llvm-ar", "rcs", str(fsarchive), *map(str, fsobjects)], check=True)
        libraries = ["base", "cap", "crypto", "cutils", "input", "log", "statslog", "ui", "utils", "PlatformProperties", "inputflinger_base", "c++", "c", "m", "dl"]
        artifact = dest / "libinputreader.so"
        command = [str(args.compiler), *flags[:3], "-resource-dir=" + str(toolchain / "lib/clang/18"),
            "-shared", "-fuse-ld=lld", "-nostdlib++", "-rtlib=compiler-rt", "-unwindlib=libunwind",
            "-Wl,-soname,libinputreader.so", "-Wl,--no-undefined", "-Wl,-z,relro,-z,now",
            "-Wl,--build-id=sha1", "-Wl,--exclude-libs,libcxxfs.a", "-Wl,--pack-dyn-relocs=android+relr",
            "-o", str(artifact), *[str(obj) for _, obj, fs in jobs if not fs], str(fsarchive),
            "-L" + str(args.live_root / "system" / libdir),
            "-L" + str(toolchain / "lib/clang/18/lib/linux" / ("aarch64" if arch == "arm64" else "arm")),
            "-Wl,--no-as-needed", *["-l" + name for name in libraries]]
        commands.append(command)
        with (dest / "link.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        symbols = output(["llvm-nm", "-D", artifact])
        if "__ndk1" in symbols or "ne180000" in symbols:
            raise ValueError("NDK ABI leaked into platform InputReader")
        manifest["results"][arch] = {
            "target": target, "artifact": arch + "/libinputreader.so", "sha256": file_digest(artifact),
            "original_sha256": file_digest(args.live_root / "system" / libdir / "libinputreader.so"),
            "commands": commands,
            "linked_libraries_sha256": {
                "lib" + name + ".so": file_digest(args.live_root / "system" / libdir / ("lib" + name + ".so"))
                for name in libraries if name not in ("c", "m", "dl")
            },
        }
        print("LINK", arch, manifest["results"][arch]["sha256"], flush=True)
    (args.output / "native-input-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
