"""
Tests for VOS3 Kernel Bridge Protocol
======================================

Unit tests for the protocol layer: command formatting, response parsing,
hex encoding, and process/kv response parsing.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel_bridge.protocol import (
    BridgeResponse,
    format_command,
    parse_response,
    parse_kv_response,
    parse_procs_response,
    hex_encode,
    hex_decode,
    CMD_PING,
    CMD_STAT,
    CMD_WRITE,
    CMD_READ,
    CMD_LS,
    CMD_EXEC,
    CMD_MKDIR,
    CMD_UNLINK,
    CMD_RENAME,
    CMD_SYSINFO,
    CMD_PROCS,
    CMD_HTTPGET,
)

# ---------------------------------------------------------------------------
# format_command
# ---------------------------------------------------------------------------


class TestFormatCommand:
    def test_single_part(self):
        assert format_command("PING") == b"PING\n"

    def test_two_parts(self):
        assert format_command("STAT", "/disk") == b"STAT|/disk\n"

    def test_three_parts(self):
        assert (
            format_command("WRITE", "/disk/f.txt", "68656c6c6f")
            == b"WRITE|/disk/f.txt|68656c6c6f\n"
        )

    def test_empty_parts_joined(self):
        assert format_command("EXEC", "/disk/prog", "") == b"EXEC|/disk/prog|\n"

    def test_output_is_bytes(self):
        result = format_command("PING")
        assert isinstance(result, bytes)

    def test_newline_terminated(self):
        result = format_command("LS", "/disk")
        assert result.endswith(b"\n")


# ---------------------------------------------------------------------------
# parse_response — OK responses
# ---------------------------------------------------------------------------


class TestParseResponseOK:
    def test_ok_no_data(self):
        resp = parse_response("OK\n")
        assert resp.success is True
        assert resp.data == ""

    def test_ok_with_data(self):
        resp = parse_response("OK|PONG\n")
        assert resp.success is True
        assert resp.data == "PONG"

    def test_ok_with_pipe_in_data(self):
        resp = parse_response("OK|key=val1,key2=val2\n")
        assert resp.success is True
        assert resp.data == "key=val1,key2=val2"

    def test_ok_strips_whitespace(self):
        resp = parse_response("  OK|data  \n")
        assert resp.success is True

    def test_ok_with_numeric_data(self):
        resp = parse_response("OK|1024\n")
        assert resp.success is True
        assert resp.data == "1024"


# ---------------------------------------------------------------------------
# parse_response — ERR responses
# ---------------------------------------------------------------------------


class TestParseResponseERR:
    def test_err_with_code_and_message(self):
        resp = parse_response("ERR|2|file not found\n")
        assert resp.success is False
        assert resp.error_code == 2
        assert resp.error_msg == "file not found"

    def test_err_code_only(self):
        resp = parse_response("ERR|5\n")
        assert resp.success is False
        assert resp.error_code == 5
        assert resp.error_msg == "unknown error"

    def test_err_no_parts(self):
        resp = parse_response("ERR\n")
        assert resp.success is False
        assert resp.error_code == -1

    def test_err_zero_code(self):
        resp = parse_response("ERR|0|ok but error\n")
        assert resp.success is False
        assert resp.error_code == 0


# ---------------------------------------------------------------------------
# parse_response — edge cases
# ---------------------------------------------------------------------------


class TestParseResponseEdge:
    def test_empty_line(self):
        resp = parse_response("")
        assert resp.success is False
        assert "empty" in resp.error_msg

    def test_whitespace_only(self):
        resp = parse_response("   \n")
        assert resp.success is False

    def test_malformed(self):
        resp = parse_response("GARBAGE_DATA\n")
        assert resp.success is False
        assert "malformed" in resp.error_msg

    def test_only_newline(self):
        resp = parse_response("\n")
        assert resp.success is False


# ---------------------------------------------------------------------------
# parse_kv_response
# ---------------------------------------------------------------------------


class TestParseKvResponse:
    def test_single_pair(self):
        result = parse_kv_response("uptime_ms=12345")
        assert result == {"uptime_ms": 12345}

    def test_multiple_pairs(self):
        result = parse_kv_response("uptime_ms=1000,mem_total_kb=524288,tasks=3")
        assert result["uptime_ms"] == 1000
        assert result["mem_total_kb"] == 524288
        assert result["tasks"] == 3

    def test_string_value(self):
        result = parse_kv_response("state=RUNNING,name=init")
        assert result["state"] == "RUNNING"
        assert result["name"] == "init"

    def test_empty_string(self):
        result = parse_kv_response("")
        assert result == {}

    def test_no_equals(self):
        result = parse_kv_response("noequalshere")
        assert result == {}

    def test_mixed_int_and_string(self):
        result = parse_kv_response("exit_code=0,output=hello")
        assert result["exit_code"] == 0
        assert result["output"] == "hello"


# ---------------------------------------------------------------------------
# parse_procs_response
# ---------------------------------------------------------------------------


class TestParseProcsResponse:
    def test_single_process(self):
        result = parse_procs_response("1,init,RUNNING,0")
        assert len(result) == 1
        assert result[0] == {"pid": 1, "name": "init", "state": "RUNNING", "ppid": 0}

    def test_multiple_processes(self):
        result = parse_procs_response(
            "1,init,RUNNING,0;2,bridge,READY,1;3,idle,BLOCKED,0"
        )
        assert len(result) == 3
        assert result[0]["name"] == "init"
        assert result[1]["name"] == "bridge"
        assert result[2]["state"] == "BLOCKED"

    def test_empty_string(self):
        result = parse_procs_response("")
        assert result == []

    def test_malformed_row_ignored(self):
        result = parse_procs_response("1,init,RUNNING,0;bad;3,idle,BLOCKED,0")
        assert len(result) == 2

    def test_pid_and_ppid_are_ints(self):
        result = parse_procs_response("42,prog,READY,7")
        assert isinstance(result[0]["pid"], int)
        assert isinstance(result[0]["ppid"], int)
        assert result[0]["pid"] == 42
        assert result[0]["ppid"] == 7


# ---------------------------------------------------------------------------
# hex_encode / hex_decode
# ---------------------------------------------------------------------------


class TestHexCodec:
    def test_encode_hello(self):
        assert hex_encode(b"hello") == "68656c6c6f"

    def test_decode_hello(self):
        assert hex_decode("68656c6c6f") == b"hello"

    def test_roundtrip(self):
        original = b"VOS3 kernel test data \x00\xff"
        assert hex_decode(hex_encode(original)) == original

    def test_empty(self):
        assert hex_encode(b"") == ""
        assert hex_decode("") == b""

    def test_binary_data(self):
        data = bytes(range(256))
        assert hex_decode(hex_encode(data)) == data


# ---------------------------------------------------------------------------
# Command constants
# ---------------------------------------------------------------------------


class TestCommandConstants:
    def test_original_five_commands(self):
        assert CMD_PING == "PING"
        assert CMD_STAT == "STAT"
        assert CMD_WRITE == "WRITE"
        assert CMD_READ == "READ"
        assert CMD_LS == "LS"

    def test_new_seven_commands(self):
        assert CMD_EXEC == "EXEC"
        assert CMD_MKDIR == "MKDIR"
        assert CMD_UNLINK == "UNLINK"
        assert CMD_RENAME == "RENAME"
        assert CMD_SYSINFO == "SYSINFO"
        assert CMD_PROCS == "PROCS"
        assert CMD_HTTPGET == "HTTPGET"

    def test_all_commands_unique(self):
        commands = [
            CMD_PING,
            CMD_STAT,
            CMD_WRITE,
            CMD_READ,
            CMD_LS,
            CMD_EXEC,
            CMD_MKDIR,
            CMD_UNLINK,
            CMD_RENAME,
            CMD_SYSINFO,
            CMD_PROCS,
            CMD_HTTPGET,
        ]
        assert len(set(commands)) == 12


# ---------------------------------------------------------------------------
# BridgeResponse dataclass
# ---------------------------------------------------------------------------


class TestBridgeResponse:
    def test_success_defaults(self):
        resp = BridgeResponse(success=True)
        assert resp.data == ""
        assert resp.error_code == 0
        assert resp.error_msg == ""

    def test_error_response(self):
        resp = BridgeResponse(success=False, error_code=5, error_msg="not found")
        assert resp.success is False
        assert resp.error_code == 5
        assert resp.error_msg == "not found"
