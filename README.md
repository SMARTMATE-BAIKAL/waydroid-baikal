<img align="left" src="data/AppIcon.png" width="64">

# Waydroid

Waydroid uses a container-based approach to boot a full Android system on a
regular GNU/Linux system.

## Baikal distribution

The Baikal build is maintained by Timofei Granovskii <t.stifix@gmail.com>.
`packaging/PKGBUILD` produces a single aarch64 `waydroid-baikal` package containing
the host integration and its matched Android framework, ART preopt, native
InputReader and Wayland HWC overlay. It replaces the retired standalone Android
runtime package in the same pacman transaction. See `packaging/README.md` for
build and upgrade instructions and `android-runtime/README.md` for the preserved
source patches, build provenance and native input verification.

## Overview

Waydroid uses Linux namespaces (user, pid, uts, net, mount, ipc) to run a
full Android system in a container and provide Android applications on
any GNU/Linux-based platform.

The Android system inside the container has direct access to any needed hardware.

The Android runtime environment ships with a minimal customized Android system
image based on [LineageOS](https://lineageos.org/). The image is currently based
on Android 13.

## Install

See install instructions [here](https://docs.waydro.id/usage/install-on-desktops)

## Documentation

Our documentation can be found at [docs.waydro.id](https://docs.waydro.id)

## Reporting bugs

If you have found an issue with Waydroid, please [file a bug](https://github.com/Waydroid/waydroid/issues/new/choose).

## Get in Touch

If you want to get in contact with the developers please feel free to join the
*Waydroid* groups in [Matrix](https://matrix.to/#/#waydroid:matrix.org) or [Telegram](https://t.me/WayDroid).

Our website can be found at [waydro.id](https://waydro.id/)
