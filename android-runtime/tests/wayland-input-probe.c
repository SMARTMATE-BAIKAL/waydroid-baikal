/* SPDX-License-Identifier: MIT */
/* Maintainer: Timofei Granovskii <t.stifix@gmail.com> */
/* Send a test gesture through KWin and the normal Wayland HWC input path. */
#include <linux/input-event-codes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include "fake-input-client-protocol.h"

static struct org_kde_kwin_fake_input *input;

static void registry_global(void *data, struct wl_registry *registry,
                            uint32_t name, const char *interface, uint32_t version)
{
    (void)data;
    if (strcmp(interface, "org_kde_kwin_fake_input") == 0 && version >= 3)
        input = wl_registry_bind(registry, name, &org_kde_kwin_fake_input_interface,
                                 version < 5 ? version : 5);
}

static void registry_remove(void *data, struct wl_registry *registry, uint32_t name)
{
    (void)data;
    (void)registry;
    (void)name;
}

int main(int argc, char **argv)
{
    if (argc != 4 || (strcmp(argv[1], "mouse") && strcmp(argv[1], "touch"))) {
        fprintf(stderr, "Usage: %s mouse|touch X Y (KWin logical coordinates)\n", argv[0]);
        return 2;
    }
    struct wl_display *display = wl_display_connect(NULL);
    if (!display)
        return 1;
    struct wl_registry *registry = wl_display_get_registry(display);
    static const struct wl_registry_listener listener = {registry_global, registry_remove};
    wl_registry_add_listener(registry, &listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !input)
        return 1;
    org_kde_kwin_fake_input_authenticate(input, "Baikal input verification",
                                       "Verify native Android mouse and touch dispatch");
    const wl_fixed_t x = wl_fixed_from_double(strtod(argv[2], NULL));
    const wl_fixed_t y = wl_fixed_from_double(strtod(argv[3], NULL));
    if (strcmp(argv[1], "mouse") == 0) {
        org_kde_kwin_fake_input_pointer_motion_absolute(input, x, y);
        wl_display_roundtrip(display);
        org_kde_kwin_fake_input_button(input, BTN_LEFT, WL_POINTER_BUTTON_STATE_PRESSED);
        wl_display_roundtrip(display);
        org_kde_kwin_fake_input_button(input, BTN_LEFT, WL_POINTER_BUTTON_STATE_RELEASED);
    } else {
        org_kde_kwin_fake_input_touch_down(input, 0, x, y);
        org_kde_kwin_fake_input_touch_frame(input);
        wl_display_roundtrip(display);
        org_kde_kwin_fake_input_touch_up(input, 0);
        org_kde_kwin_fake_input_touch_frame(input);
    }
    const int result = wl_display_roundtrip(display) < 0;
    if (org_kde_kwin_fake_input_get_version(input) >= 5)
        org_kde_kwin_fake_input_destroy(input);
    wl_registry_destroy(registry);
    wl_display_disconnect(display);
    return result;
}
