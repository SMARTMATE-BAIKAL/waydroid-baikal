#!/usr/bin/env python3
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""Rebuild the two changed HWC objects without touching the installed module."""

import hashlib
import json
from pathlib import Path
import subprocess

workspace = Path(__file__).resolve().parents[3]
source = workspace / 'system/android_hardware_waydroid'
headers = workspace / 'system/waydroid-eshare-hwdecode-private/third_party/android-platform'
build = Path.home() / '.cache/waydroid-native-build/build'
ndk = build.parent / 'android-ndk-r27c'
sysroot = ndk / 'toolchains/llvm/prebuilt/linux-x86_64/sysroot'
toolchain = Path('/var/tmp/baikal-kernel-toolchain-22-20261028/bin')
output = build / 'hwc-configure-only'
system_lib = Path('/var/lib/waydroid/rootfs/system/lib64')
vendor_lib = Path('/var/lib/waydroid/rootfs/vendor/lib64')

includes = [
    build / 'compat-cxx', build / 'compat-include', output / 'headers', build / 'gen',
    build / 'hidl-gen', build / 'host-include', build / 'host-include/drm',
    build / 'host-include/libdrm', source / 'hwcomposer', source / 'hwcomposer/modes',
    source / 'gralloc',
]
includes += [headers / name for name in [
    'android_external_minigbm', 'android_hardware_libhardware/include',
    'android_system_core/libcutils/include', 'android_system_core/libsystem/include',
    'android_system_core/libsync/include', 'android_system_core/libsync',
    'android_system_core/libutils/include', 'android_system_logging/liblog/include_vndk',
    'android_system_logging/liblog/include', 'android_frameworks_native/libs/ui/include',
    'android_frameworks_native/libs/math/include', 'android_frameworks_native/libs/nativebase/include',
    'android_frameworks_native/libs/nativewindow/include', 'android_system_libhidl/base/include',
    'android_system_libhidl/transport/include', 'android_system_libhwbinder/include',
    'android_system_libbase/include', 'android_system_libfmq/include', 'android_system_libfmq/base',
]]
flags = [
    '--target=aarch64-linux-android33', '--sysroot=' + str(sysroot),
    '-march=armv8-a', '-mtune=cortex-a57', '-O2', '-fno-lto', '-fPIC', '-DANDROID',
    '-DLOG_TAG="hwcomposer"', '-fvisibility=default', '-std=gnu++17', '-fno-rtti',
    '-Wall', '-Wno-unused-parameter', '-Wno-unused-variable', '-Wno-unused-function',
    '-Wno-missing-field-initializers', '-Wno-c99-designator', '-Wno-gnu-designator',
    '-Wno-nullability-completeness', '-Wno-inconsistent-missing-override',
] + ['-I' + str(p) for p in includes]
commands = []

def run(command):
    commands.append([str(value) for value in command])
    (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    result = subprocess.run(commands[-1])
    if result.returncode:
        raise SystemExit(result.returncode)

cpp_sources = [
    'extension.cpp', 'gralloc_handler.cpp', 'hwcomposer.cpp', 'wayland-hwc.cpp',
    'WaydroidClipboard.cpp', 'WaydroidWindow.cpp', 'egl-tools.cpp',
    'modes/waydroid_mode.cpp', 'modes/closed.cpp', 'modes/full-ui.cpp',
    'modes/multi-window.cpp', 'modes/single-window.cpp',
]
objects = []
changed = {'hwcomposer.cpp', 'wayland-hwc.cpp'}
for name in cpp_sources:
    object_name = 'hwcomposer_' + name.replace('/', '_') + '.o'
    if name in changed:
        obj = output / object_name
        inputs = [source / 'hwcomposer' / name, source / 'hwcomposer/wayland-hwc.h', source / 'hwcomposer/waydroid-input.rc', Path(__file__)]
        if not obj.exists() or obj.stat().st_mtime < max(p.stat().st_mtime for p in inputs):
            print('CXX', name, flush=True)
            run([toolchain / 'clang++', *flags, '-c', source / 'hwcomposer' / name, '-o', obj])
    else:
        obj = build / 'obj-current' / object_name
    objects.append(obj)

objects.append(build / 'obj-current/_home_baikal_baikal-workspace_system_android_hardware_waydroid_hwcomposer_cursor-shape-v1-client-protocol.c.o')
objects += [build / 'obj-current' / ('gen_' + name + '-client-protocol.c.o') for name in [
    'wayland-client-protocol-core',
]]
# The core protocol object has a historical filename without a second suffix.
objects[-1] = build / 'obj-current/gen_wayland-client-protocol-core.c.o'
objects += [build / 'obj-current' / ('gen_' + name + '-client-protocol.c.o') for name in [
    'wayland-android', 'xdg-shell', 'viewporter', 'presentation-time',
    'linux-dmabuf-unstable-v1', 'tablet-unstable-v2', 'pointer-constraints-unstable-v1',
    'relative-pointer-unstable-v1', 'idle-inhibit-unstable-v1', 'fractional-scale-v1',
]]
for path in objects:
    if not path.is_file():
        raise FileNotFoundError(path)

print('LINK hwcomposer.waydroid.so', flush=True)
run([
    toolchain / 'clang++', '--target=aarch64-linux-android33', '--sysroot=' + str(sysroot),
    '-resource-dir', ndk / 'toolchains/llvm/prebuilt/linux-x86_64/lib/clang/18',
    '-fuse-ld=' + str(toolchain / 'ld.lld'), '-shared',
    '-Wl,-soname,hwcomposer.waydroid.so', '-Wl,--hash-style=gnu', '-Wl,--build-id=sha1',
    '-o', output / 'hwcomposer.waydroid.so', *objects,
    '-L' + str(build / 'out'), '-L' + str(system_lib), '-L' + str(vendor_lib),
    '-l:libwaydroid-hwc-abi-provider.so', '-llog', '-lutils', '-lcutils', '-lhardware',
    '-lhidlbase', '-lsync', '-lui', '-ldrm', '-lEGL', '-lGLESv2',
    '-l:vendor.waydroid.clipboard@1.0.so', '-l:vendor.waydroid.display@1.0.so',
    '-l:vendor.waydroid.display@1.1.so', '-l:vendor.waydroid.display@1.2.so',
    '-l:vendor.waydroid.task@1.0.so', '-l:vendor.waydroid.window@1.0.so',
    '-l:vendor.waydroid.window@1.1.so', '-l:vendor.waydroid.window@1.2.so', '-lc++',
])
artifact = output / 'hwcomposer.waydroid.so'
with artifact.open('rb') as handle:
    checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
print('SHA256', checksum, artifact, flush=True)
