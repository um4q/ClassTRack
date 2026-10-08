"""Build HEXCHK.8xp (TI-84 Plus CE program) from HEXCHK.tib.txt.

The source file is plain UTF-8 text written with the calculator's own token
names ("Disp ", "→", "Str1", "sub(", ...), one TI-BASIC line per text line.
Lines starting with "#" are comments and are not sent to the calculator.

Why not just use tivars' own text encoder? Its "smart" mode silently changes
some punctuation inside string literals ("<=" becomes "≤", "~" becomes the
negative sign, "\\" is dropped as a token separator), and HEXCHK's ASCII
lookup string needs every one of those characters exactly. So this script
tokenizes itself, with simple rules we control:

* outside a string literal: longest match against the TI-84 Plus CE token
  names (the same thing TI Connect CE / SourceCoder do);
* inside a string literal: one character = one token, using a fixed
  character -> token map (so "SIN" stays S,I,N and never becomes sin().

tivars is only used for the token table and to write the .8xp container.

Usage:  python ti84/build.py          (writes ti84/HEXCHK.8xp)
        python ti84/build.py --check  (fails if HEXCHK.8xp is out of date)
Needs:  pip install tivars
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tivars.models import TI_84PCE
from tivars.tokenizer.decoder import decode
from tivars.types import TIProgram
from tivars.var import TIHeader

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "HEXCHK.tib.txt"
OUTPUT = HERE / "HEXCHK.8xp"
PROGRAM_NAME = "HEXCHK"
FILE_COMMENT = "HEXCHK: hex add + DGH checksums (TI-84+CE)"  # max 42 chars

QUOTE = b"\x2a"
STORE = b"\x04"
NEWLINE = b"\x3f"

# Inside a string literal, each character becomes exactly this token. Every
# printable ASCII character except the double quote (which can't appear inside
# a TI-BASIC string literal) is here, plus the few non-ASCII glyphs the
# program uses.
STRING_CHARS: dict[str, bytes] = {
    " ": b"\x29", "!": b"\x2d", "#": b"\xbb\xd2", "$": b"\xbb\xd3",
    "%": b"\xbb\xda", "&": b"\xbb\xd4", "'": b"\xae", "(": b"\x10",
    ")": b"\x11", "*": b"\x82", "+": b"\x70", ",": b"\x2b", "-": b"\x71",
    ".": b"\x3a", "/": b"\x83", ":": b"\x3e", ";": b"\xbb\xd6",
    "<": b"\x6b", "=": b"\x6a", ">": b"\x6c", "?": b"\xaf", "@": b"\xbb\xd1",
    "[": b"\x06", "\\": b"\xbb\xd7", "]": b"\x07", "^": b"\xf0",
    "_": b"\xbb\xd9", "`": b"\xbb\xd5", "{": b"\x08", "|": b"\xbb\xd8",
    "}": b"\x09", "~": b"\xbb\xcf",
    "⁻": b"\xb0", "θ": b"\x5b",
}
for _c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    STRING_CHARS[_c] = _c.encode("ascii")
# Lowercase letters live in the 0xBB two-byte page; 0xBBBB is skipped.
for _i, _c in enumerate("abcdefghijk"):
    STRING_CHARS[_c] = bytes([0xBB, 0xB0 + _i])
for _i, _c in enumerate("lmnopqrstuvwxyz"):
    STRING_CHARS[_c] = bytes([0xBB, 0xBC + _i])


def _code_tokens() -> dict[str, bytes]:
    """Display name -> token bytes for every token the TI-84 Plus CE has."""
    names: dict[str, bytes] = {}
    for bits, token in TI_84PCE.tokens.bytes.items():
        name = token.langs["en"].display
        if not name or name == "\n":
            continue
        # Prefer the one-byte token when two tokens share a display name
        # (e.g. "." is both 0x3A and a CE-only graph style).
        if name not in names or len(bits) < len(names[name]):
            names[name] = bits
    return names


CODE_TOKENS = _code_tokens()
# "Pause " ends in a space, but a bare Pause is written alone on its line and
# the source format forbids trailing whitespace.
CODE_TOKENS["Pause"] = CODE_TOKENS["Pause "]
MAX_NAME = max(len(n) for n in CODE_TOKENS)

# Outside string literals only these tokens may appear. Longest-match
# tokenizing can split a misspelled name into tokens whose names still spell
# it ("inStr(" -> i, n, Str...), so a round trip alone can't catch typos;
# this list can. Add to it deliberately when the program needs a new command.
ALLOWED_CODE = (
    set("ABCDEFGHIJKLMNOPQRSTUVWXYZθ0123456789.,()+-*/^=<>:")
    | {f"Str{i}" for i in range(10)}
    | {"→", "≠", "≤", "≥", "⁻", "ᴇ", " and ", " or ", "not(",
       "If ", "Then", "Else", "End", "For(", "While ", "Lbl ", "Goto ", "Menu(",
       "Input ", "Disp ", "Pause ", "Pause", "ClrHome", "Output(", "DelVar ",
       "sub(", "inString(", "length(", "int(", "abs(", "min("}
)


def tokenize_line(line: str, lineno: int) -> bytes:
    out = bytearray()
    i = 0
    in_string = False
    while i < len(line):
        ch = line[i]
        if in_string:
            if ch == '"':
                out += QUOTE
                in_string = False
            elif ch == "→":
                out += STORE  # "→" also closes a string on the calculator
                in_string = False
            elif ch in STRING_CHARS:
                out += STRING_CHARS[ch]
            else:
                raise SyntaxError(f"line {lineno}: no string token for {ch!r}")
            i += 1
            continue
        if ch == '"':
            out += QUOTE
            in_string = True
            i += 1
            continue
        for length in range(min(MAX_NAME, len(line) - i), 0, -1):
            name = line[i:i + length]
            bits = CODE_TOKENS.get(name)
            if bits is not None:
                if name not in ALLOWED_CODE:
                    raise SyntaxError(f"line {lineno} col {i + 1}: unexpected token {name!r} "
                                      f"(typo? see ALLOWED_CODE)")
                out += bits
                i += length
                break
        else:
            raise SyntaxError(f"line {lineno} col {i + 1}: no token for {line[i:i + 12]!r}")
    return bytes(out)


def source_lines(text: str) -> list[tuple[int, str]]:
    """(line number, code) for every non-comment line, comments removed."""
    lines = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        code = raw.rstrip("\n")
        if code.strip() == "":
            continue
        if code != code.strip():
            raise SyntaxError(f"line {lineno}: leading/trailing whitespace")
        lines.append((lineno, code))
    return lines


def tokenize(text: str) -> bytes:
    return NEWLINE.join(tokenize_line(code, n) for n, code in source_lines(text))


# tivars spells the apostrophe token (0xAE) as "\\'"; on screen it is just '.
DISPLAY_FIXUPS = {"\\'": "'"}


def token_list(data: bytes) -> list[tuple[bytes, str]]:
    """Token bytes -> (bytes, display name) for each token."""
    tokens, _ = decode(data, tokens=TI_84PCE.tokens)
    out = []
    for t in tokens:
        name = t.langs["en"].display
        out.append((t.bits, DISPLAY_FIXUPS.get(name, name)))
    return out


def display_names(data: bytes) -> list[str]:
    """Token bytes -> the calculator's display name for each token."""
    return [name for _, name in token_list(data)]


def detokenize(data: bytes) -> str:
    return "".join(display_names(data))


def build_program(text: str) -> TIProgram:
    program = TIProgram(name=PROGRAM_NAME)
    program.data = tokenize(text)
    return program


def export_bytes(program: TIProgram) -> bytes:
    """The complete .8xp file."""
    header = TIHeader(model=TI_84PCE, comment=FILE_COMMENT)
    return program.export(name=PROGRAM_NAME, header=header, model=TI_84PCE).bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if HEXCHK.8xp does not match the source")
    args = parser.parse_args()

    text = SOURCE.read_text(encoding="utf-8")
    program = build_program(text)

    # Round trip: the bytes must decode back to exactly the source text.
    expected = "\n".join(code for _, code in source_lines(text))
    roundtrip = "\n".join(line.rstrip(" ") for line in detokenize(program.data).split("\n"))
    if roundtrip != expected:
        for n, (a, b) in enumerate(zip(expected.split("\n"), roundtrip.split("\n")), 1):
            if a != b:
                raise SystemExit(f"round-trip mismatch on code line {n}:\n  {a!r}\n  {b!r}")
        raise SystemExit("round-trip mismatch (line count)")

    blob = export_bytes(program)
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != blob:
            print(f"{OUTPUT.name} is out of date - run: python ti84/build.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT.name} is up to date")
        return 0
    OUTPUT.write_bytes(blob)
    print(f"wrote {OUTPUT} ({len(blob)} bytes, program size {len(program.data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
