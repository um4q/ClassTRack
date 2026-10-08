# HEXCHK: hex calculator + checksums for the TI-84 Plus CE

`HEXCHK.8xp` is a TI-BASIC program for the **TI-84 Plus CE** (it also runs on
the CE Python edition). It does:

- **hex add/subtract**, e.g. `24+31+52+44` → `EB`
- **decimal → hex**
- **DGH checksums**: the 2-character checksum for an address + command, e.g.
  `$1RD` → `$1RDEB`
- **verifying a checksum** you received or were given
- **checksums of raw hex bytes** (Intel HEX records, Modbus ASCII LRC, XOR)

## Putting it on the calculator

1. Install **TI Connect CE** (free from education.ti.com) and plug the
   calculator into the computer with its USB cable. Turn the calculator on.
2. In TI Connect CE, open **Calculator Explorer** (the second icon on the left).
3. **Drag `HEXCHK.8xp` onto the calculator** in that window and confirm
   **Send**. (Or: *Actions → Send to Calculators*.)
4. On the calculator press `prgm`, choose **HEXCHK**, then press `enter`
   twice.

Get the file from this folder: `ti84/HEXCHK.8xp`. On GitHub, open the file and
use **Download raw file**.

## Using it

The main menu:

| # | Item | What it does |
|---|------|--------------|
| 1 | HEX ADD/SUBTRACT | Type `24+31+52+44` → `= EB HEX`, `= 235 DEC`, `LAST 2 HEX DIGITS: EB`. `-` works too (`10-20` → `-10`). Spaces or commas also mean "add". Leave it blank and press `enter` to go back. |
| 2 | DEC TO HEX | Type a decimal number (or a sum like `200+35`) → hex and decimal. |
| 3 | DGH CHECKSUM | Pick the first character (`$`, `#`, `*`, `?`, or none), then type the rest. For example, choose `$` and type `1RD`. It shows each character's ASCII code with a running total, then `CHECKSUM = EB` and `SEND THIS: $1RDEB`. |
| 4 | VERIFY A CHECKSUM | Same, but type the whole string **including** its last 2 checksum characters (e.g. `$`, then `1RDEB`). It says `MATCH, OK` or `WRONG!`. |
| 5 | HEX BYTES CHECKSUM | Type hex bytes (e.g. `0300300002337A`). It shows the byte count, the sum, the last 2 hex digits of the sum, the two's complement (Intel HEX checksum / Modbus LRC) and the XOR of the bytes. Spaces and `:` are ignored. To check a whole Intel HEX line, type it with its checksum: the sum's last 2 digits are `00` when the line is good. |
| 6 | HELP | The checksum rule and key tips, on the calculator. |
| 7 | QUIT | Exits and deletes the string variables it used (Str0–Str9). |

### How the DGH checksum works

Add up the ASCII codes (in hex) of **every** character: the `$` or `#`
prompt, the address, the command, and any data. The checksum is the **last two
hex digits** of that sum. It goes after the command, before the carriage
return.

```
$1RD   →   $ = 24, 1 = 31, R = 52, D = 44
           24 + 31 + 52 + 44 = EB        → send  $1RDEB
```

If the sum goes past `FF` (e.g. `2D4`), keep only the last two digits (`D4`).
The program does this for you and shows the full sum too.

### Typing on the calculator

- Letters: press `alpha` then the key (A is `math`, B is `apps`, C is `prgm`,
  D is `x⁻¹`, E is `sin`, F is `cos`). `2nd` `alpha` locks alpha on; press
  `alpha` again to get numbers back.
- `*` is the `×` key. `-` is the `−` key (the `(−)` key works too). Space is
  `alpha` `0`. `:` is `alpha` `.` and `?` is `alpha` `(−)`.
- `$` and `#` aren't on the keypad, which is why you pick the first character
  from a menu.
- Pressing `enter` on a blank line goes back a menu.
- Press `on` at any time to break out of the program (choose **Quit**).

## For developers

`HEXCHK.tib.txt` is the program source (one TI-BASIC line per line, written
with the calculator's own token names). `HEXCHK.8xp` is built from it:

```
pip install tivars pytest
python ti84/build.py               # rebuild HEXCHK.8xp
python -m pytest ti84              # run the tests
```

`build.py` tokenizes the source itself, because tivars' text encoder changes
some punctuation inside strings. It also rejects any token that isn't on an
allow-list, so a typo like `inStr(` (the real command is `inString(`) can't
slip through.

`sim.py` is a small TI-BASIC interpreter. The tests use it to run the real
tokenized program with scripted keypresses and check every answer against
Python. It also enforces calculator rules:

- no `Goto`/`Menu(` from inside a block (memory leak)
- no line wider than 26 columns
- no output that scrolls off, gets cleared, or is covered by a menu before you
  can read it
