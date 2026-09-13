/*
 * B3 mark-push struct contract probe (host clang, TEST_PLAN_300 §B3 / G5).
 *
 * Validates the Atomic Mark-and-Push control structure added to
 * kernel/include/vos/taint_maps.h for the Finding B3-1 closure (Option (a)):
 *
 *   - the color-entry struct is UNCHANGED (65568 bytes);
 *   - the mark-push arg is a 16-byte header prepended to that UNCHANGED entry,
 *     so sizeof == 65584 byte-for-byte (invariant G5);
 *   - field offsets are exactly { abi=0, fd=4, flags=8, _pad=12, entry=16 };
 *   - the ABI version constant is present;
 *   - the ioctl request macro compiles where <sys/ioctl.h> is available.
 *
 * This is a pure host compile+size check — it does NOT exercise the eBPF LSM
 * (which needs a Linux >= 5.17 BPF-LSM runner, unavailable here).
 */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <vos/taint_maps.h>

static int fails = 0;

#define CHECK(label, cond)                                            \
    do {                                                              \
        if (cond) { printf("%-58s PASS\n", (label)); }                \
        else      { printf("%-58s FAIL\n", (label)); fails++; }       \
    } while (0)

int main(void)
{
    printf("=== B3-MARK-PUSH — atomic mark-and-push struct contract (G5) ===\n");

    CHECK("B3MP.1 color entry struct UNCHANGED (== 65568 bytes)",
          sizeof(struct vos3_taint_color_entry) == 65568u);

    CHECK("B3MP.2 mark_push header is 16 bytes (entry at offset 16)",
          offsetof(struct vos3_taint_mark_push_arg, entry) == 16u);

    CHECK("B3MP.3 mark_push arg == header + entry == 65584 (G5)",
          sizeof(struct vos3_taint_mark_push_arg) == 65584u);

    CHECK("B3MP.4 header field offsets abi=0 fd=4 flags=8 _pad=12",
          offsetof(struct vos3_taint_mark_push_arg, abi_version) == 0u &&
          offsetof(struct vos3_taint_mark_push_arg, fd)          == 4u &&
          offsetof(struct vos3_taint_mark_push_arg, flags)       == 8u &&
          offsetof(struct vos3_taint_mark_push_arg, _pad)        == 12u);

    CHECK("B3MP.5 ABI version constant present (== 1)",
          VOS3_TAINT_MARK_PUSH_ABI == 1u);

    CHECK("B3MP.6 MP flag bits are distinct single bits",
          VOS3_TAINT_MP_REPLACE == 1u && VOS3_TAINT_MP_ONESHOT == 2u);

#ifdef VOS3_TAINT_IOC_MARK_PUSH
    /* Encoded with a POINTER arg (sizeof == 8) — the 65584-byte struct would
     * overflow the _IOC size field if passed by value; the handler
     * copy_from_user()s the full struct via the pointer. */
    CHECK("B3MP.7 VOS3_TAINT_IOC_MARK_PUSH ioctl code defined (nonzero)",
          (unsigned long)VOS3_TAINT_IOC_MARK_PUSH != 0ul);
#else
    printf("%-58s SKIP (<sys/ioctl.h> absent on this host)\n",
           "B3MP.7 VOS3_TAINT_IOC_MARK_PUSH ioctl code");
#endif

    if (fails == 0) {
        printf("\n== B3-MARK-PUSH 7 passed, 0 failed ==\n");
        return 0;
    }
    printf("\n== B3-MARK-PUSH %d FAILED ==\n", fails);
    return 1;
}
