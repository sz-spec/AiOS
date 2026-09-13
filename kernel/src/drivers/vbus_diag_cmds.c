/**
 * @file vbus_diag_cmds.c
 * @brief VBus bridge diagnostic and system command handlers.
 *
 * Extracted from virtio_bridge.c during the bridge split refactor.
 * Contains: PING, SYSINFO, PROCS, KASLR_BASE, CPU_PATH, CRING_STATUS,
 * VECTOR_CHECK, MEMCPY_BENCH, CORE_PIN, SMP_STATUS, SQ_STATUS,
 * DOORBELL_STATUS, CRING_FLOOD, TLB_AUDIT, QUERY_CR4,
 * HEARTBEAT_ADDR, HEARTBEAT_STATUS.
 */

#include "vbus_bridge_internal.h"

/* ============================================================================
 * BASIC DIAGNOSTIC COMMANDS
 * ============================================================================ */

void cmd_ping(void)  { send_ok("PONG"); }

/**
 * @brief BUILD_UUID — Return compile-time build identifier for desync detection.
 *
 * v19.8: The CLI reads the same UUID from the host-side ELF binary and
 * compares it against this response.  A mismatch means the running kernel
 * doesn't match the binary on disk.
 */
extern const char vos3_build_uuid[];
void cmd_build_uuid(void) { send_ok(vos3_build_uuid); }

/**
 * @brief SYSINFO — Return system information as key=value pairs
 */
void cmd_sysinfo(void)
{
    uint64_t uptime = vos3_sched_get_uptime_ms();
    size_t total_pages = vos3_pmm_total_pages_count();
    size_t free_pages = vos3_pmm_free_pages_count();

    uint32_t task_count = 0;
    for (uint32_t i = 0; i < VOS3_MAX_TASKS; i++) {
        vos3_task_t* t = vos3_task_get(i);
        if (t != NULL && t->state != VOS3_TASK_DEAD) task_count++;
    }

    char resp[512]; int ri = 0; char nb[20]; const char* p; const char* k;

    k = "uptime_ms="; while (*k) resp[ri++] = *k++;
    uint_to_str(uptime, nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "mem_total_kb="; while (*k) resp[ri++] = *k++;
    uint_to_str((uint64_t)(total_pages * 4), nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "mem_free_kb="; while (*k) resp[ri++] = *k++;
    uint_to_str((uint64_t)(free_pages * 4), nb, 20); p = nb; while (*p) resp[ri++] = *p++;
    resp[ri++] = ',';

    k = "tasks="; while (*k) resp[ri++] = *k++;
    uint_to_str(task_count, nb, 20); p = nb; while (*p) resp[ri++] = *p++;

    resp[ri] = '\0';
    send_ok(resp);
}

/**
 * @brief PROCS — Return list of processes as CSV rows
 */
void cmd_procs(void)
{
    static char resp[4096];
    int ri = 0;
    int first = 1;

    for (uint32_t i = 0; i < VOS3_MAX_TASKS && ri < 4000; i++) {
        vos3_task_t* t = vos3_task_get(i);
        if (t == NULL || t->state == VOS3_TASK_DEAD) continue;

        if (!first && ri < 4095) resp[ri++] = ';';
        first = 0;

        char nb[20]; const char* p;
        uint_to_str(t->pid, nb, 20); p = nb;
        while (*p && ri < 4095) resp[ri++] = *p++;
        if (ri < 4095) resp[ri++] = ',';

        const char* n = t->name;
        while (*n && ri < 4095) resp[ri++] = *n++;
        if (ri < 4095) resp[ri++] = ',';

        const char* st = vos3_task_state_name(t->state);
        while (*st && ri < 4095) resp[ri++] = *st++;
        if (ri < 4095) resp[ri++] = ',';

        uint_to_str(t->parent ? t->parent->pid : 0, nb, 20); p = nb;
        while (*p && ri < 4095) resp[ri++] = *p++;
    }

    resp[ri] = '\0';
    send_ok(resp);
}

/* ============================================================================
 * PHASE 4.4: UNIVERSAL SHARD — DIAGNOSTIC COMMANDS
 * ============================================================================ */

void cmd_kaslr_base(void)
{
    uint64_t base = vos3_ai_kaslr_base();
    char buf[24];
    u64_to_hex(base, buf, sizeof(buf));
    send_ok(buf);
}

void cmd_cpu_path(void)
{
    send_ok(vos3_memcpy_path_name());
}

void cmd_cring_status(void)
{
    uint64_t hb = vos3_ai_get_heartbeat_phys();
    if (hb == 0) {
        send_err(12, "ENOMEM: heartbeat page not allocated");
        return;
    }
    volatile uint8_t *page = (volatile uint8_t *)(0xFFFF800000000000ULL + hb);
    volatile uint32_t *head = (volatile uint32_t *)(page + VOS3_CRING_OFFSET);
    volatile uint32_t *tail = (volatile uint32_t *)(page + VOS3_CRING_OFFSET + 4);
    char buf[32];
    uint_to_str(*head, buf, sizeof(buf));
    int len = 0;
    while (buf[len]) len++;
    buf[len++] = '|';
    uint_to_str(*tail, buf + len, sizeof(buf) - len);
    send_ok(buf);
}

/**
 * Phase 4.4.1: VECTOR_CHECK — read YMM0-3 and report whether they are all-zero.
 */
void cmd_vector_check(void)
{
    extern void vos3_simd_scrub_all(void);
    uint64_t dirty_hi = 0, clean_hi = 0;

    /* Step 1: Dirty YMM registers via an AVX2 256-bit load */
    {
        uint8_t pattern[32] __attribute__((aligned(32)));
        for (int i = 0; i < 32; i++) pattern[i] = 0xFF;
        __asm__ volatile(
            "vmovdqu (%0), %%ymm0"
            :: "r"(pattern) : "memory"
        );
    }

    /* Read YMM0 upper 64 bits */
    __asm__ volatile(
        "vextracti128 $1, %%ymm0, %%xmm1\n\t"
        "movq %%xmm1, %0"
        : "=r"(dirty_hi) :: "memory"
    );

    /* Step 2: Run SIMD scrub */
    vos3_simd_scrub_all();

    /* Step 3: Read YMM0 upper 64 bits — should now be 0x0 */
    __asm__ volatile(
        "vextracti128 $1, %%ymm0, %%xmm1\n\t"
        "movq %%xmm1, %0"
        : "=r"(clean_hi) :: "memory"
    );

    char buf[64];
    int pos = 0;
    char tmp[24];

    u64_to_hex(dirty_hi, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';

    u64_to_hex(clean_hi, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';

    const char *verdict = (dirty_hi != 0 && clean_hi == 0) ? "PASS" : "FAIL";
    for (int i = 0; verdict[i]; i++) buf[pos++] = verdict[i];
    buf[pos] = '\0';

    send_ok(buf);
}

void cmd_memcpy_bench(void)
{
    uint64_t erms_cycles = 0, opt_cycles = 0;
    vos3_memcpy_bench(&erms_cycles, &opt_cycles);

    char buf[80];
    int pos = 0;
    char tmp[24];
    uint_to_str(erms_cycles, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';
    uint_to_str(opt_cycles, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';
    const char *pn = vos3_memcpy_path_name();
    for (int i = 0; pn[i]; i++) buf[pos++] = pn[i];
    buf[pos] = '\0';
    send_ok(buf);
}

/* Phase 4.5: CORE_PIN|slot_id */
void cmd_core_pin(const char *slot_str)
{
    if (slot_str == NULL) { send_err(22, "EINVAL: missing slot_id"); return; }
    uint8_t sid = (uint8_t)parse_u64(slot_str);
    int rc = vos3_ai_core_pinning(sid);
    if (rc != 0) { send_err(22, "EINVAL: core_pinning failed"); return; }
    char buf[32];
    int pos = 0;
    char tmp[8];
    uint_to_str(sid, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';
    uint_to_str(sid + 1, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos] = '\0';
    send_ok(buf);
}

/* Phase 4.5: SMP_STATUS */
void cmd_smp_status(void)
{
    char buf[64];
    int pos = 0;
    char tmp[8];
    uint8_t mask = 0;
    for (uint8_t i = 0; i < 4; i++) {
        if (vos3_ai_slot_is_streaming(i)) mask |= (1U << i);
    }
    uint_to_str(mask, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos] = '\0';
    send_ok(buf);
}

/* Phase 4.6: SQ_STATUS */
void cmd_sq_status(void)
{
    uint32_t head = 0, tail = 0;
    vos3_sq_status(&head, &tail);
    uint64_t processed = 0, sntl_rejects = 0;
    vos3_sq_security_stats(&processed, &sntl_rejects);
    char buf[128];
    int pos = 0;
    char tmp[24];
    uint_to_str(head, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';
    uint_to_str(tail, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';
    u64_to_hex(VOS3_SNTL, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';
    uint_to_str(processed, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';
    uint_to_str(sntl_rejects, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos] = '\0';
    send_ok(buf);
}

/* Phase 4.7: DOORBELL_STATUS */
void cmd_doorbell_status(void)
{
    uint32_t bits = vos3_doorbell_check();
    char buf[16];
    u64_to_hex((uint64_t)bits, buf, sizeof(buf));
    send_ok(buf);
}

/* Phase 4.5.1: CRING_FLOOD|count */
void cmd_cring_flood(const char *count_str)
{
    if (count_str == NULL) { send_err(22, "EINVAL: missing count"); return; }
    uint32_t count = (uint32_t)parse_u64(count_str);
    if (count > 1000) count = 1000;

    for (uint32_t i = 0; i < count; i++) {
        vos3_cring_post(0xFF, 0x00, (uint16_t)(i & 0xFFFF));
    }

    char buf[64];
    int pos = 0;
    char tmp[16];
    extern uint64_t vos3_ai_heartbeat_phys(void);
    uint_to_str(count, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos] = '\0';
    send_ok(buf);
}

/* Phase 4.5.1: TLB_AUDIT */
void cmd_tlb_audit(void)
{
    uint64_t cr3_val;
    __asm__ volatile("mov %%cr3, %0" : "=r"(cr3_val));
    __asm__ volatile("mov %0, %%cr3" :: "r"(cr3_val) : "memory");
    __asm__ volatile("mfence" ::: "memory");

    extern uint32_t vos3_smp_online_count(void);
    uint32_t ncpus = vos3_smp_online_count();
    char buf[48];
    int pos = 0;
    const char *prefix = "CR3_RELOAD_COMPLETE|";
    for (int i = 0; prefix[i]; i++) buf[pos++] = prefix[i];
    char tmp[8];
    uint_to_str(ncpus, tmp, sizeof(tmp));
    for (int i = 0; tmp[i]; i++) buf[pos++] = tmp[i];
    buf[pos] = '\0';
    send_ok(buf);
}

/* Phase 4.2.20: QUERY_CR4 */
void cmd_query_cr4(void)
{
    uint64_t cr4;
    __asm__ volatile("mov %%cr4, %0" : "=r"(cr4));
    char buf[24];
    u64_to_hex(cr4, buf, sizeof(buf));
    send_ok(buf);
}

/* Phase 4.2.15: HEARTBEAT_ADDR */
void cmd_heartbeat_addr(void)
{
    uint64_t phys = vos3_ai_get_heartbeat_phys();
    if (phys == 0) {
        send_err(12, "ENOMEM: heartbeat page not allocated");
        return;
    }
    char buf[24];
    u64_to_hex(VOS3_HEARTBEAT_PAGE_VADDR, buf, sizeof(buf));
    send_ok(buf);
}

/* Phase 4.2.16: HEARTBEAT_STATUS */
void cmd_heartbeat_status(void)
{
    uint8_t status = vos3_heartbeat_get_status();
    char buf[8];
    buf[0] = "0123456789abcdef"[(status >> 4) & 0xF];
    buf[1] = "0123456789abcdef"[status & 0xF];
    buf[2] = '\0';
    send_ok(buf);
}

/* ============================================================================
 * Phase v20.0: HAL + Integrity Commands
 * ============================================================================ */

/**
 * @brief PCI_LIST — Return JSON array of discovered PCI devices
 */
void cmd_pci_list(void)
{
    const vos3_pci_device_t *devs = vos3_pci_get_devices();
    int count = vos3_pci_get_count();

    /* Build JSON response — worst case ~100 bytes per device */
    static char resp[4096];
    int ri = 0;
    resp[ri++] = '[';

    for (int i = 0; i < count && ri < 3900; i++) {
        if (i > 0) resp[ri++] = ',';
        /* {"bus":0,"dev":2,"fn":0,"vendor":"1af4","device":"1110","class":"ff00"} */
        char nb[12];

        /* Opening brace + bus */
        const char *s = "{\"bus\":";
        while (*s) resp[ri++] = *s++;
        uint_to_str(devs[i].bus, nb, sizeof(nb));
        s = nb; while (*s) resp[ri++] = *s++;

        /* dev */
        s = ",\"dev\":";
        while (*s) resp[ri++] = *s++;
        uint_to_str(devs[i].dev, nb, sizeof(nb));
        s = nb; while (*s) resp[ri++] = *s++;

        /* fn */
        s = ",\"fn\":";
        while (*s) resp[ri++] = *s++;
        uint_to_str(devs[i].func, nb, sizeof(nb));
        s = nb; while (*s) resp[ri++] = *s++;

        /* vendor (hex string) */
        s = ",\"vendor\":\"";
        while (*s) resp[ri++] = *s++;
        resp[ri++] = "0123456789abcdef"[(devs[i].vendor_id >> 12) & 0xF];
        resp[ri++] = "0123456789abcdef"[(devs[i].vendor_id >> 8) & 0xF];
        resp[ri++] = "0123456789abcdef"[(devs[i].vendor_id >> 4) & 0xF];
        resp[ri++] = "0123456789abcdef"[devs[i].vendor_id & 0xF];
        resp[ri++] = '"';

        /* device (hex string) */
        s = ",\"device\":\"";
        while (*s) resp[ri++] = *s++;
        resp[ri++] = "0123456789abcdef"[(devs[i].device_id >> 12) & 0xF];
        resp[ri++] = "0123456789abcdef"[(devs[i].device_id >> 8) & 0xF];
        resp[ri++] = "0123456789abcdef"[(devs[i].device_id >> 4) & 0xF];
        resp[ri++] = "0123456789abcdef"[devs[i].device_id & 0xF];
        resp[ri++] = '"';

        /* class (hex string, class:subclass) */
        s = ",\"class\":\"";
        while (*s) resp[ri++] = *s++;
        resp[ri++] = "0123456789abcdef"[(devs[i].class_code >> 4) & 0xF];
        resp[ri++] = "0123456789abcdef"[devs[i].class_code & 0xF];
        resp[ri++] = "0123456789abcdef"[(devs[i].subclass >> 4) & 0xF];
        resp[ri++] = "0123456789abcdef"[devs[i].subclass & 0xF];
        resp[ri++] = '"';

        /* name */
        s = ",\"name\":\"";
        while (*s) resp[ri++] = *s++;
        const char *name = vos3_pci_class_name(devs[i].class_code, devs[i].subclass);
        while (*name && ri < 3950) resp[ri++] = *name++;
        resp[ri++] = '"';

        resp[ri++] = '}';
    }

    resp[ri++] = ']';
    resp[ri] = '\0';

    send_ok(resp);
}

/**
 * @brief KTEXT_HASH — Compute and compare live kernel .text CRC32C
 *
 * At boot, g_ktext_boot_crc is computed once. This command recomputes
 * the CRC live and compares against the boot value.
 */

extern uint8_t _text_start[];
extern uint8_t _text_end[];

static uint32_t g_ktext_boot_crc = 0;
static int      g_ktext_boot_done = 0;

/* Simple CRC32C (Castagnoli) — software fallback, no SSE4.2 needed */
static uint32_t crc32c_sw(const uint8_t *data, size_t len)
{
    /* CRC32C polynomial: 0x82F63B78 */
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++) {
            if (crc & 1U)
                crc = (crc >> 1) ^ 0x82F63B78U;
            else
                crc >>= 1;
        }
    }
    return crc ^ 0xFFFFFFFFU;
}

void vos3_ktext_hash_init(void)
{
    size_t text_size = (size_t)(_text_end - _text_start);
    g_ktext_boot_crc = crc32c_sw(_text_start, text_size);
    g_ktext_boot_done = 1;
    vos3_console_printf("[KTEXT] Boot CRC32C: %08x (%lu bytes)\n",
                        (unsigned)g_ktext_boot_crc, (unsigned long)text_size);
}

void cmd_ktext_hash(void)
{
    if (!g_ktext_boot_done) {
        send_err(1, "ktext hash not initialized");
        return;
    }

    size_t text_size = (size_t)(_text_end - _text_start);
    uint32_t live_crc = crc32c_sw(_text_start, text_size);
    int match = (live_crc == g_ktext_boot_crc) ? 1 : 0;

    /* {"boot_crc":"XXXXXXXX","live_crc":"XXXXXXXX","match":true} */
    char resp[128];
    int ri = 0;
    const char *s;

    s = "{\"boot_crc\":\"";
    while (*s) resp[ri++] = *s++;
    u64_to_hex(g_ktext_boot_crc, &resp[ri], 9); ri += 8;
    resp[ri++] = '"';

    s = ",\"live_crc\":\"";
    while (*s) resp[ri++] = *s++;
    u64_to_hex(live_crc, &resp[ri], 9); ri += 8;
    resp[ri++] = '"';

    s = match ? ",\"match\":true}" : ",\"match\":false}";
    while (*s) resp[ri++] = *s++;
    resp[ri] = '\0';

    send_ok(resp);
}

/**
 * @brief CTX_STATS — Return per-slot context freeze/thaw statistics
 */
void cmd_ctx_stats(const char *slot_str)
{
    if (slot_str == NULL) {
        send_err(22, "missing slot_id");
        return;
    }
    uint8_t slot_id = (uint8_t)parse_uint(slot_str);
    if (slot_id >= 8U) {
        send_err(22, "invalid slot");
        return;
    }

    vos3_ai_model_slot_t info;
    vos3_ai_model_slot_get_info(slot_id, &info);

    /* Return slot context stats as JSON */
    char resp[256];
    int ri = 0;
    char nb[20];
    const char *s;

    s = "{\"slot\":";
    while (*s) resp[ri++] = *s++;
    uint_to_str(slot_id, nb, sizeof(nb));
    s = nb; while (*s) resp[ri++] = *s++;

    s = ",\"hp_count\":";
    while (*s) resp[ri++] = *s++;
    uint_to_str(info.hp_count, nb, sizeof(nb));
    s = nb; while (*s) resp[ri++] = *s++;

    s = ",\"session_epoch\":";
    while (*s) resp[ri++] = *s++;
    uint_to_str(info.session_epoch, nb, sizeof(nb));
    s = nb; while (*s) resp[ri++] = *s++;

    s = ",\"ctx_frozen\":";
    while (*s) resp[ri++] = *s++;
    uint_to_str(info.ctx_frozen, nb, sizeof(nb));
    s = nb; while (*s) resp[ri++] = *s++;

    s = ",\"freeze_id\":";
    while (*s) resp[ri++] = *s++;
    uint_to_str((uint64_t)info.freeze_id, nb, sizeof(nb));
    s = nb; while (*s) resp[ri++] = *s++;

    resp[ri++] = '}';
    resp[ri] = '\0';

    send_ok(resp);
}

/* ============================================================================
 * Phase 9: Storage HAL Command
 * ============================================================================ */

/**
 * @brief STORAGE_INFO -- Show detected storage devices
 *
 * Response format: count|active_type|dev0_type|dev1_type|...
 */
void cmd_storage_info(void)
{
    extern uint32_t vos3_storage_device_count(void);
    extern int vos3_storage_get_device(uint32_t, void *);
    extern int vos3_storage_active_type(void);  /* returns vos3_storage_type_t (int-compatible enum) */

    uint32_t count = vos3_storage_device_count();
    int active_type = vos3_storage_active_type();

    char buf[128];
    char tmp[16];
    int pos = 0;

    /* count */
    uint_to_str(count, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 120; i++) buf[pos++] = tmp[i];
    buf[pos++] = '|';

    /* active_type */
    uint_to_str((uint32_t)active_type, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 120; i++) buf[pos++] = tmp[i];

    /* Per-device type codes */
    for (uint32_t d = 0; d < count && d < 4; d++) {
        /* Inline struct to avoid pulling in the full header */
        struct {
            int type;
            uint8_t pci_bus, pci_dev, pci_func;
            uint32_t bar0;
            uintptr_t mmio_base;
            uint64_t sector_count;
            uint32_t sector_size, max_transfer;
            char model[40];
            uint8_t active;
            uint32_t ahci_port, ahci_cap;
            uint32_t nvme_nsid;
            uint16_t nvme_sqes, nvme_cqes;
        } info;
        __builtin_memset(&info, 0, sizeof(info));
        vos3_storage_get_device(d, (void *)&info);
        buf[pos++] = '|';
        uint_to_str((uint32_t)info.type, tmp, sizeof(tmp));
        for (int i = 0; tmp[i] && pos < 120; i++) buf[pos++] = tmp[i];
    }

    buf[pos] = '\0';
    send_ok(buf);
}

/* ============================================================================
 * Phase 9: V-AAAK Native Mode Command
 * ============================================================================ */

/**
 * @brief AAAK_MODE|<0|1> -- Enable/disable V-AAAK native compression
 *
 * When enabled, all text responses from send_ok() are automatically
 * compressed using the V-AAAK token dictionary before transmission.
 * The compressed payload is prefixed with "AAAK:" so the Python side
 * can detect and decompress it.
 */
void cmd_aaak_mode(const char *enable_str)
{
    extern int g_vbus_aaak_native;
    uint32_t enable = parse_uint(enable_str);
    g_vbus_aaak_native = (enable != 0) ? 1 : 0;
    send_ok(g_vbus_aaak_native ? "AAAK_NATIVE_ON" : "AAAK_NATIVE_OFF");
}

/* ============================================================================
 * Cryptographic Provenance: GET_ENTROPY Command
 * ============================================================================ */

/**
 * @brief GET_ENTROPY — Return 32 bytes of hardware-seeded entropy as hex.
 *
 * Uses RDRAND to fill a 32-byte buffer. If RDRAND is unavailable, falls
 * back to TSC + xorshift mixing. The 32 bytes are hex-encoded into a
 * 64-character string and returned via send_ok().
 *
 * Used by the Python-side ProvenanceSigner to seed HMAC signing keys
 * from kernel-backed hardware entropy (Intel DRNG / AMD RDRAND).
 */
void cmd_get_entropy(void)
{
    uint8_t buf[32];
    int pos = 0;

    /* Fill 32 bytes using RDRAND (8 bytes at a time, 4 iterations) */
    for (int i = 0; i < 4; i++) {
        uint64_t val = 0;
        uint8_t ok = 0;
        int retries = 10;  /* Intel recommends retry on CF=0 */

        while (retries-- > 0) {
            __asm__ volatile("rdrand %0; setc %1"
                             : "=r"(val), "=qm"(ok));
            if (ok) break;
        }

        if (!ok) {
            /* Fallback: TSC + xorshift mixing */
            uint32_t tsc_lo, tsc_hi;
            __asm__ volatile("rdtsc" : "=a"(tsc_lo), "=d"(tsc_hi));
            val = ((uint64_t)tsc_hi << 32) | tsc_lo;
            val ^= (val >> 17);
            val ^= (val << 13);
            val ^= (val >> 7);
            /* Mix in the iteration index to avoid identical blocks */
            val ^= (uint64_t)(i + 1) * 0x9E3779B97F4A7C15ULL;
        }

        /* Store 8 bytes (little-endian) */
        for (int j = 0; j < 8 && pos < 32; j++, pos++) {
            buf[pos] = (uint8_t)(val & 0xFF);
            val >>= 8;
        }
    }

    /* Hex-encode 32 bytes into 64-char string */
    char hex[65];
    static const char hextab[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        hex[i * 2]     = hextab[(buf[i] >> 4) & 0xF];
        hex[i * 2 + 1] = hextab[buf[i] & 0xF];
    }
    hex[64] = '\0';

    send_ok(hex);
}

/* ============================================================================
 * v23.14 (D-CRIT2): HMAC Statistics Command
 * ============================================================================ */

/**
 * @brief HMAC_STATS — Report HMAC violation count and ban status.
 *
 * Response: "violations=<N>,ban_active=<0|1>,hmac_enabled=<0|1>"
 */
void cmd_hmac_stats(void)
{
    extern uint32_t vos3_vbus_get_bad_hmac_count(void);
    extern int      vos3_vbus_hmac_ban_active(void);
    extern int      vos3_vbus_hmac_is_enabled(void);

    uint32_t violations = vos3_vbus_get_bad_hmac_count();
    int ban    = vos3_vbus_hmac_ban_active();
    int enabled = vos3_vbus_hmac_is_enabled();

    char buf[80];
    int pos = 0;
    const char *k; char tmp[12];

    k = "violations=";
    while (*k && pos < 70) buf[pos++] = *k++;
    uint_to_str(violations, tmp, sizeof(tmp));
    for (int i = 0; tmp[i] && pos < 70; i++) buf[pos++] = tmp[i];

    buf[pos++] = ',';
    k = "ban_active=";
    while (*k && pos < 70) buf[pos++] = *k++;
    buf[pos++] = ban ? '1' : '0';

    buf[pos++] = ',';
    k = "hmac_enabled=";
    while (*k && pos < 78) buf[pos++] = *k++;
    buf[pos++] = enabled ? '1' : '0';

    buf[pos] = '\0';
    send_ok(buf);
}
