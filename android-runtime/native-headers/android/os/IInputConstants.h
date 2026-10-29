// Build-time declarations for Android 13 platform libraries.
// Maintainer: Timofei Granovskii <t.stifix@gmail.com>
#pragma once
namespace android::os {
class IInputConstants {
public:
    static constexpr int UNMULTIPLIED_DEFAULT_DISPATCHING_TIMEOUT_MILLIS = 5000;
    static constexpr int INVALID_BATTERY_CAPACITY = -1;
    static constexpr int INVALID_INPUT_EVENT_ID = 0;
    static constexpr int POLICY_FLAG_INJECTED_FROM_ACCESSIBILITY = 0x20000;
    static constexpr int INPUT_EVENT_FLAG_IS_ACCESSIBILITY_EVENT = 0x800;
    static constexpr int DEFAULT_POINTER_ACCELERATION = 3;
};
}
