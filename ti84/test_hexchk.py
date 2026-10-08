"""Tests for the HEXCHK TI-84 Plus CE program.

Run with:  python -m pytest ti84
(needs:    pip install tivars pytest)

The program is executed from its real tokenized bytes by sim.py, and every
answer is compared with a plain-Python reference implementation.
"""

from __future__ import annotations

import random
import struct
import sys
from pathlib import Path

import pytest

pytest.importorskip("tivars")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build  # noqa: E402
import sim  # noqa: E402

DATA = build.tokenize(build.SOURCE.read_text(encoding="utf-8"))

# main menu items
HEX_ADD, DEC_TO_HEX, DGH, VERIFY, HEX_BYTES, HELP, QUIT = range(1, 8)
# first-character menu items
PROMPTS = {"$": 1, "#": 2, "*": 3, "?": 4, "": 5}
BACK = 6

# every character a user can type at an Input prompt (no $ # which come from
# the menu, no " which a TI string can't hold)
TYPABLE = " !%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`{|}~"


# --------------------------------------------------------- reference maths
def ref_checksum(s: str) -> str:
    return f"{sum(map(ord, s)) % 256:02X}"


def ref_hex(n: int) -> str:
    return ("-" if n < 0 else "") + f"{abs(n):X}"


def ref_bytes(hexstr: str) -> dict:
    digits = [c for c in hexstr if c not in " :,"]
    data = bytes.fromhex("".join(digits))
    x = 0
    for b in data:
        x ^= b
    return {"count": len(data), "sum": sum(data), "low": sum(data) % 256,
            "twos": (-sum(data)) % 256, "xor": x}


def run(*keys):
    result = sim.run_program(DATA, list(keys))
    return result


def checksum_lines(prompt: str, typed: str, verify: bool = False) -> list[str]:
    keys = [("menu", VERIFY if verify else DGH), ("menu", PROMPTS[prompt]), ("input", typed),
            ("menu", BACK), ("menu", QUIT)]
    r = run(*keys)
    assert r.finished
    return r.lines


# ------------------------------------------------------------ build / file
def test_source_round_trips_and_file_is_current():
    program = build.build_program(build.SOURCE.read_text(encoding="utf-8"))
    blob = program.export(name=build.PROGRAM_NAME, model=build.TI_84PCE).bytes()
    assert build.OUTPUT.read_bytes() == blob, "HEXCHK.8xp is stale - run python ti84/build.py"


def test_8xp_container_is_valid():
    """Check the file byte-by-byte against the documented .8xp layout,
    independently of tivars (which wrote it)."""
    raw = build.OUTPUT.read_bytes()
    assert raw[:8] == b"**TI83F*"
    assert raw[8:10] == b"\x1a\x0a"
    assert raw[10] in (0x00, 0x13)  # 0x13 = TI-84 Plus CE product id
    body_len = struct.unpack_from("<H", raw, 53)[0]
    body = raw[55:55 + body_len]
    assert len(raw) == 55 + body_len + 2
    assert struct.unpack_from("<H", raw, 55 + body_len)[0] == sum(body) & 0xFFFF
    meta_len, data_len, type_id = struct.unpack_from("<HHB", body, 0)
    assert meta_len == 0x0D
    assert type_id == 0x05  # unprotected program (editable on the calculator)
    name = body[5:13].rstrip(b"\x00")
    assert name == b"HEXCHK"
    version, flag = body[13], body[14]
    assert flag in (0x00, 0x80)
    assert struct.unpack_from("<H", body, 15)[0] == data_len
    var = body[17:17 + data_len]
    assert struct.unpack_from("<H", var, 0)[0] == len(var) - 2
    assert var[2:] == DATA
    assert version <= 0x06  # runs on any TI-84 Plus OS with remainder( etc.


def test_static_rules():
    assert sim.static_check(DATA) == []


def test_lookup_string_is_ascii_32_to_126_without_quote():
    tokens = build.display_names(DATA)
    start = tokens.index('"', tokens.index("0") + 20)  # second string literal
    end = tokens.index('"', start + 1)
    lut = "".join(tokens[start + 1:end])
    expected = "".join(chr(c) for c in range(32, 127) if c != 34)
    assert lut == expected


# --------------------------------------------------------------- hex add
@pytest.mark.parametrize("expr, value", [
    ("24+31+52+44", 0xEB),
    ("FF+1", 0x100),
    ("0", 0),
    ("10-20", -0x10),
    ("⁻5+A", 5),
    ("1 2 3", 6),
    ("ABCDEF+123456", 0xABCDEF + 0x123456),
    ("FFFFFFFFF", 0xFFFFFFFFF),
    ("-FF", -0xFF),
])
def test_hex_add(expr, value):
    r = run(("menu", HEX_ADD), ("input", expr), ("input", ""), ("menu", QUIT))
    lines = r.lines[4:]
    assert lines[0] == "= " + ref_hex(value) + " HEX"
    assert lines[1] == f"= {value} DEC"
    if value >= 0:
        assert lines[2] == f"LAST 2 HEX DIGITS: {value % 256:02X}"
    else:
        assert lines[2] == f"8-BIT 2'S COMP: {value % 256:02X}"


def test_hex_add_random():
    rnd = random.Random(84)
    for _ in range(40):
        terms = [rnd.randrange(0, 16 ** rnd.randint(1, 8)) for _ in range(rnd.randint(1, 6))]
        signs = [rnd.choice("+-") for _ in terms]
        expr = "".join(s + f"{t:X}" for s, t in zip(signs, terms)).lstrip("+")
        value = sum(t if s == "+" else -t for s, t in zip(signs, terms))
        r = run(("menu", HEX_ADD), ("input", expr), ("input", ""), ("menu", QUIT))
        assert r.lines[4] == "= " + ref_hex(value) + " HEX", expr
        assert r.lines[5] == f"= {value} DEC", expr


def test_hex_add_errors_then_keeps_going():
    r = run(("menu", HEX_ADD), ("input", "2G"), ("input", "FFFFFFFFFFF"),
            ("input", "1+1"), ("input", ""), ("menu", QUIT))
    assert r.lines[4:] == ["NOT A DIGIT: G", "NUMBER TOO BIG", "= 2 HEX", "= 2 DEC",
                           "LAST 2 HEX DIGITS: 02"]
    assert r.finished


def test_dec_to_hex():
    r = run(("menu", DEC_TO_HEX), ("input", "235"), ("input", "200+35"),
            ("input", "1A"), ("input", ""), ("menu", QUIT))
    lines = r.lines[3:]
    assert lines[:3] == ["= EB HEX", "= 235 DEC", "LAST 2 HEX DIGITS: EB"]
    assert lines[3:6] == ["= EB HEX", "= 235 DEC", "LAST 2 HEX DIGITS: EB"]
    assert lines[6] == "NOT A DIGIT: A"


# ------------------------------------------------------------ DGH checksum
def test_dgh_example_from_manual():
    lines = checksum_lines("$", "1RD")
    assert lines[2:] == [
        "CHR  HEX  SUM",
        " $   24   24",
        " 1   31   55",
        " R   52   A7",
        " D   44   EB",
        "SUM = EB HEX (235)",
        "CHECKSUM = EB",
        "SEND THIS:",
        "$1RDEB",
    ]


@pytest.mark.parametrize("prompt", ["$", "#", "*", "?", ""])
def test_dgh_checksum_matches_reference(prompt):
    rnd = random.Random(prompt)
    for _ in range(15):
        typed = "".join(rnd.choice(TYPABLE) for _ in range(rnd.randint(1, 20)))
        if prompt == "" and typed.strip() == "":
            continue
        lines = checksum_lines(prompt, typed)
        full = prompt + typed
        total = sum(map(ord, full))
        assert f"SUM = {total:X} HEX ({total})" in lines, typed
        assert f"CHECKSUM = {ref_checksum(full)}" in lines, typed
        sent = "".join(lines[lines.index("SEND THIS:") + 1:])
        assert sent == full + ref_checksum(full), typed


def test_dgh_long_command_pages_every_8_rows():
    typed = "1" + "SU31070142" * 2
    r = run(("menu", DGH), ("menu", 1), ("input", typed), ("menu", BACK), ("menu", QUIT))
    pauses_before_result = 0
    for e in r.events:
        if e.kind == "pause":
            pauses_before_result += 1
        if e.kind == "disp" and e.value.startswith("CHECKSUM"):
            break
    n = len(typed) + 1                     # + the "$"
    full_pages = (n - 1) // 8              # Pause after every 8 rows...
    last_page_rows = n - 8 * full_pages
    expected = full_pages + (last_page_rows > 5)  # ...and before a crowded summary
    assert pauses_before_result == expected
    assert f"CHECKSUM = {ref_checksum('$' + typed)}" in r.lines


def test_negative_key_counts_as_minus():
    lines = checksum_lines("*", "⁻00012.30")
    assert f"CHECKSUM = {ref_checksum('*-00012.30')}" in lines


def test_verify_match_and_mismatch():
    ok = checksum_lines("$", "1RDEB", verify=True)
    assert "RECEIVED EB = MATCH, OK" in ok
    bad = checksum_lines("$", "1RDEA", verify=True)
    assert "RECEIVED EA = WRONG!" in bad
    resp = "+00072.10"
    good = checksum_lines("*", resp + ref_checksum("*" + resp), verify=True)
    assert f"RECEIVED {ref_checksum('*' + resp)} = MATCH, OK" in good


def test_verify_too_short():
    r = run(("menu", VERIFY), ("menu", 5), ("input", "AB"), ("menu", BACK), ("menu", QUIT))
    assert r.lines[1:4] == ["TOO SHORT - TYPE THE", "STRING AND ITS 2-CHARACTER", "CHECKSUM"]
    assert r.finished


def test_non_ascii_character_is_reported():
    # "θ" can be typed (ALPHA 3) but isn't an ASCII character.
    r = run(("menu", DGH), ("menu", 1), ("input", "1Rθ"), ("menu", BACK), ("menu", QUIT))
    assert "NOT AN ASCII CHARACTER:" in r.lines
    assert not any(line.startswith("CHECKSUM") for line in r.lines)
    assert r.finished


def test_blank_entry_goes_back():
    r = run(("menu", DGH), ("menu", 5), ("input", ""), ("menu", BACK), ("menu", QUIT))
    assert r.finished


# -------------------------------------------------------- hex bytes mode
@pytest.mark.parametrize("line", [
    ":0300300002337A1E",          # Intel HEX record including its checksum
    "0300300002337A",              # same record without it
    "01 03 00 00 00 0A",           # Modbus ASCII read request (LRC = F2)
    "FF",
    "00 00",
])
def test_hex_bytes(line):
    r = run(("menu", HEX_BYTES), ("input", line), ("input", ""), ("menu", QUIT))
    ref = ref_bytes(line)
    assert r.lines[5:] == [
        f"{ref['count']} BYTES, SUM = {ref['sum']:X}",
        f"SUM, LAST 2 DIGITS: {ref['low']:02X}",
        f"2'S COMP (INTEL/LRC): {ref['twos']:02X}",
        f"XOR OF BYTES: {ref['xor']:02X}",
    ]


def test_hex_bytes_known_answers():
    r = run(("menu", HEX_BYTES), ("input", "0300300002337A"), ("input", "010300000000A"),
            ("input", "0103000A000A"), ("input", "12G4"), ("input", ""), ("menu", QUIT))
    assert "2'S COMP (INTEL/LRC): 1E" in r.lines      # Intel HEX example record
    assert "ODD NUMBER OF DIGITS" in r.lines
    assert "2'S COMP (INTEL/LRC): E8" in r.lines      # 01+03+00+0A+00+0A = 18 -> E8
    assert "NOT A HEX DIGIT: G" in r.lines


def test_hex_bytes_random_xor_and_sum():
    rnd = random.Random(2026)
    for _ in range(25):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 16)))
        line = data.hex().upper()
        r = run(("menu", HEX_BYTES), ("input", line), ("input", ""), ("menu", QUIT))
        ref = ref_bytes(line)
        assert r.lines[5] == f"{ref['count']} BYTES, SUM = {ref['sum']:X}"
        assert r.lines[8] == f"XOR OF BYTES: {ref['xor']:02X}"


# -------------------------------------------------------------- the rest
def test_help_and_quit_leave_a_clean_screen():
    r = run(("menu", HELP), ("menu", QUIT))
    assert r.finished
    assert r.last_was_output  # Output( last, so the calculator doesn't print "Done"
    assert "SEND $1RDEB" in r.lines


def test_quit_deletes_work_strings():
    interp = sim.Interpreter(DATA, [("menu", DGH), ("menu", 1), ("input", "1RD"),
                                    ("menu", BACK), ("menu", QUIT)])
    interp.execute()
    assert not any(k.startswith("Str") for k in interp.vars)
