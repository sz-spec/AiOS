/**
 * @file keyboard.c
 * @brief VOS3 PS/2 Keyboard Driver
 *
 * @details PS/2 Set 1 scancode-to-ASCII translation with modifier support.
 *          Forwards translated characters to the TTY layer.
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 26 - TTY Input Implementation
 */

#include "../../include/vos/keyboard.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * EXTERNAL DEPENDENCIES
 * ============================================================================ */

/**
 * @brief Forward character to TTY layer (defined in tty.c)
 */
extern void vos3_tty_input_char(char c);

/* ============================================================================
 * MODIFIER STATE
 * ============================================================================ */

/** @brief Current modifier key state */
static struct {
    uint8_t shift_left  : 1;
    uint8_t shift_right : 1;
    uint8_t ctrl        : 1;
    uint8_t alt         : 1;
    uint8_t caps_lock   : 1;
    uint8_t reserved    : 3;
} g_kbd_modifiers = {0, 0, 0, 0, 0, 0};

/* ============================================================================
 * SCANCODE-TO-ASCII TRANSLATION TABLES
 * ============================================================================ */

/**
 * @brief Normal (unshifted) scancode to ASCII mapping
 *        Index = scancode, Value = ASCII character (0 = no mapping)
 */
static const char g_scancode_to_ascii[128] = {
    /*  0x00 */ 0,    27,   '1',  '2',  '3',  '4',  '5',  '6',
    /*  0x08 */ '7',  '8',  '9',  '0',  '-',  '=',  '\b', '\t',
    /*  0x10 */ 'q',  'w',  'e',  'r',  't',  'y',  'u',  'i',
    /*  0x18 */ 'o',  'p',  '[',  ']',  '\n', 0,    'a',  's',
    /*  0x20 */ 'd',  'f',  'g',  'h',  'j',  'k',  'l',  ';',
    /*  0x28 */ '\'', '`',  0,    '\\', 'z',  'x',  'c',  'v',
    /*  0x30 */ 'b',  'n',  'm',  ',',  '.',  '/',  0,    '*',
    /*  0x38 */ 0,    ' ',  0,    0,    0,    0,    0,    0,
    /*  0x40 */ 0,    0,    0,    0,    0,    0,    0,    '7',
    /*  0x48 */ '8',  '9',  '-',  '4',  '5',  '6',  '+',  '1',
    /*  0x50 */ '2',  '3',  '0',  '.',  0,    0,    0,    0,
    /*  0x58 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x60 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x68 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x70 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x78 */ 0,    0,    0,    0,    0,    0,    0,    0
};

/**
 * @brief Shifted scancode to ASCII mapping
 *        Index = scancode, Value = ASCII character (0 = no mapping)
 */
static const char g_scancode_to_ascii_shift[128] = {
    /*  0x00 */ 0,    27,   '!',  '@',  '#',  '$',  '%',  '^',
    /*  0x08 */ '&',  '*',  '(',  ')',  '_',  '+',  '\b', '\t',
    /*  0x10 */ 'Q',  'W',  'E',  'R',  'T',  'Y',  'U',  'I',
    /*  0x18 */ 'O',  'P',  '{',  '}',  '\n', 0,    'A',  'S',
    /*  0x20 */ 'D',  'F',  'G',  'H',  'J',  'K',  'L',  ':',
    /*  0x28 */ '"',  '~',  0,    '|',  'Z',  'X',  'C',  'V',
    /*  0x30 */ 'B',  'N',  'M',  '<',  '>',  '?',  0,    '*',
    /*  0x38 */ 0,    ' ',  0,    0,    0,    0,    0,    0,
    /*  0x40 */ 0,    0,    0,    0,    0,    0,    0,    '7',
    /*  0x48 */ '8',  '9',  '-',  '4',  '5',  '6',  '+',  '1',
    /*  0x50 */ '2',  '3',  '0',  '.',  0,    0,    0,    0,
    /*  0x58 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x60 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x68 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x70 */ 0,    0,    0,    0,    0,    0,    0,    0,
    /*  0x78 */ 0,    0,    0,    0,    0,    0,    0,    0
};

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize the keyboard driver
 */
int vos3_keyboard_init(void)
{
    /* Clear modifier state */
    g_kbd_modifiers.shift_left  = 0;
    g_kbd_modifiers.shift_right = 0;
    g_kbd_modifiers.ctrl        = 0;
    g_kbd_modifiers.alt         = 0;
    g_kbd_modifiers.caps_lock   = 0;

    VOS3_INFO("Keyboard driver initialized (PS/2 Set 1, US QWERTY)");

    return 0;
}

/**
 * @brief Get current modifier state
 */
uint8_t vos3_keyboard_get_modifiers(void)
{
    uint8_t mods = 0;

    if (g_kbd_modifiers.shift_left || g_kbd_modifiers.shift_right) {
        mods |= VOS3_KB_MOD_SHIFT;
    }
    if (g_kbd_modifiers.ctrl) {
        mods |= VOS3_KB_MOD_CTRL;
    }
    if (g_kbd_modifiers.alt) {
        mods |= VOS3_KB_MOD_ALT;
    }
    if (g_kbd_modifiers.caps_lock) {
        mods |= VOS3_KB_MOD_CAPSLOCK;
    }

    return mods;
}

/**
 * @brief Handle a PS/2 scancode from keyboard interrupt
 */
void vos3_keyboard_handle_scancode(uint8_t scancode)
{
    /* Check if this is a key release (break code) */
    int is_release = (scancode & PS2_KEY_RELEASE) != 0;
    uint8_t key = scancode & 0x7FU;

    /* Handle modifier keys */
    switch (key) {
        case PS2_KEY_LSHIFT:
            g_kbd_modifiers.shift_left = is_release ? 0 : 1;
            return;

        case PS2_KEY_RSHIFT:
            g_kbd_modifiers.shift_right = is_release ? 0 : 1;
            return;

        case PS2_KEY_LCTRL:
            g_kbd_modifiers.ctrl = is_release ? 0 : 1;
            return;

        case PS2_KEY_LALT:
            g_kbd_modifiers.alt = is_release ? 0 : 1;
            return;

        case PS2_KEY_CAPSLOCK:
            /* Toggle CapsLock on key press only */
            if (!is_release) {
                g_kbd_modifiers.caps_lock = !g_kbd_modifiers.caps_lock;
            }
            return;

        default:
            break;
    }

    /* Ignore key releases for non-modifier keys */
    if (is_release) {
        return;
    }

    /* Determine if shift is active */
    int shift_active = g_kbd_modifiers.shift_left || g_kbd_modifiers.shift_right;

    /* Translate scancode to ASCII */
    char c;
    if (shift_active) {
        c = g_scancode_to_ascii_shift[key];
    } else {
        c = g_scancode_to_ascii[key];
    }

    /* Handle CapsLock (only affects letters) */
    if (g_kbd_modifiers.caps_lock && !shift_active) {
        if (c >= 'a' && c <= 'z') {
            c = c - 'a' + 'A';  /* Convert to uppercase */
        }
    } else if (g_kbd_modifiers.caps_lock && shift_active) {
        if (c >= 'A' && c <= 'Z') {
            c = c - 'A' + 'a';  /* Convert to lowercase (Shift + CapsLock = lower) */
        }
    }

    /* No valid ASCII mapping */
    if (c == 0) {
        return;
    }

    /* Handle Ctrl combinations */
    if (g_kbd_modifiers.ctrl) {
        /* Ctrl+letter produces control characters (ASCII 1-26) */
        if (c >= 'a' && c <= 'z') {
            c = c - 'a' + 1;
        } else if (c >= 'A' && c <= 'Z') {
            c = c - 'A' + 1;
        }
        /* Ctrl+C = ASCII 3, Ctrl+Z = ASCII 26, etc. */
    }

    /* Forward character to TTY layer */
    vos3_tty_input_char(c);
}
