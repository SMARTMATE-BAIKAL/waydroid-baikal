// Build-time declarations for Android 13 platform libraries.
// Maintainer: Timofei Granovskii <t.stifix@gmail.com>
#pragma once
namespace android::util {
constexpr int INPUTDEVICE_REGISTERED = 351;
int stats_write(int, const char*, int, int, int, int, const char*, int);
}
