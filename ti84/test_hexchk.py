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
    """Run the program with scripted keys; it must use every key and then
    finish (every script here ends by choosing QUIT)."""
    result = sim.run_program(DATA, list(keys))
    assert result.leftover_keys == [], f"unused keys: {result.leftover_keys}"
    assert result.finished, "program stopped early or is still waiting"
    return result


CUE = "(PRESS ENTER)"


def checksum_lines(prompt: str, typed, verify: bool = False) -> list[str]:
    """Lines shown by DGH CHECKSUM / VERIFY, without the "(PRESS ENTER)" cues."""
    keys = [("menu", VERIFY if verify else DGH), ("menu", PROMPTS[prompt]), ("input", typed),
            ("menu", BACK), ("menu", QUIT)]
    return [line for line in run(*keys).lines if line != CUE]


def sent(lines: list[str]) -> str:
    """The full string (with checksum) that DGH CHECKSUM shows at the end."""
    for line in lines:
        if line.startswith("SEND THIS: "):
            return line[len("SEND THIS: "):]
    label = "SEND THIS:" if "SEND THIS:" in lines else "WITH CHECKSUM:"
    return "".join(lines[lines.index(label) + 1:])


# ------------------------------------------------------------ build / file
def test_source_round_trips_and_file_is_current():
    program = build.build_program(build.SOURCE.read_text(encoding="utf-8"))
    blob = build.export_bytes(program)
    assert build.OUTPUT.read_bytes() == blob, "HEXCHK.8xp is stale - run python ti84/build.py"


def test_8xp_container_is_valid():
    """Check the file byte-by-byte against the documented .8xp layout,
    independently of tivars (which wrote it)."""
    raw = build.OUTPUT.read_bytes()
    assert raw[:8] == b"**TI83F*"
    assert raw[8:10] == b"\x1a\x0a"
    assert raw[10] in (0x00, 0x13)  # 0x13 = TI-84 Plus CE product id
    assert raw[11:53].rstrip(b"\x00") == build.FILE_COMMENT.encode("ascii")
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


def test_lookup_string_is_ascii_32_to_126_without_quote_or_lowercase():
    tokens = build.display_names(DATA)
    start = tokens.index('"', tokens.index("0") + 20)  # second string literal
    end = tokens.index('"', start + 1)
    lut = "".join(tokens[start + 1:end])
    expected = "".join(chr(c) for c in range(32, 127) if c != 34 and not chr(c).islower())
    assert lut == expected
    # every token in it is 1 byte except the symbols that only exist as
    # 2-byte tokens; none of them is a lowercase letter
    two_byte = {c for c in lut if len(build.STRING_CHARS[c]) == 2}
    assert two_byte == set("#$%&;@\\_`|~")
    # And byte for byte, written out independently of build.STRING_CHARS:
    # some glyphs have two tokens ("`" is BB9B and BBD5, "." is 3A and EF73)
    # and only these are the plain characters.
    expected_hex = ("29 2D BBD2 BBD3 BBDA BBD4 AE 10 11 82 70 2B 71 3A 83 "
                    + " ".join(f"{c:02X}" for c in range(0x30, 0x3A))
                    + " 3E BBD6 6B 6A 6C AF BBD1 "
                    + " ".join(f"{c:02X}" for c in range(0x41, 0x5B))
                    + " 06 BBD7 07 F0 BBD9 BBD5 08 BBD8 09 BBCF")
    lut_bits = [bits for bits, _ in build.token_list(DATA)[start + 1:end]]
    assert lut_bits == [bytes.fromhex(h) for h in expected_hex.split()]


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
    ("24,31,52,44", 0xEB),
    ("10-⁻5", 0x15),          # minus then (-) key: 10 - (-5)
    ("5--3", 8),
    ("10-5+3", 0xE),
    ("3-0-5", -2),
    ("⁻⁻7", 7),
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
    assert r.lines[4:] == ["NOT A DIGIT:", "G", "NUMBER TOO BIG, MAX IS", "E8D4A50FFF HEX",
                           "= 2 HEX", "= 2 DEC", "LAST 2 HEX DIGITS: 02"]
    assert r.finished


def test_dec_to_hex():
    r = run(("menu", DEC_TO_HEX), ("input", "235"), ("input", "200+35"),
            ("input", "1A"), ("input", ""), ("menu", QUIT))
    lines = r.lines[3:]
    assert lines[:3] == ["= EB HEX", "= 235 DEC", "LAST 2 HEX DIGITS: EB"]
    assert lines[3:6] == ["= EB HEX", "= 235 DEC", "LAST 2 HEX DIGITS: EB"]
    assert lines[6:8] == ["NOT A DIGIT:", "A"]


# ------------------------------------------------------------ DGH checksum
def test_dgh_example_from_manual():
    r = run(("menu", DGH), ("menu", 1), ("input", "1RD"), ("menu", BACK), ("menu", QUIT))
    assert r.screens()[1] == [            # one screen, no extra page
        "$=24 1=31 R=52 D=44",
        "SUM = EB HEX (235 DEC)",
        "CHECKSUM = EB",
        "SEND THIS: $1RDEB",
        CUE,
    ]


@pytest.mark.parametrize("typed", ["1RD", "1DOFF", "1SU31070182", "1AO+00010.00"])
def test_typical_lab_commands_fit_one_screen(typed):
    r = run(("menu", DGH), ("menu", 1), ("input", typed), ("menu", BACK), ("menu", QUIT))
    result = r.screens()[1]
    assert result[-1] == CUE and len(result) <= 9
    assert result[-2] == "SEND THIS: $" + typed + ref_checksum("$" + typed)


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
        assert f"SUM = {total:X} HEX ({total} DEC)" in lines, typed
        assert f"CHECKSUM = {ref_checksum(full)}" in lines, typed
        assert sent(lines) == full + ref_checksum(full), typed
        pairs = " ".join(lines[:lines.index(f"SUM = {total:X} HEX ({total} DEC)")][-((len(full) + 4) // 5):])
        assert pairs == " ".join(f"{c}={ord(c):02X}" for c in full), typed


@pytest.mark.parametrize("n", [4, 25, 40, 41, 45, 80, 81, 120])
def test_dgh_long_command_pages(n):
    typed = ("1" + "SU31070142" * 12)[:n - 1]
    r = run(("menu", DGH), ("menu", 1), ("input", typed), ("menu", BACK), ("menu", QUIT))
    pauses_before_result = 0
    for e in r.events:
        if e.kind == "pause":
            pauses_before_result += 1
        if e.kind == "disp" and e.value.startswith("CHECKSUM"):
            break
    breaks = (n - 1) // 40                   # a page is 8 lines of 5 characters
    last_lines = -(-(n - 40 * breaks) // 5)
    one_line = n < 14
    lines_after = 4 + (0 if one_line else (n + 27) // 26)
    expected = breaks + (last_lines + lines_after > 9)
    assert pauses_before_result == expected
    assert f"CHECKSUM = {ref_checksum('$' + typed)}" in r.lines


def test_negative_key_counts_as_minus():
    lines = checksum_lines("*", "⁻00012.30")
    assert f"CHECKSUM = {ref_checksum('*-00012.30')}" in lines
    assert sent(lines) == "*-00012.30" + ref_checksum("*-00012.30")  # shown as -, not ⁻


def test_verify_match_and_mismatch():
    ok = checksum_lines("$", "1RDEB", verify=True)
    assert ok[-2:] == ["RECEIVED EB", "MATCH - CHECKSUM OK"]
    bad = checksum_lines("$", "1RDEA", verify=True)
    assert bad[-3:] == ["CHECKSUM = EB", "WRONG! RECEIVED:", "EA"]
    resp = "+00072.10"
    good = checksum_lines("*", resp + ref_checksum("*" + resp), verify=True)
    assert good[-2:] == [f"RECEIVED {ref_checksum('*' + resp)}", "MATCH - CHECKSUM OK"]


def test_verify_too_short():
    r = run(("menu", VERIFY), ("menu", 5), ("input", "AB"), ("menu", BACK), ("menu", QUIT))
    assert r.lines[2:6] == ["TOO SHORT - TYPE THE", "STRING AND ITS 2-CHARACTER", "CHECKSUM", CUE]
    assert r.finished


def test_non_ascii_character_is_reported():
    # "θ" can be typed (ALPHA 3) but isn't an ASCII character.
    r = run(("menu", DGH), ("menu", 1), ("input", "1Rθ"), ("menu", BACK), ("menu", QUIT))
    assert "CAN'T USE THIS CHARACTER:" in r.lines
    assert not any(line.startswith("CHECKSUM") for line in r.lines)
    assert r.finished


def test_lowercase_is_not_accepted():
    # can't be typed on a stock CE; make sure it's rejected, not mis-summed
    r = run(("menu", DGH), ("menu", 1), ("input", "1rd"), ("menu", BACK), ("menu", QUIT))
    assert "CAN'T USE THIS CHARACTER:" in r.lines


# Every example printed in the DGH manuals (D1000, D1700, D5000, D3000M).
@pytest.mark.parametrize("prompt, typed, checksum", [
    ("$", "1RD", "EB"),
    ("#", "1RD", "EA"),
    ("#", "1DOFF", "73"),
    ("#", "1DOFF00", "D3"),
    ("$", "1RZ", "01"),            # sum 101: must stay 2 characters
])
def test_dgh_manual_commands(prompt, typed, checksum):
    lines = checksum_lines(prompt, typed)
    assert f"CHECKSUM = {checksum}" in lines
    assert f"SEND THIS: {prompt}{typed}{checksum}" in lines


@pytest.mark.parametrize("reply", ["1RD+00072.10A4", "1DI8000B0"])
def test_dgh_manual_long_form_replies_verify(reply):
    lines = checksum_lines("*", reply, verify=True)
    assert lines[-2:] == [f"RECEIVED {reply[-2:]}", "MATCH - CHECKSUM OK"]


@pytest.mark.parametrize("blank", ["empty", "keep"])
@pytest.mark.parametrize("keys", [
    [("menu", HEX_ADD), ("input", "1+1"), ("input", "")],
    [("menu", HEX_ADD), ("input", "")],
    [("menu", DEC_TO_HEX), ("input", "")],
    [("menu", HEX_BYTES), ("input", "0102"), ("input", "")],
    [("menu", DGH), ("menu", 1), ("input", ""), ("menu", BACK)],
    [("menu", DGH), ("menu", 1), ("input", "1RD"), ("menu", 1), ("input", ""), ("menu", BACK)],
    [("menu", VERIFY), ("menu", 3), ("input", ""), ("menu", BACK)],
    [("menu", DGH), ("menu", 5), ("input", ""), ("menu", BACK)],
])
def test_blank_entry_goes_back(keys, blank):
    """Whatever the OS does with a blank ENTER, it goes straight back to a menu."""
    r = sim.run_program(DATA, keys + [("menu", QUIT)], blank_input=blank)
    assert r.finished
    blank_at = max(i for i, e in enumerate(r.events) if e.kind == "input" and e.value[1] == "")
    assert r.events[blank_at + 1].kind == "menu", r.events[blank_at + 1:]


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
        f"BYTES: {ref['count']}",
        f"SUM = {ref['sum']:X} HEX",
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
    assert r.lines[r.lines.index("NOT A HEX DIGIT:") + 1] == "G"


def test_hex_bytes_random_xor_and_sum():
    rnd = random.Random(2026)
    for _ in range(25):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 16)))
        line = data.hex().upper()
        r = run(("menu", HEX_BYTES), ("input", line), ("input", ""), ("menu", QUIT))
        ref = ref_bytes(line)
        assert r.lines[5:7] == [f"BYTES: {ref['count']}", f"SUM = {ref['sum']:X} HEX"]
        assert r.lines[9] == f"XOR OF BYTES: {ref['xor']:02X}"


# -------------------------------------------------------------- the rest
def test_help_and_quit_leave_a_clean_screen():
    r = run(("menu", HELP), ("menu", QUIT))
    assert r.finished
    assert r.ends_with_value  # a bare value last, so the calculator doesn't print "Done"
    assert "SEND $1RDEB" in r.lines


def test_quit_deletes_work_strings():
    interp = sim.Interpreter(DATA, [("menu", DGH), ("menu", 1), ("input", "1RD"),
                                    ("menu", BACK), ("menu", QUIT)])
    interp.execute()
    assert not any(k.startswith("Str") for k in interp.vars)


# ------------------------------------------------- review follow-ups
def test_signs_reset_after_each_number():
    r = run(("menu", HEX_ADD), ("input", "1-1 1"), ("input", ""), ("menu", QUIT))
    assert r.lines[4] == "= 1 HEX"  # 1 - 1 + 1


@pytest.mark.parametrize("mode, digits", [(HEX_ADD, "F" * 90), (DEC_TO_HEX, "9" * 110),
                                          (HEX_ADD, "1+" + "F" * 120)])
def test_huge_numbers_say_too_big_instead_of_overflowing(mode, digits):
    r = run(("menu", mode), ("input", digits), ("input", ""), ("menu", QUIT))
    assert "NUMBER TOO BIG, MAX IS" in r.lines


@pytest.mark.parametrize("mode, expr, ok", [
    (HEX_ADD, "E8D4A50FFF", True), (HEX_ADD, "E8D4A51000", False),
    (HEX_ADD, "E8D4A50FFF+1", False), (HEX_ADD, "E8D4A50FFF+1-1", True),
    (DEC_TO_HEX, "999999999999+1-2", True),
    (HEX_ADD, "+".join(["E8D4A50FFF"] * 11) + "-" + "-".join(["E8D4A50FFF"] * 11), False),
    (DEC_TO_HEX, "999999999999", True), (DEC_TO_HEX, "1000000000000", False),
    (HEX_ADD, "-E8D4A50FFF", True), (HEX_ADD, "-E8D4A50FFF-1", False),
])
def test_limit_is_below_1e12(mode, expr, ok):
    r = run(("menu", mode), ("input", expr), ("input", ""), ("menu", QUIT))
    assert ("NUMBER TOO BIG, MAX IS" not in r.lines) == ok
    if not ok:
        i = r.lines.index("NUMBER TOO BIG, MAX IS")
        assert r.lines[i + 1] == ("E8D4A50FFF HEX" if mode == HEX_ADD else "999999999999")


def test_simulator_enforces_the_1e100_limit():
    data = build.tokenize("9ᴇ99→A\n10*A→A")
    with pytest.raises(sim.TIError, match="OVERFLOW"):
        sim.run_program(data, [])


@pytest.mark.parametrize("mode, keys, expect", [
    (HEX_ADD, ["1", "normalcdf("], ["NOT A DIGIT:", "normalcdf("]),
    (DEC_TO_HEX, ["sin(", "2"], ["NOT A DIGIT:", "sin("]),
    (HEX_BYTES, ["1", "randIntNoRep("], ["NOT A HEX DIGIT:", "randIntNoRep("]),
])
def test_long_tokens_are_reported_on_their_own_line(mode, keys, expect):
    """E.g. pressing SIN (sin() instead of ALPHA SIN (E)."""
    r = run(("menu", mode), ("input", keys), ("input", ""), ("menu", QUIT))
    i = r.lines.index(expect[0])
    assert r.lines[i:i + 2] == expect


def test_long_token_in_a_dgh_command():
    r = run(("menu", DGH), ("menu", 1), ("input", ["1", "R", "sin("]), ("menu", BACK), ("menu", QUIT))
    assert "$=24 1=31 R=52 ?=??" in r.lines
    i = r.lines.index("CAN'T USE THIS CHARACTER:")
    assert r.lines[i + 1] == "sin("


def test_verify_with_long_tokens_as_the_checksum():
    r = run(("menu", VERIFY), ("menu", 1), ("input", ["1", "R", "D", "sin(", "cos("]),
            ("menu", BACK), ("menu", QUIT))
    assert r.lines[-3:] == ["WRONG! RECEIVED:", "sin(cos(", CUE]


@pytest.mark.parametrize("length", range(1, 120))
@pytest.mark.parametrize("verify", [False, True])
def test_every_length_fits_the_screen(length, verify):
    """sim raises RuleViolation if any line scrolls off before it can be read."""
    rnd = random.Random(length)
    typed = "".join(rnd.choice(TYPABLE.replace(" ", "")) for _ in range(length))
    full = "$" + typed
    if verify and len(full) + 2 > 120:
        pytest.skip("over the 120-character limit once the checksum is typed too")
    if verify:
        lines = checksum_lines("$", typed + ref_checksum(full), verify=True)
        assert lines[-1] == "MATCH - CHECKSUM OK"
    else:
        lines = checksum_lines("$", typed)
        assert sent(lines) == full + ref_checksum(full)


@pytest.mark.parametrize("typed", ["1RDθ123", "1RDθ1234567890123", "θ"])
def test_bad_character_on_a_full_page(typed):
    lines = checksum_lines("$", typed)
    assert "CAN'T USE THIS CHARACTER:" in lines


def test_input_length_limit():
    r = run(("menu", DGH), ("menu", 1), ("input", "1" * 120), ("menu", BACK), ("menu", QUIT))
    kinds = [e.kind for e in r.events]
    i = next(k for k, e in enumerate(r.events) if e.value == "TOO LONG (MAX 120)")
    assert kinds[i + 1:i + 4] == ["disp", "pause", "menu"]  # cue, then straight back
    ok = checksum_lines("$", "1" * 119)
    assert f"CHECKSUM = {ref_checksum('$' + '1' * 119)}" in ok


@pytest.mark.parametrize("prompt, typed", [("#", "1RDEA"), ("?", "1 BAD CHECKSUM"), ("", "$1RDEB")])
def test_verify_through_other_first_characters(prompt, typed):
    if prompt == "?":
        typed += ref_checksum("?" + typed)
    lines = checksum_lines(prompt, typed, verify=True)
    assert lines[-1] == "MATCH - CHECKSUM OK"


def test_verify_reports_bad_character():
    lines = checksum_lines("$", "1θDEB", verify=True)
    assert "CAN'T USE THIS CHARACTER:" in lines


def test_only_first_bad_character_is_reported():
    r = run(("menu", HEX_ADD), ("input", "GH"), ("input", ""), ("menu", QUIT))
    assert r.lines[4:6] == ["NOT A DIGIT:", "G"]


def test_hex_bytes_commas_and_colon_are_ignored():
    r = run(("menu", HEX_BYTES), ("input", ":01,03,00 0A"), ("input", ""), ("menu", QUIT))
    assert r.lines[5:7] == ["BYTES: 4", "SUM = E HEX"]


def test_lookalike_tokens_are_not_ascii():
    """VARS > Statistics "n" looks like a letter but isn't one."""
    data = build.tokenize('"N"→Str1')
    assert sim.program_tokens(data)[1] == "N"
    lookalike = sim.program_tokens(bytes.fromhex("6202"))  # statistics n
    assert lookalike == ["n<6202>"]


# ---------------------------------------------------- round-2 follow-ups
SCENARIOS = [
    [("menu", HELP)],
    [("menu", DGH), ("menu", 1), ("input", "1RD"), ("menu", BACK)],
    [("menu", DGH), ("menu", 1), ("input", "1" * 60), ("menu", BACK)],
    [("menu", DGH), ("menu", 3), ("input", "1RD+00072.10"), ("menu", BACK)],
    [("menu", DGH), ("menu", 1), ("input", "1Rθ"), ("menu", BACK)],
    [("menu", DGH), ("menu", 1), ("input", "1" * 130), ("menu", BACK)],
    [("menu", VERIFY), ("menu", 1), ("input", "1RDEB"), ("menu", BACK)],
    [("menu", VERIFY), ("menu", 5), ("input", "AB"), ("menu", BACK)],
]


@pytest.mark.parametrize("keys", SCENARIOS)
def test_every_pause_says_press_enter(keys):
    """A first-time user sees "(PRESS ENTER)" whenever the program waits."""
    r = run(*keys, ("menu", QUIT))
    for i, e in enumerate(r.events):
        if e.kind == "pause":
            assert r.events[i - 1].kind == "disp" and r.events[i - 1].value == CUE


def test_screen_model_keeps_the_last_row_free():
    """Disp can't keep text on row 10, so 10 lines in a row lose the first."""
    data = build.tokenize("ClrHome\n" + "\n".join(f'Disp "L{i}"' for i in range(10)) + "\nPause")
    with pytest.raises(sim.RuleViolation, match="L0"):
        sim.run_program(data, [])
    data = build.tokenize("ClrHome\n" + "\n".join(f'Disp "L{i}"' for i in range(9)) + "\nPause")
    sim.run_program(data, [])


@pytest.mark.parametrize("prompt, label", [("$", "SEND THIS"), ("#", "SEND THIS"),
                                           ("*", "WITH CHECKSUM:"), ("?", "WITH CHECKSUM:"),
                                           ("", "WITH CHECKSUM:")])
def test_only_commands_say_send(prompt, label):
    lines = checksum_lines(prompt, "1RD")
    assert any(line.startswith(label) for line in lines)
    if prompt not in ("$", "#"):  # replies aren't sent by the student
        assert not any(line.startswith("SEND THIS") for line in lines)


def test_error_reply_verify_has_no_misleading_example():
    r = run(("menu", VERIFY), ("menu", PROMPTS["?"]), ("input", "1 BAD CHECKSUMCA"),
            ("menu", BACK), ("menu", QUIT))
    assert "EG 1 BAD CHECKSUM" not in r.lines
    assert r.lines[:2] == ["TYPE THE REST OF THE REPLY", "THEN ITS 2 CHECKSUM CHARS"]
