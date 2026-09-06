// platform/usb_disk_stub.c — the vendored SD/FatFs diskio glue (drivers/sdcard)
// dispatches physical drive 1 to a USB mass-storage backend that lives in the USB
// driver (tinyusb), which this build does not link. We use only drive 0 (SD), so
// drive 1 is never reached at run time; these stubs exist purely to satisfy the
// linker. If USB-MSC is ever wanted, drop these and link the real usb_disk_*.
#include "ff.h"
#include "diskio.h"

DSTATUS usb_disk_status(void)     { return STA_NODISK | STA_NOINIT; }
DSTATUS usb_disk_initialize(void) { return STA_NODISK | STA_NOINIT; }

DRESULT usb_disk_read(BYTE* buff, LBA_t sector, UINT count) {
    (void)buff; (void)sector; (void)count; return RES_NOTRDY;
}
DRESULT usb_disk_write(const BYTE* buff, LBA_t sector, UINT count) {
    (void)buff; (void)sector; (void)count; return RES_NOTRDY;
}
DRESULT usb_disk_ioctl(BYTE cmd, void* buff) {
    (void)cmd; (void)buff; return RES_NOTRDY;
}
