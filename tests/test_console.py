# -*- coding: utf-8 -*-
"""The scripting console: evaluation, the safety net, and the namespace.

Pure -- a fake machine, no Qt. The safety net gets the most attention: this code
runs inside a Qt slot, and an exception reaching PyQt calls qFatal(), killing the
process with no message. A REPL that let one through would be a crash generator
with a prompt on it.
"""

from core import console as con


class _FakeMachine:
    def __init__(self):
        self.b = bytearray(0x400000)
        self.b[0x200000:0x200008] = b"HELLOxyz"
        self.written: list = []

    def read(self, addr, n=1):
        return bytes(self.b[addr:addr + n])

    def write(self, addr, data):
        self.written.append((addr, bytes(data)))
        self.b[addr:addr + len(data)] = data

    def cpu(self):
        class _C:
            pc = 0x201234
            regs = (1, 2, 3, 4, 5, 6, 7, 8)
        return _C()

    def framebuffer(self):
        return [0] * (152 * 160)


class _FakePlay:
    def __init__(self):
        self.frame_hooks: list = []
        self.steps = 0

    def step_forward(self):
        self.steps += 1


def _console(machine=None, play=None):
    c = con.Console()
    c.set_namespace(con.build_namespace(machine or _FakeMachine(),
                                        play or _FakePlay()))
    return c


# ---------------------------------------------------------------- evaluation
def test_an_expression_echoes_its_value():
    assert _console().run("1 + 1").output.strip() == "2"


def test_a_statement_echoes_nothing():
    c = _console()
    assert c.run("x = 5").output == ""
    assert c.run("x").output.strip() == "5"


def test_print_is_captured_not_lost_to_the_terminal():
    """The window is the only place the user is looking."""
    assert "hello" in _console().run("print('hello')").output


def test_the_last_value_is_kept_as_underscore():
    c = _console()
    c.run("40 + 2")
    assert c.run("_").output.strip() == "42"


def test_a_block_spanning_lines_is_reported_incomplete_not_broken():
    """Raising a syntax error at someone mid-thought is how a console teaches you
    not to write loops in it."""
    c = _console()
    assert c.run("for i in range(3):").incomplete
    assert not c.run("for i in range(3):\n    pass\n").incomplete


def test_real_syntax_errors_are_not_mistaken_for_unfinished_input():
    r = _console().run("1 +* 2")
    assert r.failed and not r.incomplete


# ---------------------------------------------------------------- the net
def test_an_exception_is_captured_never_raised():
    r = _console().run("1 / 0")
    assert r.failed
    assert "ZeroDivisionError" in r.output


def test_the_traceback_starts_at_the_users_line_not_ours():
    """Our exec frame at the top of every mistake reads as 'the console is
    broken'."""
    out = _console().run("1 / 0").output
    assert "core\\console.py" not in out and "core/console.py" not in out


def test_even_a_keyboard_interrupt_or_base_exception_is_contained():
    r = _console().run("raise BaseException('boom')")
    assert r.failed and "boom" in r.output


def test_exit_does_not_take_the_emulator_with_it():
    """`exit()` in a debugger console means 'close the console', never 'tear down
    a running game'."""
    r = _console().run("exit()")
    assert not r.failed
    assert "close" in r.output


def test_a_missing_machine_answers_instead_of_crashing_on_none():
    c = con.Console()
    c.set_namespace(con.build_namespace(None, None))
    r = c.run("u8(0x200000)")
    assert r.failed and "no game running" in r.output


# ---------------------------------------------------------------- namespace
def test_the_namespace_is_documented_by_help():
    c = _console()
    out = c.run("help()").output
    for name in ("read", "u16", "on_frame", "hexdump", "hwregs"):
        assert name in out, f"help() must list {name}"


def test_help_lists_only_names_that_exist():
    """A namespace you have to guess at is not an API — and one whose help lies
    about it is worse."""
    ns = con.build_namespace(_FakeMachine(), _FakePlay())
    for line in con.HELP.splitlines():
        for word in line.replace("/", " ").split():
            base = word.split("(")[0]
            if base.isidentifier() and base.islower() and len(base) > 2:
                if base in ("the", "and", "one", "how", "you", "already",
                            "imported", "returns", "call", "every", "holds",
                            "what", "here", "everything", "window", "itself",
                            "uses", "machine", "scope", "prints", "this",
                            "value", "last", "currently", "subscribed", "per",
                            "function", "cycles", "whole", "scroll", "plane",
                            "hardware", "register", "decoded", "bytes", "from",
                            "bus", "onto", "addresses", "byte", "pattern",
                            "printable", "hex", "disassembly", "sound", "run",
                            "frames", "framebuffer", "state", "registers",
                            "dict", "page", "pause", "native", "player",
                            "canceller", "modules", "printed"):
                    continue
                assert base in ns, f"help() mentions `{base}`, which does not exist"


def test_helpers_read_the_machine():
    c = _console()
    assert c.run("u8(0x200000)").output.strip() == "72"          # 'H'
    assert c.run("read(0x200000, 5)").output.strip() == "b'HELLO'"
    assert c.run("hex(u16(0x200000))").output.strip() == "'0x4548'"


def test_find_locates_a_pattern_and_reports_addresses():
    c = _console()
    out = c.run("[hex(a) for a in find(b'HELLO')]").output
    assert "0x200000" in out


def test_poke_goes_through_the_machines_own_write():
    m = _FakeMachine()
    c = _console(m)
    c.run("poke(0x004000, 0xAB)")
    assert m.written and m.written[-1] == (0x004000, b"\xab")


def test_hexdump_is_printable():
    out = _console().run("print(hexdump(0x200000, 16))").output
    assert "200000" in out and "HELLO" in out


def test_regs_names_the_registers():
    c = _console()
    assert c.run("hex(regs()['PC'])").output.strip() == "'0x201234'"
    assert c.run("regs()['XSP']").output.strip() == "8"


def test_on_frame_subscribes_and_returns_a_canceller():
    """Per-frame is the granularity the one-off scripts always needed: sampling at
    the window's refresh rate counts eight of six hundred thousand instructions."""
    play = _FakePlay()
    c = _console(play=play)
    c.run("seen = []")
    c.run("cancel = on_frame(lambda: seen.append(1))")
    assert len(play.frame_hooks) == 1
    play.frame_hooks[0]()
    assert c.run("len(seen)").output.strip() == "1"
    c.run("cancel()")
    assert play.frame_hooks == []


def test_step_drives_the_player():
    play = _FakePlay()
    _console(play=play).run("step(3)")
    assert play.steps == 3


# ---------------------------------------------------------------- session
def test_history_records_what_was_run():
    c = _console()
    c.run("1")
    c.run("   ")            # blank lines are not history
    c.run("2")
    assert c.history == ["1", "2"]


def test_reloading_the_machine_keeps_what_the_user_defined():
    """A console that forgot your helper every time a game was loaded would train
    you not to write one."""
    c = _console()
    c.run("def mine(): return 7")
    c.set_namespace(con.build_namespace(_FakeMachine(), _FakePlay()))
    assert c.run("mine()").output.strip() == "7"


def test_reloading_the_machine_replaces_the_machine():
    c = _console()
    first = c.namespace["m"]
    c.set_namespace(con.build_namespace(_FakeMachine(), _FakePlay()))
    assert c.namespace["m"] is not first
