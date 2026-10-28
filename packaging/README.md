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

The existing Baikal HWC, Android Mesa, touch-device allowlist, and hardware video
decoder integration are independent components and must be retained.

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

Run `makepkg` from this directory. The source revision is pinned; a local source
URL can be supplied with `WAYDROID_SOURCE_URL=file:///path/to/waydroid-baikal`.
The package replaces the stock `waydroid` provider. The menu package uses this
native service provider explicitly.

The package install transaction converts old global launcher commands and removes
the known obsolete session keepers, warmup unit and manual cgroup controller writes.
Legacy files are matched by SHA256 before removal; locally modified variants are
reported and preserved for review. The normal runtime has no migration script.
