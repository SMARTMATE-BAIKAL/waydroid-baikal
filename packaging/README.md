# Baikal Waydroid system integration

Maintainer: Timofei Granovskii <t.stifix@gmail.com>

The distribution starts the container manager during system boot. The user
session starts as soon as KWin has imported its actual Wayland environment, in
parallel with Plasma. The notification connection does not block startup.
Android reports readiness through its
Binder user-unlocked event, a successful application-list transaction, and host
service initialization. The Waydroid process then sends `READY=1` to systemd.

The Linux desktop and Crystal Dock never wait for Android readiness. The session
joins the login transaction without implicit target ordering, so it starts in
parallel immediately after KWin. Application desktop entries invoke
`waydroid app launch` directly. No session keeper, socket-name guess,
property polling, or application-launch shell wrapper participates in startup.

The service is wanted, not required, by the Plasma Wayland target. Android startup
has a bounded timeout and restart budget, so an Android failure does not prevent
access to the Linux desktop. An already initialized Android image
is required; installing this package never replaces images or user data.

The matched ART/framework, InputReader for both Android ABIs and Baikal HWC are
included in this package. The host VAAPI decoder and its native systemd service
are included too; the host executable is rebuilt against the installed FFmpeg
and records exact library SONAME dependencies. Android Mesa and the Android-side
EShare codec provider are retained. Input travels only through the
native Wayland HWC FIFOs; physical host input nodes stay outside the container.
Waydroid core publishes the device policy before every LXC start and migrates
existing configuration away from the retired input synchronization mount hook.

## Persistent Android data

An existing device can select its established Android profile in the system
configuration `/var/lib/waydroid/waydroid.cfg`:

```ini
[userdata]
1001 = /var/lib/waydroid/data
```

The key is the host user's Unix UID. The directory must already exist; an invalid
explicit mapping fails instead of silently creating an empty profile. Desktop
entries remain in the user's normal XDG applications directory. No data is copied
or recursively reowned. The container validates the selection for the authenticated
D-Bus caller.

## Build and migration

First build and verify the matched payload using `../android-runtime/README.md`.
The packer publishes the source archive in this directory and updates its SHA256
in the existing PKGBUILD. Run `makepkg --config "$PWD/makepkg.conf"` from this
directory. The source revision is pinned; a local source
URL can be supplied with `WAYDROID_SOURCE_URL=file:///path/to/waydroid-baikal`.
The package replaces both stock `waydroid` and the retired standalone
`waydroid-android-runtime-baikal` package. Conflicts and compatibility provides
allow pacman to remove the old runtime and transfer all runtime file ownership
within one transaction. The menu package uses this native service provider
explicitly. No broad overwrite is required.

The build runs the host integration tests, runtime tooling tests and a complete
payload hash/provenance check. Stripping and debug splitting are disabled so the
verified Android artifacts remain byte-identical. Installed verification metadata
is available in `/usr/share/waydroid-baikal/android-runtime/`.

The host decoder source is maintained in the existing private
`waydroid-eshare-hwdecode-artifacts` repository. Export the pinned revision into
the local source archive before building; proprietary APKs and private source
are not copied into this public Git repository:

```sh
git -C /path/to/waydroid-eshare-hwdecode-private archive --format=tar \
  --prefix=waydroid-hwdec-source/ f29b7b0a3173c0d87b5774e7dadeaeaea5171401 \
  src/host_vaapi_h264d.cpp src/waydroid_hwdec_protocol.h | \
  zstd -T1 -6 > waydroid-hwdec-source-f29b7b0a3173.tar.zst
```

The recipe verifies the source archive hash and exercises the actual host
protocol and graceful shutdown. Pacman owns the decoder, systemd service and
container dependency. Known old `/usr/local` and `/etc/systemd` implementations
are removed on exact hash matches; no compatibility library symlinks are used.

The package install transaction converts old global launcher commands and removes
the known obsolete session keepers, warmup unit and manual cgroup controller writes.
Legacy files are matched by SHA256 before removal; locally modified variants are
reported and preserved for review. The normal runtime has no migration script.

If an application is launched before the session exists, the native CLI activates
`waydroid-session.service` through systemd D-Bus and waits for its readiness job.
It never starts an unmanaged foreground Android desktop.

Known obsolete helper implementations are removed only on exact hash matches.
Package migration is limited to system paths and never edits user home directories.
Their original bytes and metadata are preserved in the private artifacts
repository at `preserve/waydroid-obsolete-runtime-20261028`.
