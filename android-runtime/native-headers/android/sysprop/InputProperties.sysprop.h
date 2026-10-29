// Build-time declarations for Android 13 platform libraries.
// Maintainer: Timofei Granovskii <t.stifix@gmail.com>
#pragma once
#include <optional>
namespace android::sysprop::InputProperties {
std::optional<bool> simulate_stylus_with_touch();
}
