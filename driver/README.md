# arctic_fan_controller kernel driver

This directory holds the hwmon driver for the ARCTIC Fan Controller, packaged
for DKMS so it can be built on a kernel that does not ship it yet.

## What is here

- `arctic_fan_controller.c` - the driver source
- `Makefile` - out-of-tree module makefile
- `dkms.conf` - DKMS package definition
- `install-dkms.sh` - copies the source to `/usr/src`, then adds, builds and
  installs the DKMS package

## Provenance

The source is the upstream Linux driver, taken from the mainline kernel tree.
Evidence recorded from the files themselves and from the kernel git history:

- SPDX line in the source: `// SPDX-License-Identifier: GPL-2.0-or-later`
- `MODULE_AUTHOR("Aureo Serrano de Souza <aureo.serrano@arctic.de>")`
- A comment added locally to this copy names the upstream commit:
  "mainline commit e28d0c73d4d7 assumes kernel 6.12+, where asm/unaligned.h was
  renamed to linux/unaligned.h. Everything else is the mainline file byte for
  byte."
- The upstream commit is `e28d0c73d4d7adc9cd3747d81fdc7338217f9a0c`,
  "hwmon: add driver for ARCTIC Fan Controller", authored 2026-05-08 by Aureo
  Serrano de Souza and committed 2026-06-09 by the hwmon maintainer, Guenter
  Roeck. Mailing list posting:
  https://lore.kernel.org/r/20260508064405.38676-1-aureo.serrano@arctic.de

The only difference from the mainline file is a version-conditional include, so
the same source builds on both older and newer kernels:

```c
#include <linux/version.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 12, 0)
#include <linux/unaligned.h>
#else
#include <asm/unaligned.h>
#endif
```

Mainline uses `#include <linux/unaligned.h>` unconditionally, which does not
exist before kernel 6.12.

## Which kernels already ship it

Checked against the mainline git tree: the file
`drivers/hwmon/arctic_fan_controller.c` is present from tag `v7.2-rc1` onward
and absent in `v7.1` and every earlier tag. So Linux 7.2 is the first released
kernel that ships this driver. On anything older, use the DKMS package here.

The in-tree config symbol is `CONFIG_SENSORS_ARCTIC_FAN_CONTROLLER`. A
distribution kernel that is new enough may still have it turned off, so check
with `modinfo arctic_fan_controller` rather than assuming.

## License

GPL-2.0-or-later, as stated in the source file's SPDX header. The header and
the author attribution are kept intact. The MIT license in the repository root
covers the fan control tool only, not this driver.
