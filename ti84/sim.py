"""A small TI-BASIC interpreter, just big enough to test HEXCHK off-calculator.

It runs the *tokenized bytes* that go into HEXCHK.8xp (not the text source),
using TI-84 Plus CE rules for the commands HEXCHK uses:

  If / Then / Else / End, For( / While / Repeat, Lbl / Goto, Menu(, Input,
  Disp, Pause, ClrHome, Output(, DelVar, Stop / Return, →, and the functions
  sub( inString( length( int( abs( min( max( not( remainder( iPart( fPart(.

Numbers are 14-significant-digit decimals (like the calculator); strings are
sequences of tokens (so "sin(" is one character, exactly as sub(/length( see
it on the calculator).

Besides running the program it enforces the rules a real calculator punishes:
  * Goto / Menu( executed while inside an unfinished If-Then/For/While/Repeat
    block (memory leak -> eventually ERR:MEMORY on a real calculator),
  * any Disp'd line wider than the 26-column home screen,
  * Menu( with more than 7 options or items too wide for the screen,
  * sub( / inString( / length( misuse that would raise an ERR: on the calculator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, Context, Overflow, ROUND_HALF_UP, ROUND_FLOOR, ROUND_DOWN

from build import STRING_CHARS, token_list

SCREEN_COLS = 26
SCREEN_ROWS = 10
MENU_MAX_ITEMS = 7
MENU_ITEM_COLS = SCREEN_COLS - 2  # "1:" prefix
# 14 significant digits, and |x| < 1E100 like the calculator (else ERR:OVERFLOW)
CTX = Context(prec=14, rounding=ROUND_HALF_UP, Emax=99, Emin=-99)



class TIError(Exception):
    """An error the calculator would show as ERR:<kind>."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"ERR:{kind} {detail}".strip())
        self.kind = kind


class RuleViolation(Exception):
    """Something that works in the simulator but is unsafe on a real calculator."""


class OutOfInput(Exception):
    """The test script ran out of keypresses while the program wanted more."""


def program_tokens(data: bytes) -> list[str]:
    """Token names, kept distinct by bytes: a token that merely *looks* like
    one of the program's string characters (e.g. the statistics variable "n"
    vs the letter n) gets its hex code appended so it never compares equal."""
    out = []
    for bits, name in token_list(data):
        if name in STRING_CHARS and STRING_CHARS[name] != bits:
            name = f"{name}<{bits.hex()}>"
        out.append(name)
    return out


def typed(keys) -> tuple[str, ...]:
    """What a user typing `keys` at an Input prompt produces. A str is one
    token per character ("⁻" is the (-) key, "-" the minus key, "*" the times
    key); a list/tuple gives the tokens explicitly, e.g. ["1", "sin("]."""
    return tuple(keys)


def text(s: tuple[str, ...]) -> str:
    return "".join(s)


def num(x) -> Decimal:
    return CTX.plus(Decimal(x))


@dataclass
class Event:
    kind: str           # "disp", "input", "menu", "pause", "clrhome", "output"
    value: object = None


class Screen:
    """The 10-row home screen (9 usable by Disp). A row counts as read once the program waits for
    the user (Pause, Input, or the program ending) while it is on screen; a
    row that scrolls off, is cleared, or is covered by a Menu( before that is
    a RuleViolation - the user never got to see it."""

    def __init__(self):
        self.rows: list[list] = []  # [text, read?]

    def _lost(self, how: str):
        unread = [r[0] for r in self.rows if not r[1]]
        if unread:
            raise RuleViolation(f"{how} before the user could read: {unread}")

    def add(self, line: str, read: bool = False):
        # Writing the last (10th) row scrolls the screen straight away, so at
        # most 9 rows of Disp/Input output are visible at once.
        for k in range(0, max(len(line), 1), SCREEN_COLS):
            self.rows.append([line[k:k + SCREEN_COLS], read])
            if len(self.rows) > SCREEN_ROWS - 1:
                top = self.rows.pop(0)
                if not top[1]:
                    raise RuleViolation(f"line scrolled off unread: {top[0]!r}")

    def wait(self):
        for r in self.rows:
            r[1] = True

    def clear(self):
        self._lost("ClrHome")
        self.rows = []

    def menu(self):
        self._lost("Menu( covered the screen")
        self.rows = []


@dataclass
class Run:
    events: list[Event] = field(default_factory=list)
    finished: bool = False
    leftover_keys: list = field(default_factory=list)
    ends_with_value: bool = False  # last statement was a bare value: no "Done"

    @property
    def lines(self) -> list[str]:
        """Every Disp'd line, in order."""
        return [e.value for e in self.events if e.kind == "disp"]

    def screens(self) -> list[list[str]]:
        """Disp'd lines grouped between ClrHome calls."""
        out = [[]]
        for e in self.events:
            if e.kind == "clrhome":
                out.append([])
            elif e.kind == "disp":
                out[-1].append(e.value)
        return [s for s in out if s]


STATEMENT_OPENERS = ("For(", "While ", "Repeat ")


class Interpreter:
    def __init__(self, data: bytes, keys: list, *, blank_input: str = "empty",
                 max_steps: int = 2_000_000):
        # What Input does when the user presses ENTER on a blank line isn't
        # documented for the CE: it may store "" ("empty") or leave the
        # variable as it was ("keep"). HEXCHK must work either way.
        if blank_input not in ("empty", "keep"):
            raise ValueError(blank_input)
        self.blank_input = blank_input
        self.stmts = self._split(program_tokens(data))
        self.labels: dict[str, int] = {}
        for i, st in enumerate(self.stmts):
            if st and st[0] == "Lbl ":
                name = "".join(st[1:])
                if name in self.labels:
                    raise RuleViolation(f"duplicate Lbl {name}")
                self.labels[name] = i
        self.keys = list(keys)
        self.vars: dict[str, object] = {}
        self.stack: list[tuple] = []
        self.run = Run()
        self.screen = Screen()
        self.max_steps = max_steps

    # ------------------------------------------------------------ parsing
    @staticmethod
    def _split(tokens: list[str]) -> list[list[str]]:
        stmts, cur, in_string = [], [], False
        for t in tokens:
            if t == "\n" or (t == ":" and not in_string):
                stmts.append(cur)
                cur, in_string = [], False
                continue
            if t == '"':
                in_string = not in_string
            elif t == "→":
                in_string = False
            cur.append(t)
        stmts.append(cur)
        return stmts

    def _next_key(self, kind: str):
        if not self.keys:
            raise OutOfInput(kind)
        key = self.keys.pop(0)
        if key[0] != kind:
            raise AssertionError(f"program wants {kind!r} but the script has {key!r}")
        return key[1] if len(key) > 1 else None

    # ---------------------------------------------------------- execution
    def execute(self) -> Run:
        pc, steps = 0, 0
        try:
            while pc < len(self.stmts):
                steps += 1
                if steps > self.max_steps:
                    raise RuntimeError("step limit hit (infinite loop?)")
                try:
                    pc = self._step(pc)
                except Overflow:
                    raise TIError("OVERFLOW") from None
            self.screen.wait()
            self.run.finished = True
        except OutOfInput:
            pass
        self.run.leftover_keys = list(self.keys)
        return self.run

    def _match_end(self, start: int, stop_at_else: bool) -> int:
        """Index of the End (or Else, if allowed) closing the block opened before `start`."""
        depth = 0
        for j in range(start, len(self.stmts)):
            st = self.stmts[j]
            if not st:
                continue
            if st == ["Then"] or st[0] in STATEMENT_OPENERS:
                depth += 1
            elif st == ["End"]:
                if depth == 0:
                    return j
                depth -= 1
            elif st == ["Else"] and depth == 0 and stop_at_else:
                return j
        raise TIError("SYNTAX", "missing End")

    def _goto(self, label: str, why: str) -> int:
        if self.stack:
            raise RuleViolation(f"{why} {label} from inside {self.stack[-1][0]} block")
        if label not in self.labels:
            raise TIError("LABEL", label)
        return self.labels[label] + 1

    def _step(self, pc: int) -> int:
        st = self.stmts[pc]
        self.run.ends_with_value = False
        if not st:
            return pc + 1
        head = st[0]

        if head == "If ":
            cond = self._truth(self.eval(st[1:]))
            nxt = self.stmts[pc + 1] if pc + 1 < len(self.stmts) else None
            if nxt == ["Then"]:
                if cond:
                    self.stack.append(("If-Then",))
                    return pc + 2
                j = self._match_end(pc + 2, stop_at_else=True)
                if self.stmts[j] == ["Else"]:
                    self.stack.append(("If-Then",))
                return j + 1
            return pc + 1 if cond else pc + 2

        if st == ["Then"]:
            raise TIError("SYNTAX", "Then without If")

        if st == ["Else"]:
            if not self.stack or self.stack[-1][0] != "If-Then":
                raise TIError("SYNTAX", "Else outside If-Then")
            self.stack.pop()
            return self._match_end(pc + 1, stop_at_else=False) + 1

        if st == ["End"]:
            if not self.stack:
                raise TIError("SYNTAX", "End without block")
            block = self.stack.pop()
            if block[0] == "If-Then":
                return pc + 1
            if block[0] == "For(":
                _, for_pc, var, end, step = block
                value = num(self.vars[var] + step)
                self.vars[var] = value
                if (step > 0 and value <= end) or (step < 0 and value >= end):
                    self.stack.append(block)
                    return for_pc + 1
                return pc + 1
            if block[0] == "While ":
                return block[1]  # re-test the condition
            if block[0] == "Repeat ":
                rep_pc = block[1]
                if self._truth(self.eval(self.stmts[rep_pc][1:])):
                    return pc + 1
                self.stack.append(block)
                return rep_pc + 1

        if head == "For(":
            args = self._args(st[1:], closing=True)
            if len(args) not in (3, 4):
                raise TIError("ARGUMENT", "For(")
            var = "".join(args[0])
            start, end = self._number(args[1]), self._number(args[2])
            step = self._number(args[3]) if len(args) == 4 else num(1)
            if step == 0:
                raise TIError("INCREMENT")
            self.vars[var] = start
            if (step > 0 and start > end) or (step < 0 and start < end):
                return self._match_end(pc + 1, stop_at_else=False) + 1
            self.stack.append(("For(", pc, var, end, step))
            return pc + 1

        if head == "While ":
            if self._truth(self.eval(st[1:])):
                self.stack.append(("While ", pc))
                return pc + 1
            return self._match_end(pc + 1, stop_at_else=False) + 1

        if head == "Repeat ":
            self.stack.append(("Repeat ", pc))
            return pc + 1

        if head == "Lbl ":
            return pc + 1

        if head == "Goto ":
            return self._goto("".join(st[1:]), "Goto")

        if head == "Menu(":
            args = self._args(st[1:], closing=True)
            title = self._string(args[0])
            pairs = args[1:]
            if len(pairs) % 2 or not pairs:
                raise TIError("ARGUMENT", "Menu(")
            items = [(self._string(pairs[i]), "".join(pairs[i + 1])) for i in range(0, len(pairs), 2)]
            if len(items) > MENU_MAX_ITEMS:
                raise RuleViolation(f"Menu( has {len(items)} items (max {MENU_MAX_ITEMS})")
            if len(text(title)) > SCREEN_COLS:
                raise RuleViolation(f"Menu( title too wide: {text(title)!r}")
            for item, label in items:
                if len(text(item)) > MENU_ITEM_COLS:
                    raise RuleViolation(f"Menu( item too wide: {text(item)!r}")
                if label not in self.labels:
                    raise TIError("LABEL", label)
            self.screen.menu()
            self.run.events.append(Event("menu", (text(title), [text(i) for i, _ in items])))
            choice = self._next_key("menu")
            if not 1 <= choice <= len(items):
                raise AssertionError(f"menu choice {choice} out of range for {items}")
            return self._goto(items[choice - 1][1], "Menu(")

        if head == "Input ":
            args = self._args(st[1:], closing=False)
            prompt = self._string(args[0]) if len(args) == 2 else ("?",)
            var = "".join(args[-1])
            value = self._next_key("input")
            if not var.startswith("Str"):
                raise RuleViolation("HEXCHK should only Input into strings")
            if value or self.blank_input == "empty":
                self.vars[var] = typed(value)
            self.screen.wait()
            self.screen.add(text(prompt) + text(typed(value)), read=True)
            self.run.events.append(Event("input", (text(prompt), text(typed(value)))))
            return pc + 1

        if head == "Disp ":
            for arg in self._args(st[1:], closing=False):
                value = self.eval(arg)
                if isinstance(value, tuple):
                    line = text(value)
                    if len(line) > SCREEN_COLS:
                        raise RuleViolation(f"Disp line wider than {SCREEN_COLS} columns: {line!r}")
                else:
                    line = self._format(value)
                self.screen.add(line)
                self.run.events.append(Event("disp", line))
            return pc + 1

        if head == "Pause ":
            if len(st) > 1:
                raise RuleViolation("HEXCHK doesn't use Pause with an argument")
            self.screen.wait()
            self.run.events.append(Event("pause"))
            return pc + 1

        if st == ["ClrHome"]:
            self.screen.clear()
            self.run.events.append(Event("clrhome"))
            return pc + 1

        if head == "Output(":
            args = self._args(st[1:], closing=True)
            row, col = self._number(args[0]), self._number(args[1])
            value = self.eval(args[2])
            if not (1 <= row <= 10 and 1 <= col <= SCREEN_COLS):
                raise TIError("DOMAIN", "Output( position")
            self.run.events.append(Event("output", (int(row), int(col), text(value))))
            return pc + 1

        if head == "DelVar ":
            if len(st) != 2:
                raise RuleViolation("one DelVar per line, please")
            self.vars.pop("".join(st[1:]), None)
            return pc + 1

        if st in (["Stop"], ["Return"]):
            return len(self.stmts)

        # expression statement, possibly with → stores
        if "→" in st:
            i = len(st) - 1 - st[::-1].index("→")
            value = self.eval(st[:i])
            target = "".join(st[i + 1:])
            if target.startswith("Str"):
                if not isinstance(value, tuple):
                    raise TIError("DATA TYPE", f"number → {target}")
            elif isinstance(value, tuple):
                raise TIError("DATA TYPE", f"string → {target}")
            self.vars[target] = value
            self.vars["Ans"] = value
            return pc + 1

        self.vars["Ans"] = self.eval(st)
        self.run.ends_with_value = True
        return pc + 1

    # -------------------------------------------------------- expressions
    def _args(self, toks: list[str], closing: bool) -> list[list[str]]:
        """Split on top-level commas (outside strings/parens); drop a final ')'."""
        args, cur, depth, in_string = [], [], 0, False
        for t in toks:
            if t == '"':
                in_string = not in_string
            elif not in_string:
                if t.endswith("(") or t == "(":
                    depth += 1
                elif t == ")":
                    if depth == 0 and closing:
                        continue
                    depth -= 1
                elif t == "," and depth == 0:
                    args.append(cur)
                    cur = []
                    continue
            cur.append(t)
        args.append(cur)
        return args

    def _string(self, toks) -> tuple:
        v = self.eval(toks)
        if not isinstance(v, tuple):
            raise TIError("DATA TYPE", "expected a string")
        return v

    def _number(self, toks) -> Decimal:
        v = self.eval(toks)
        if isinstance(v, tuple):
            raise TIError("DATA TYPE", "expected a number")
        return v

    @staticmethod
    def _truth(v) -> bool:
        if isinstance(v, tuple):
            raise TIError("DATA TYPE", "string used as a condition")
        return v != 0

    @staticmethod
    def _format(v: Decimal) -> str:
        if v == v.to_integral_value():
            return str(int(v))
        return format(v.normalize(), "f")

    def eval(self, toks: list[str]):
        parser = _Parser(toks, self)
        value = parser.expr()
        if parser.i != len(toks):
            raise TIError("SYNTAX", f"unparsed: {''.join(toks[parser.i:])!r} in {''.join(toks)!r}")
        return value


FUNCS = {"sub(", "inString(", "length(", "int(", "abs(", "min(", "max(", "not(",
         "remainder(", "iPart(", "fPart("}
VARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZθ") | {f"Str{i}" for i in range(10)} | {"Ans"}
DIGITS = set("0123456789.")


class _Parser:
    """Precedence (low -> high): or/xor, and, relations, + -, * / and implied
    multiplication, negation, ^, atoms.  Closing parens may be omitted at the
    end of the statement, as on the calculator."""

    def __init__(self, toks: list[str], interp: Interpreter):
        self.t, self.i, self.interp = toks, 0, interp

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expr(self):
        left = self.and_()
        while self.peek() in (" or ", " xor "):
            op = self.take()
            right = self.and_()
            a, b = self.interp._truth(left), self.interp._truth(right)
            left = num(int(a or b) if op == " or " else int(a != b))
        return left

    def and_(self):
        left = self.rel()
        while self.peek() == " and ":
            self.take()
            right = self.rel()
            left = num(int(self.interp._truth(left) and self.interp._truth(right)))
        return left

    def rel(self):
        left = self.add()
        while self.peek() in ("=", "≠", "<", ">", "≤", "≥"):
            op = self.take()
            right = self.add()
            if isinstance(left, tuple) or isinstance(right, tuple):
                if not (isinstance(left, tuple) and isinstance(right, tuple)) or op not in ("=", "≠"):
                    raise TIError("DATA TYPE", f"string {op}")
                result = (left == right) == (op == "=")
            else:
                result = {"=": left == right, "≠": left != right, "<": left < right,
                          ">": left > right, "≤": left <= right, "≥": left >= right}[op]
            left = num(int(result))
        return left

    def add(self):
        left = self.mul()
        while self.peek() in ("+", "-"):
            op = self.take()
            right = self.mul()
            if op == "+" and isinstance(left, tuple) and isinstance(right, tuple):
                left = left + right
            elif isinstance(left, tuple) or isinstance(right, tuple):
                raise TIError("DATA TYPE", f"string {op}")
            else:
                left = CTX.add(left, right) if op == "+" else CTX.subtract(left, right)
        return left

    def _starts_atom(self, tok) -> bool:
        return tok is not None and (tok in DIGITS or tok in VARS or tok in FUNCS or tok == "(")

    def mul(self):
        left = self.neg()
        while self.peek() in ("*", "/") or self._starts_atom(self.peek()):
            op = self.take() if self.peek() in ("*", "/") else "*"
            right = self.neg()
            if isinstance(left, tuple) or isinstance(right, tuple):
                raise TIError("DATA TYPE", f"string {op}")
            if op == "/" and right == 0:
                raise TIError("DIVIDE BY 0")
            left = CTX.multiply(left, right) if op == "*" else CTX.divide(left, right)
        return left

    def neg(self):
        if self.peek() == "⁻":
            self.take()
            v = self.neg()
            if isinstance(v, tuple):
                raise TIError("DATA TYPE", "negated string")
            return -v
        return self.pow()

    def pow(self):
        left = self.atom()
        while self.peek() == "^":
            self.take()
            right = self.atom()
            left = CTX.power(left, right)
        return left

    def close(self):
        if self.peek() == ")":
            self.take()
        elif self.peek() is not None:
            raise TIError("SYNTAX", f"expected ) at {self.t[self.i:]}")

    def atom(self):
        tok = self.take()
        if tok is None:
            raise TIError("SYNTAX", "missing value")
        if tok == '"':
            chars = []
            while self.peek() is not None and self.peek() != '"':
                chars.append(self.take())
            if self.peek() == '"':
                self.take()
            return tuple(chars)
        if tok in DIGITS:
            s = tok
            while self.peek() in DIGITS:
                s += self.take()
            if self.peek() == "ᴇ":
                self.take()
                s += "E"
                if self.peek() == "⁻":
                    self.take()
                    s += "-"
                while self.peek() is not None and self.peek().isdigit():
                    s += self.take()
            return num(s)
        if tok in VARS:
            if tok not in self.interp.vars:
                if tok.startswith("Str"):
                    raise TIError("UNDEFINED", tok)
                self.interp.vars[tok] = num(0)
            return self.interp.vars[tok]
        if tok == "(":
            v = self.expr()
            self.close()
            return v
        if tok in FUNCS:
            args = [self.expr()]
            while self.peek() == ",":
                self.take()
                args.append(self.expr())
            self.close()
            return self.call(tok, args)
        raise TIError("SYNTAX", f"unexpected {tok!r}")

    def call(self, name: str, args: list):
        def n(i):
            if isinstance(args[i], tuple):
                raise TIError("DATA TYPE", name)
            return args[i]

        def s(i):
            if not isinstance(args[i], tuple):
                raise TIError("DATA TYPE", name)
            return args[i]

        if name == "sub(":
            if len(args) != 3:
                raise TIError("ARGUMENT", "sub(")
            string, start, length = s(0), n(1), n(2)
            if start != int(start) or length != int(length):
                raise TIError("DOMAIN", "sub( non-integer")
            start, length = int(start), int(length)
            if start < 1 or length < 1 or start + length - 1 > len(string):
                raise TIError("INVALID DIM", f"sub({text(string)!r},{start},{length})")
            return string[start - 1:start - 1 + length]
        if name == "inString(":
            hay, needle = s(0), s(1)
            start = int(n(2)) if len(args) == 3 else 1
            if not needle:
                raise RuleViolation("inString( with an empty search string")
            for k in range(start - 1, len(hay) - len(needle) + 1):
                if hay[k:k + len(needle)] == needle:
                    return num(k + 1)
            return num(0)
        if name == "length(":
            return num(len(s(0)))
        if name == "int(":
            return n(0).to_integral_value(rounding=ROUND_FLOOR)
        if name == "iPart(":
            return n(0).to_integral_value(rounding=ROUND_DOWN)
        if name == "fPart(":
            return CTX.subtract(n(0), n(0).to_integral_value(rounding=ROUND_DOWN))
        if name == "abs(":
            return abs(n(0))
        if name == "min(":
            return min(n(0), n(1))
        if name == "max(":
            return max(n(0), n(1))
        if name == "not(":
            return num(int(n(0) == 0))
        if name == "remainder(":
            a, b = n(0), n(1)
            return CTX.subtract(a, CTX.multiply(b, (a / b).to_integral_value(rounding=ROUND_DOWN)))
        raise TIError("SYNTAX", name)


def run_program(data: bytes, keys: list, *, blank_input: str = "empty") -> Run:
    """Run token bytes with scripted keys: ("menu", n) picks item n,
    ("input", "text") types text then ENTER. Pauses need no key."""
    return Interpreter(data, keys, blank_input=blank_input).execute()


def static_check(data: bytes) -> list[str]:
    """Problems found without running: Goto/Menu( inside a block, unknown or
    duplicate labels, unbalanced blocks."""
    problems = []
    interp = Interpreter.__new__(Interpreter)
    stmts = Interpreter._split(program_tokens(data))
    labels = {}
    for i, st in enumerate(stmts):
        if st and st[0] == "Lbl ":
            name = "".join(st[1:])
            if name in labels:
                problems.append(f"duplicate Lbl {name}")
            labels[name] = i
    depth = 0
    for i, st in enumerate(stmts):
        if not st:
            continue
        if st == ["Then"] or st[0] in STATEMENT_OPENERS:
            depth += 1
        elif st == ["End"]:
            depth -= 1
            if depth < 0:
                problems.append(f"statement {i}: End without a block")
                depth = 0
        targets = []
        if st[0] == "Goto ":
            targets = ["".join(st[1:])]
        elif st[0] == "Menu(":
            args = Interpreter._args(interp, st[1:], closing=True)
            targets = ["".join(a) for a in args[2::2]]
        if st[0] == "Lbl " and depth:
            problems.append(f"statement {i}: Lbl {''.join(st[1:])} inside a block")
        for label in targets:
            if label not in labels:
                problems.append(f"statement {i}: no Lbl {label}")
            if depth:
                problems.append(f"statement {i}: {st[0].strip()} {label} inside a block")
            # A Goto right after a single-line If at depth 0 is fine.
    if depth:
        problems.append(f"{depth} block(s) never closed with End")
    return problems
