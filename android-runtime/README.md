# Baikal Android runtime overlay

Maintainer: Timofei Granovskii <t.stifix@gmail.com>

This directory belongs to the `waydroid-baikal` project. It builds the Android
boot image extensions matching the existing custom framework and packages them
together with native InputReader and Wayland HWC in the main `waydroid-baikal`
package. There is no separate runtime package. It is a
build-time operation: no compilation service, launcher wrapper, polling process,
or additional desktop session is installed.

## Why matching artifacts are required

The April 3, 2026 Android 13 image contains framework boot artifacts expecting DEX
checksum `0x7d2645ff`. The installed custom framework has checksum `0x6c5aa6d8`.
ART rejected the packaged extension and compiled temporary images for both zygotes
on every boot. The old boot profile also describes the original DEX layout.

The builder preserves every uncompressed ZIP member byte and aligns stored
entries to four bytes. It uses the image's own ART compiler and original base
boot images to compile all seven dependent framework extensions for both arm and
arm64. `speed` AOT compilation avoids the incompatible original profile. The
compiler remains Cortex-A57 compatible; CRC, LSE, FP16, dot-product and SVE
instructions are explicitly disabled for arm64. Only one compiler runs at a time
with at most eight workers.

All inputs, commands, output checksums and source framework DEX hashes are
recorded in `build-manifest.json`. Android images and application data are not
replaced by the build.

## Build and verify

The build uses the active container's mount namespace and stages output only in
`/data/local/tmp/baikal-art-candidate`. On this machine that directory is backed by
`/var/lib/waydroid/data/local/tmp/baikal-art-candidate`.

```sh
sudo python3 build-boot-extension.py
sudo python3 verify-boot-extension.py
sudo python3 build-system-server.py --all
sudo python3 verify-system-server.py
```

Verification checks all 42 generated files against their hashes and starts each
ART VM with image compilation disabled. It requires all seven staged extensions
to load, rejects fallback/checksum/alignment errors, and intentionally selects
`java.lang.Object` as a main class. The expected missing-main error is reached
only after VM initialization. This is not a graphical Android session and does
not start an Android app.

The system-server builder also preserves the two custom `services.jar` DEX
payloads and their metadata. It precompiles all nine `SYSTEMSERVERCLASSPATH`
components and six standalone service JARs against the same boot image and full
boot classpath. Main-chain components use `PCL[preceding JARs]`; standalone
components use `PCL[];PCL[complete system-server classpath]`. Unchanged, verified
outputs can be reused incrementally.

APEX JARs remain inside their signed, read-only APEX packages. Their preopt
artifacts use ART's existing system-side lookup under
`/system/framework/oat/arm64/apex@...@classes.odex`. Verification binds the staged
outputs only inside a new recursively private mount namespace and runs the
platform `dexoptanalyzer` for every component with its precise loader context.
Every component must report `kOatUpToDate`, `speed`, no missing checksums and no
fallback. The original live `services.jar` hash is checked again afterward.

Both verification reports bind their results to the exact candidate manifests
and artifact SHA256 hashes. Packaging rejects stale reports, changed payloads,
or system-server artifacts recorded against another boot generation. Repeat the
two verification commands after rebuilding any candidate component. An older
system-server build manifest without recorded boot hashes is accepted only after
a new complete ART compatibility check records the current validated generation
and explicitly identifies the legacy provenance; its historical build record is
never rewritten. A failed verification removes its old success report.
The tools reject Python optimization mode because the ART compatibility checks
must remain enabled. Packaging writes a temporary archive and replaces the
published payload only after compression and final generation checks succeed.

Build the HWC from the preserved native incremental toolchain, then create a
payload with its exact reviewed commit and SHA256 and the verified native input
generation described below:

```sh
python3 pack-candidate.py \
  --candidate /var/lib/waydroid/data/local/tmp/baikal-art-candidate \
  --hwc /path/to/hwcomposer.waydroid.so \
  --hwc-sha256 REVIEWED_SHA256 --hwc-commit REVIEWED_COMMIT \
  --native-input "$HOME/.cache/waydroid-native-build/inputreader-build" \
  --input-init ../../android_hardware_waydroid/hwcomposer/waydroid-input.rc
cd ../packaging
makepkg --config "$PWD/makepkg.conf" -f
```

Run `makepkg` as the build user, not root; make the generated archive readable to
that user. The package disables stripping because ART OAT files and the validated
HWC must remain byte-identical. Runtime ownership is consolidated in
`waydroid-baikal`. The packer updates the existing main package recipe and never
generates another package. The generated source archive is a local build artifact,
excluded from Git; source patches, validation tools and the reviewed file manifest
are tracked here. `check-runtime-payload.py` checks every overlay file and the
boot, system-server and native input verification provenance before packaging.

The package declares `provides`, `conflicts` and `replaces` for the former
`waydroid-android-runtime-baikal` package. Pacman removes that package and transfers
ownership of the identical runtime files in the same installation transaction.
No broad file overwrite or post-install copying is used. Runtime build and
verification records are installed under
`/usr/share/waydroid-baikal/android-runtime/`.

## Deployment and verification boundary

Stop the native Waydroid session and container before installing the package.
It owns the existing highest-priority overlay locations:

- `/var/lib/waydroid/overlay_rw/system/system/framework/framework.jar`
- The 42 `arm` and `arm64` framework extension `.art`, `.oat`, and `.vdex` files
- `services.jar` and the 30 system-server `.odex`/`.vdex` files under `oat/arm64`
- `/var/lib/waydroid/overlay_rw/vendor/lib64/hw/hwcomposer.waydroid.so`
- Both `system/system/lib/libinputreader.so` and `lib64/libinputreader.so`
- `vendor/etc/init/waydroid-input.rc` for the unprivileged native input bridge

The original framework, services and HWC were unowned files. On first adoption, allow
overwrite of those exact paths only after verifying their recorded original
hashes. Do not use a broad pacman overwrite. Preserve the normal ART base image,
Android application data, hardware decode provider and Android Mesa.

After startup, verify no framework checksum errors or boot-extension dex2oat
invocations, check both zygotes and Android app launch, and measure readiness.
Confirm that the live system-server process maps the new executable `.odex`
files, including `services.odex`, rather than using only VDEX/JIT fallback.
The unit graph must continue to leave the desktop and dock independent of
Android readiness. A standalone VM load proves image compatibility; it does not
prove the entire Android framework or Binder contracts are correct.

If Android framework/APEX inputs change, regenerate the entire matched set.
Do not reuse this payload with another base image without checking all recorded
input hashes. The source framework's existing Java patches are preserved exactly;
any separate Java ABI correction must happen before rebuilding this set.

## Native Wayland input

The Wayland HWC transports Linux `input_event` records over four private FIFOs.
These endpoints are not evdev devices. EventHub must identify them before
querying capabilities and synthesize the protocol's axes and device classes;
evdev identity, capability, repeat and clock ioctls must stay restricted to real
evdev descriptors. Ordinary hardware input retains its original path. Cursor
absolute coordinates apply only to the Wayland device; relative pointer capture
continues to use the normal relative mapper path.

InputReader is built from LineageOS 20 `android_frameworks_native` commit
`915ad642e331e0f8ea107b5f5519f2c6f1e7e01c` with
`patches/inputreader-wayland-fifo.patch`. The original Waydroid patches are kept
alongside it for provenance. The image supports both arm64 and arm32, so both
libraries are mandatory. The platform libc++ headers and filesystem sources are
from `android_external_libcxx` commit
`0eab991b37dfa2342b7f0a1d2307241954dd226f`, with the small Bionic header compatibility
patch in `patches/libcxx-bionic-locale.patch`. Do not substitute NDK libc++ or
rename its ABI symbols. Generated build headers declare the image's actual
external sysprop and stats functions; they do not replace those functions.

Source trees, dependencies and NDK r27c live under
`~/.cache/waydroid-native-build/`. Check out the recorded commits and apply the
corresponding saved patches there. The build manifest records the exact libbase,
system/core, logging and fmt commits, all build-header hashes, compiler identity,
commands, original libraries and linked Android library hashes. A source diff
must match the saved patch exactly before compilation is allowed.

```sh
python3 build-native-input.py \
  --deps "$HOME/.cache/waydroid-native-build/input-deps" \
  --fmt "$HOME/.cache/waydroid-native-build/fmt-input" \
  --live-root /var/lib/waydroid/rootfs --jobs 4
sudo python3 verify-native-input.py \
  --candidate "$HOME/.cache/waydroid-native-build/inputreader-build" \
  --hwc /path/to/hwcomposer.waydroid.so \
  --input-init ../../android_hardware_waydroid/hwcomposer/waydroid-input.rc \
  --ndk "$HOME/.cache/waydroid-native-build/android-ndk-r27c"
python3 -m unittest
```

The native aarch64 Clang 18 compiler uses the NDK sysroot and compiler runtime;
the NDK's x86_64 executables are not run on this host. All code targets
`-march=armv8-a -mtune=cortex-a57 -O2 -fno-lto`. Object hashes permit incremental
reuse. Platform `libc++fs` is built from source and its symbols stay local.

Verification requires the original live build inputs, identical ELF dependency
contracts, and no NDK C++ ABI symbols. It creates a separate private mount
namespace inside the running Android container, binds each staged InputReader
only there, and runs an ABI-matched `dlopen(RTLD_NOW)` probe against InputReader,
libinputflinger and libandroid_servers. The live system-server is unaffected.
HWC may not introduce new unverified dynamic symbols. Packaging binds this
report to both InputReader hashes, the native manifest and the exact HWC hash,
the Android init action, and rechecks the generation after compression. Android
init prepares `/dev/input` as `system:system` mode `0770`. HWC keeps each FIFO
under its actual host UID and primary group `system`, mode `0660`, so no root
or `CAP_CHOWN` is needed. InputReader opens the streams through group `system`.

After package installation and an Android-only restart, check EventHub and
InputReader for `wayland_pointer`, `wayland_touch`, `wayland_keyboard` and
`wayland_tablet`, with valid display geometry and no evdev ioctl rejections.
Check actual mouse and multitouch dispatch through the HWC path as well as the
native loader result. A successful loader test alone does not prove physical
input or window-coordinate behavior.

The HWC native input changes are preserved as `patches/hwc-native-input.patch`,
against commit `3f6f17b`, through reviewed commit
`758d725012d39b49914f85380b45cc8bb8bd8f8b`. Its incremental build driver is
`build-hwc-incremental.py`; it explicitly uses the preserved HWC object/header
cache and Android platform dependencies, and is not a clean Android build recipe.
Keep that cache when cleaning obsolete package work directories.
