#include "../include/vos/ai_oom.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define REQUIRE(condition) do {                                               \
    if (!(condition)) {                                                       \
        fprintf(stderr, "requirement failed at %s:%d: %s\n",               \
                __FILE__, __LINE__, #condition);                              \
        abort();                                                              \
    }                                                                         \
} while (0)

static uint64_t random_state = UINT64_C(0xd1b54a32d192ed03);

static uint64_t next_random(void)
{
    random_state ^= random_state >> 12;
    random_state ^= random_state << 25;
    random_state ^= random_state >> 27;
    return random_state * UINT64_C(2685821657736338717);
}

static void reserve_and_commit(vos3_ai_mem_quota_t *quota, uint64_t bytes,
                               vos3_ai_quota_reservation_t *reservation)
{
    REQUIRE(vos3_ai_quota_try_reserve(
        quota, bytes, 0, reservation, NULL, NULL) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_commit(reservation) == VOS3_AI_QUOTA_OK);
}

static void test_hard_and_soft_limits(void)
{
    vos3_ai_mem_quota_t q = {.limit = 8192U};
    vos3_ai_quota_reservation_t first = {0};
    vos3_ai_quota_reservation_t second = {0};
    vos3_ai_quota_reservation_t denied = {0};
    vos3_ai_quota_reservation_t soft = {0};
    uint64_t after = 0U;
    int warned = -1;

    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 4096U, 1, &first, &warned, &after) == VOS3_AI_QUOTA_OK);
    REQUIRE(warned == 0 && after == 4096U && q.reserved == 4096U);
    REQUIRE(vos3_ai_quota_commit(&first) == VOS3_AI_QUOTA_OK);
    reserve_and_commit(&q, 4096U, &second);
    REQUIRE(q.used == 8192U && q.reserved == 0U);

    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 1U, 1, &denied, NULL, NULL) == VOS3_AI_QUOTA_EXCEEDED);
    REQUIRE(q.limit_events == 1U && q.denials == 1U &&
            denied.owner == NULL);
    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 1U, 0, &soft, &warned, &after) == VOS3_AI_QUOTA_OK);
    REQUIRE(warned == 1 && after == 8193U && q.limit_events == 2U &&
            q.denials == 1U && q.reserved == 1U);
    REQUIRE(vos3_ai_quota_cancel(&soft, 0) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_release(&first) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_release(&second) == VOS3_AI_QUOTA_OK);
    REQUIRE(q.used == 0U && q.reserved == 0U);
}

static void test_handle_copy_and_release_replay(void)
{
    vos3_ai_mem_quota_t q = {.limit = 64U};
    vos3_ai_quota_reservation_t a = {0};
    vos3_ai_quota_reservation_t b = {0};
    vos3_ai_quota_reservation_t a_copy;

    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 8U, 1, &a, NULL, NULL) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 8U, 1, &b, NULL, NULL) == VOS3_AI_QUOTA_OK);
    a_copy = a;
    REQUIRE(vos3_ai_quota_cancel(&a, 0) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_cancel(&a_copy, 0) == VOS3_AI_QUOTA_CORRUPT);
    REQUIRE(q.reserved == 8U);
    vos3_ai_quota_reservation_t reused = {0};
    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 4U, 1, &reused, NULL, NULL) == VOS3_AI_QUOTA_OK);
    REQUIRE(reused.index == a.index && reused.generation != a.generation);
    REQUIRE(vos3_ai_quota_cancel(&a_copy, 0) == VOS3_AI_QUOTA_CORRUPT);
    REQUIRE(q.reserved == 12U);
    REQUIRE(vos3_ai_quota_cancel(&reused, 0) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_commit(&b) == VOS3_AI_QUOTA_OK);
    REQUIRE(q.used == 8U && q.reserved == 0U);
    REQUIRE(vos3_ai_quota_release(&b) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_release(&b) == VOS3_AI_QUOTA_CORRUPT);
    REQUIRE(q.used == 0U);

    vos3_ai_quota_reservation_t c = {0};
    vos3_ai_quota_reservation_t d = {0};
    reserve_and_commit(&q, 8U, &c);
    reserve_and_commit(&q, 8U, &d);
    vos3_ai_quota_reservation_t c_copy = c;
    REQUIRE(vos3_ai_quota_release(&c) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_release(&c_copy) == VOS3_AI_QUOTA_CORRUPT);
    REQUIRE(q.used == 8U);
    REQUIRE(vos3_ai_quota_release(&d) == VOS3_AI_QUOTA_OK);
}

static void test_corrupt_state_is_fail_closed(void)
{
    vos3_ai_mem_quota_t q = {0};
    vos3_ai_quota_reservation_t r = {0};
    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 16U, 1, &r, NULL, NULL) == VOS3_AI_QUOTA_OK);
    q.used = 1U; /* Ledger says no committed entry. */
    REQUIRE(vos3_ai_quota_commit(&r) == VOS3_AI_QUOTA_CORRUPT);
    REQUIRE(q.used == 1U && q.reserved == 16U &&
            q.entries[r.index].state == VOS3_AI_RESERVATION_RESERVED);
    REQUIRE(vos3_ai_quota_cancel(&r, 0) == VOS3_AI_QUOTA_CORRUPT);
    REQUIRE(q.used == 1U && q.reserved == 16U);

    vos3_ai_mem_quota_t zero_generation = {0};
    zero_generation.reserved = 1U;
    zero_generation.entries[0].bytes = 1U;
    zero_generation.entries[0].state = VOS3_AI_RESERVATION_RESERVED;
    uint64_t total = UINT64_C(0xfeedfacefeedface);
    REQUIRE(vos3_ai_quota_total(&zero_generation, &total) ==
            VOS3_AI_QUOTA_CORRUPT);
    REQUIRE(total == UINT64_C(0xfeedfacefeedface));
}

static void test_capacity_and_saturating_counters(void)
{
    vos3_ai_mem_quota_t q = {0};
    vos3_ai_quota_reservation_t reservations[
        VOS3_AI_QUOTA_MAX_RESERVATIONS] = {{0}};
    vos3_ai_quota_reservation_t extra = {0};

    for (uint32_t i = 0U; i < VOS3_AI_QUOTA_MAX_RESERVATIONS; i++) {
        REQUIRE(vos3_ai_quota_try_reserve(
            &q, 1U, 1, &reservations[i], NULL, NULL) == VOS3_AI_QUOTA_OK);
    }
    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 1U, 1, &extra, NULL, NULL) == VOS3_AI_QUOTA_BUSY);
    for (uint32_t i = 0U; i < VOS3_AI_QUOTA_MAX_RESERVATIONS; i++) {
        REQUIRE(vos3_ai_quota_cancel(&reservations[i], 0) ==
                VOS3_AI_QUOTA_OK);
    }

    q.limit = 1U;
    q.limit_events = UINT64_MAX;
    q.denials = UINT64_MAX;
    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 2U, 1, &extra, NULL, NULL) == VOS3_AI_QUOTA_EXCEEDED);
    REQUIRE(q.limit_events == UINT64_MAX && q.denials == UINT64_MAX);

    q.limit = 0U;
    q.failures = UINT64_MAX;
    extra = (vos3_ai_quota_reservation_t){0};
    REQUIRE(vos3_ai_quota_try_reserve(
        &q, 2U, 1, &extra, NULL, NULL) == VOS3_AI_QUOTA_OK);
    REQUIRE(vos3_ai_quota_cancel(&extra, 1) == VOS3_AI_QUOTA_OK);
    REQUIRE(q.failures == UINT64_MAX);
}

static void test_checked_arithmetic(void)
{
    uint64_t out = 0U;
    REQUIRE(vos3_ai_size_align_up(1U, 4096U, &out) ==
            VOS3_AI_QUOTA_OK && out == 4096U);
    REQUIRE(vos3_ai_size_align_up(4096U, 4096U, &out) ==
            VOS3_AI_QUOTA_OK && out == 4096U);
    REQUIRE(vos3_ai_size_align_up(4097U, 4096U, &out) ==
            VOS3_AI_QUOTA_OK && out == 8192U);
    REQUIRE(vos3_ai_size_align_up(SIZE_MAX - 4095U, 4096U, &out) ==
            VOS3_AI_QUOTA_OK && out == (uint64_t)(SIZE_MAX - 4095U));
    REQUIRE(vos3_ai_size_align_up(SIZE_MAX - 4094U, 4096U, &out) ==
            VOS3_AI_QUOTA_OVERFLOW);
    REQUIRE(vos3_ai_size_align_up(1U, 3U, &out) ==
            VOS3_AI_QUOTA_INVALID);
    REQUIRE(vos3_ai_size_add(UINT64_MAX, 1U, &out) ==
            VOS3_AI_QUOTA_OVERFLOW);
    REQUIRE(vos3_ai_size_mul(UINT64_MAX, 2U, &out) ==
            VOS3_AI_QUOTA_OVERFLOW);
    REQUIRE(vos3_ai_size_mul(4U, 2U * 1024U * 1024U, &out) ==
            VOS3_AI_QUOTA_OK && out == 8U * 1024U * 1024U);
}

static void test_randomized_reserve_oracle(void)
{
    for (uint32_t i = 0U; i < 100000U; i++) {
        vos3_ai_mem_quota_t q = {0};
        vos3_ai_quota_reservation_t committed = {0};
        vos3_ai_quota_reservation_t pending = {0};
        vos3_ai_quota_reservation_t candidate = {0};
        uint64_t committed_bytes = (next_random() & UINT64_C(0xffff)) + 1U;
        uint64_t pending_bytes = (next_random() & UINT64_C(0xffff)) + 1U;
        uint64_t bytes = next_random();
        int enforce = (int)(next_random() & 1U);
        int warned = -1;
        uint64_t after = UINT64_C(0x5a5a5a5a5a5a5a5a);

        reserve_and_commit(&q, committed_bytes, &committed);
        REQUIRE(vos3_ai_quota_try_reserve(
            &q, pending_bytes, 0, &pending, NULL, NULL) == VOS3_AI_QUOTA_OK);
        q.limit = next_random();
        __uint128_t charged = (__uint128_t)committed_bytes + pending_bytes;
        __uint128_t requested = charged + bytes;
        vos3_ai_quota_result_t expected;
        if (bytes == 0U) {
            expected = VOS3_AI_QUOTA_INVALID;
        } else if (requested > UINT64_MAX) {
            expected = VOS3_AI_QUOTA_OVERFLOW;
        } else if (enforce != 0 && q.limit != 0U && requested > q.limit) {
            expected = VOS3_AI_QUOTA_EXCEEDED;
        } else {
            expected = VOS3_AI_QUOTA_OK;
        }

        vos3_ai_quota_result_t actual = vos3_ai_quota_try_reserve(
            &q, bytes, enforce, &candidate, &warned, &after);
        REQUIRE(actual == expected);
        if (actual == VOS3_AI_QUOTA_OK) {
            int expected_warning =
                q.limit != 0U && requested > (__uint128_t)q.limit;
            REQUIRE(after == (uint64_t)requested && warned == expected_warning);
            REQUIRE(vos3_ai_quota_cancel(&candidate, 0) == VOS3_AI_QUOTA_OK);
        } else {
            REQUIRE(after == UINT64_C(0x5a5a5a5a5a5a5a5a) && warned == -1);
            REQUIRE(candidate.owner == NULL && candidate.generation == 0U);
        }
        REQUIRE(vos3_ai_quota_cancel(&pending, 0) == VOS3_AI_QUOTA_OK);
        REQUIRE(vos3_ai_quota_release(&committed) == VOS3_AI_QUOTA_OK);
        REQUIRE(q.used == 0U && q.reserved == 0U);
    }
}

int main(void)
{
    test_hard_and_soft_limits();
    test_handle_copy_and_release_replay();
    test_corrupt_state_is_fail_closed();
    test_capacity_and_saturating_counters();
    test_checked_arithmetic();
    test_randomized_reserve_oracle();
    return 0;
}
