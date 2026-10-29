/* SPDX-License-Identifier: Apache-2.0 */
/* Maintainer: Timofei Granovskii <t.stifix@gmail.com> */
#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    if (argc < 2)
        return 2;
    for (int i = 1; i < argc; ++i) {
        /* Keep dependencies loaded together, as in the Android service. */
        if (!dlopen(argv[i], RTLD_NOW | RTLD_GLOBAL)) {
            fprintf(stderr, "%s: %s\n", argv[i], dlerror());
            return 1;
        }
        printf("LOADED %s\n", argv[i]);
    }
    return 0;
}
