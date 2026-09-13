# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""Shared constants for the VOS3 backend."""

# EU AI Act audit bundle
EU_ACT_SCHEMA_ID = "vos3.eu_act.v1"
EU_ACT_MAX_BUNDLE_BYTES = 256 * 1024 * 1024  # 256 MiB

# Chat / input validation (mirrors middleware limits)
CHAT_MAX_CONTENT_BYTES = 100 * 1024  # 100 KB per message
CHAT_MAX_MESSAGES = 500  # history window cap

# Rate-limit tiers (requests per second)
RATE_DEFAULT_RPS = 10.0
RATE_AUTH_RPS = 5.0
RATE_BURST = 20
