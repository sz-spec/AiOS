/**
 * @file keyboard.h
 * @brief VOS3 PS/2 Keyboard Driver
 *
 * @details PS/2 Set 1 scancode translation for US QWERTY layout.
 *          Supports Shift, Ctrl, and CapsLock modifiers.
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 26 - TTY Input Implementation
 */

#ifndef VOS3_KEYBOARD_H
#define VOS3_KEYBOARD_H

#include <stdint.h>

/* ============================================================================
 * PS/2 SCANCODES (Set 1)
 * ============================================================================ */

/** @brief Key press scancodes (make codes) */
#define PS2_KEY_ESC         0x01U
#define PS2_KEY_1           0x02U
#define PS2_KEY_2           0x03U
#define PS2_KEY_3           0x04U
#define PS2_KEY_4           0x05U
#define PS2_KEY_5           0x06U
#define PS2_KEY_6           0x07U
#define PS2_KEY_7           0x08U
#define PS2_KEY_8           0x09U
#define PS2_KEY_9           0x0AU
#define PS2_KEY_0           0x0BU
#define PS2_KEY_MINUS       0x0CU
#define PS2_KEY_EQUALS      0x0DU
#define PS2_KEY_BACKSPACE   0x0EU
#define PS2_KEY_TAB         0x0FU
#define PS2_KEY_Q           0x10U
#define PS2_KEY_W           0x11U
#define PS2_KEY_E           0x12U
#define PS2_KEY_R           0x13U
#define PS2_KEY_T           0x14U
#define PS2_KEY_Y           0x15U
#define PS2_KEY_U           0x16U
#define PS2_KEY_I           0x17U
#define PS2_KEY_O           0x18U
#define PS2_KEY_P           0x19U
#define PS2_KEY_LBRACKET    0x1AU
#define PS2_KEY_RBRACKET    0x1BU
#define PS2_KEY_ENTER       0x1CU
#define PS2_KEY_LCTRL       0x1DU
#define PS2_KEY_A           0x1EU
#define PS2_KEY_S           0x1FU
#define PS2_KEY_D           0x20U
#define PS2_KEY_F           0x21U
#define PS2_KEY_G           0x22U
#define PS2_KEY_H           0x23U
#define PS2_KEY_J           0x24U
#define PS2_KEY_K           0x25U
#define PS2_KEY_L           0x26U
#define PS2_KEY_SEMICOLON   0x27U
#define PS2_KEY_QUOTE       0x28U
#define PS2_KEY_BACKTICK    0x29U
#define PS2_KEY_LSHIFT      0x2AU
#define PS2_KEY_BACKSLASH   0x2BU
#define PS2_KEY_Z           0x2CU
#define PS2_KEY_X           0x2DU
#define PS2_KEY_C           0x2EU
#define PS2_KEY_V           0x2FU
#define PS2_KEY_B           0x30U
#define PS2_KEY_N           0x31U
#define PS2_KEY_M           0x32U
#define PS2_KEY_COMMA       0x33U
#define PS2_KEY_PERIOD      0x34U
#define PS2_KEY_SLASH       0x35U
#define PS2_KEY_RSHIFT      0x36U
#define PS2_KEY_KPSTAR      0x37U
#define PS2_KEY_LALT        0x38U
#define PS2_KEY_SPACE       0x39U
#define PS2_KEY_CAPSLOCK    0x3AU

/** @brief Key release flag (OR'd with make code) */
#define PS2_KEY_RELEASE     0x80U

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Handle a PS/2 scancode from the keyboard interrupt
 * @param[in] scancode Raw PS/2 Set 1 scancode
 *
 * @details Translates the scancode to ASCII (considering modifiers)
 *          and forwards printable characters to the TTY layer via
 *          vos3_tty_input_char().
 */
void vos3_keyboard_handle_scancode(uint8_t scancode);

/**
 * @brief Initialize the keyboard driver
 * @return 0 on success
 */
int vos3_keyboard_init(void);

/**
 * @brief Get current modifier state
 * @return Bitmask of active modifiers
 */
uint8_t vos3_keyboard_get_modifiers(void);

/* Modifier state bits */
#define VOS3_KB_MOD_SHIFT       0x01U
#define VOS3_KB_MOD_CTRL        0x02U
#define VOS3_KB_MOD_ALT         0x04U
#define VOS3_KB_MOD_CAPSLOCK    0x08U

#endif /* VOS3_KEYBOARD_H */
