"""Mirror netplay -- exchange the BUTTONS, not the cable (core/netplay.py).

Two layers:

* the session logic against a pipe made of two lists (always runs, no ROM needed);
* the whole thing for real: FOUR consoles, two mirror sessions wired to each other
  exactly as two PCs would be, running the link probe ROM. That one needs the retail
  BIOS and the probe ROM, so it skips when either is absent.

The property the real test pins is the one the mode exists for: **the game's speed
does not depend on the network delay**, and the two PCs stay bit-identical.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import lobby  # noqa: E402
from core import netplay  # noqa: E402
from core.netplay import Handshake, ListPipe, MirrorSession, SocketPipe  # noqa: E402

# ⚡ IMPORTED AT MODULE LEVEL ON PURPOSE, not inside the test. The root conftest
# sandboxes the coin cell by patching `ngpc_shell._SYSTEM_RAM`, and it can only do
# that for a module already in sys.modules when the fixture runs -- importing the
# shell inside the test body means `stop()` writes the PLAYER'S system.ram instead.
shell = pytest.importorskip("ngpc_shell")
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

REPO = Path(__file__).resolve().parent.parent
BIOS = REPO / "bios.bin"
ROM = REPO / "tests" / "roms" / "link_probe.ngc"


@pytest.fixture(scope="module")
def sh(app):
    """ONE Shell, reused by every test here.

    ⚠️ NOT tidiness: a Shell that is never disposed of stays subscribed to the settings
    object, and the UI suite deletes settings keys between its own tests -- so every
    leaked Shell re-applies its settings on every one of those. Eight of them turned a
    100-second suite into a SEVEN-MINUTE one, with the cost landing on somebody else's
    test (`test_both_windows_can_be_made_small`, 283 s), which is the worst place for
    it to show up. One shell, started and stopped per test.
    """
    w = shell.Shell()
    w._settings.setValue("paths/bios", str(BIOS))
    w.play._frames_due = lambda: 1
    yield w
    _wipe(w)


def _wipe(w) -> None:
    """Put the shared shell back the way the next test expects to find it."""
    if w._net_status is not None:
        w._net_status.stop(); w._net_status = None
    w._end_mirror_bringup()
    w._mirror_pending = None
    w.play.detach_mirror()
    w.play.stop()


@pytest.fixture(autouse=True)
def _between_tests(request):
    """One Shell for the module, but no state carried from one test to the next."""
    yield
    w = request.node.funcargs.get("sh")
    if w is not None:
        _wipe(w)



def _bring_up(sh, far_sock, far_image: bytes, cap: int = 4000, announce=None):
    """Complete the cartridge trade so the shell ends up with a live session.

    `Shell._begin_mirror` no longer hands one over on the spot: the two PCs trade
    cartridge images first (that is what lets them be DIFFERENT, and different is what
    two players' saves always are). The far end here is a plain CartExchange built from
    the shell's own fingerprint -- same PC, same settings, so it matches by
    construction rather than by a copy of the values that could drift.

    ⚠️ `far_image` is the LOADED cartridge image (`session._rom`), not the file on disk:
    a session pads the image up to the flash chip's capacity and applies the save, and
    the hello announces a hash of THAT. Sending the raw file is refused as a corrupt
    transfer -- the guard doing its job, and it cost one debugging round to see it.
    """
    from core.netplay import CartExchange, SocketPipe, _image_hash

    far_hs = sh._mirror_handshake(host=False)
    # A real far end announces ITS OWN cartridge -- that is what makes two different
    # ones possible. `announce` overrides it to fake a transfer that arrives different
    # from what was promised.
    far_hs.rom_hash = announce or _image_hash(far_image)
    far = CartExchange(SocketPipe(far_sock), far_image, far_hs)
    for _ in range(cap):
        if sh.play._mirror is not None or sh._mirror_boot is None:
            break
        sh._pump_mirror_bringup()
        far.pump()
    return far



class FakeMachine:
    """Records what it was told to do; enough for the session's own logic."""

    def __init__(self, tag: int = 0) -> None:
        self.pads: list[int] = []
        self.frames = 0
        self.tag = tag

    def write(self, addr: int, data: bytes) -> None:
        if addr == 0x00B0:
            self.pads.append(data[0])

    def run_frames(self, n: int = 1):
        self.frames += n

    def read(self, addr: int, n: int) -> bytes:
        return bytes([self.tag]) * n


class FakeLink:
    def __init__(self) -> None:
        self.pumps = 0

    def pump(self) -> None:
        self.pumps += 1


def _pair(delay: int = 3, delay_pumps: int = 0, *, tags=(1, 1), hs_kw=None):
    pa, pb = ListPipe.pair(delay_pumps)
    kw = dict(rom_hash="R", bios_hash="B", core_version="C", delay=delay)
    kw.update(hs_kw or {})
    a = MirrorSession(FakeMachine(tags[0]), FakeMachine(tags[0]), FakeLink(), pa,
                      Handshake(host=True, **kw))
    b = MirrorSession(FakeMachine(tags[1]), FakeMachine(tags[1]), FakeLink(), pb,
                      Handshake(host=False, **kw))
    return a, b


def test_both_sides_play_the_same_two_input_streams():
    """The whole model rests on this: each PC simulates BOTH consoles, so both PCs
    must feed them identical buttons or the two copies drift apart."""
    a, b = _pair(delay=2)
    for i in range(40):
        assert a.step(i) == "ran"
        assert b.step(0x80 | i) == "ran"
    # a's local console got a's buttons; a's mirror got b's -- and vice versa, with the
    # same delay on both, so the two PCs played the same pair of streams.
    assert a.local.pads == b.peer.pads
    assert a.peer.pads == b.local.pads
    # ...and the first `delay` frames are the agreed neutral padding, not player input.
    assert a.local.pads[:2] == [0, 0]
    assert a.local.pads[2:5] == [0, 1, 2]


def test_a_frame_whose_input_has_not_arrived_stalls_instead_of_guessing():
    """Delay-based netplay waits; it never invents a button. A guess would desync the
    two PCs silently, which is the one failure this whole design is built to avoid."""
    delay = 1
    a, b = _pair(delay=delay)
    for _ in range(3):
        a.step(0x10); b.step(0x20)
    b.pipe.peer = None                      # b's side of the wire goes quiet
    for _ in range(delay):                  # the inputs already in flight still play
        a.step(0x10)
    frames = a.local.frames
    outcomes = {a.step(0x10) for _ in range(5)}
    assert outcomes == {"waiting"}, "it made a button up rather than wait"
    assert a.local.frames == frames, "it ran a frame on an input it did not have"
    assert a.stalls >= 5                     # the ping, visible as a number


def test_a_slow_wire_costs_frames_of_delay_but_not_speed():
    """Hold every packet back for several pumps: the session still runs a frame per
    step once the pipeline is primed. That is the point of the mode -- latency is
    spent on input delay, not on the game's speed."""
    a, b = _pair(delay=6, delay_pumps=4)
    ran = 0
    for i in range(60):
        ra, rb = a.step(i & 0x3F), b.step(i & 0x3F)
        ran += (ra == "ran") + (rb == "ran")
    assert ran >= 100, f"a delayed wire cost speed: {ran}/120 frames ran"


def test_two_different_cartridges_are_allowed():
    """⚡ THE POINT OF TRADING THE IMAGES. Requiring the same cartridge meant requiring
    the same SAVE -- a save lives inside the image -- which two players almost never
    have. It also barred SNK-versus-Capcom in Card Fighters' Clash, which is the reason
    that game has a link at all."""
    pa, pb = ListPipe.pair()
    common = dict(bios_hash="B", core_version="C", delay=2)
    a = MirrorSession(FakeMachine(), FakeMachine(), FakeLink(), pa,
                      Handshake(rom_hash="ROM-A", host=True, **common))
    b = MirrorSession(FakeMachine(), FakeMachine(), FakeLink(), pb,
                      Handshake(rom_hash="ROM-B", **common))
    b.step(0)
    assert a.step(0) == "ran"
    assert a.rejected is None
    assert a.hs.peer_rom_hash == "ROM-B", "the peer's cartridge must still be announced"


@pytest.mark.parametrize("field,expected", [
    ("bios_hash", "bios"), ("core_version", "core_version")])
def test_a_different_bios_or_core_is_refused_too(field, expected):
    pa, pb = ListPipe.pair()
    common = dict(rom_hash="R", bios_hash="B", core_version="C", delay=2)
    other = dict(common); other[field] = "OTHER"
    a = MirrorSession(FakeMachine(), FakeMachine(), FakeLink(), pa,
                      Handshake(host=True, **common))
    b = MirrorSession(FakeMachine(), FakeMachine(), FakeLink(), pb, Handshake(**other))
    b.step(0)
    assert a.step(0) == "rejected"
    assert a.rejected == expected


def test_a_desync_is_noticed_and_named():
    """Detected, not repaired -- but never silent. Two sides whose consoles hold
    different bytes must not keep playing as if they agreed."""
    a, b = _pair(delay=1, tags=(1, 2))       # their consoles' memory differs on purpose
    for _ in range(netplay.CHECK_EVERY + 4):
        a.step(0); b.step(0)
    assert a.desync_at is not None
    assert b.desync_at is not None


def test_matching_consoles_never_report_a_desync():
    """The control group. Without it, a checksum that always mismatched would look
    exactly like a working detector."""
    a, b = _pair(delay=1, tags=(7, 7))
    for _ in range(netplay.CHECK_EVERY * 3):
        a.step(0); b.step(0)
    assert a.desync_at is None and b.desync_at is None


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_the_mirrors_own_cable_is_stepped_like_a_cable():
    """⚡ The mirror owns BOTH consoles, so it owns the same trap the local 2P cable had.

    It used to run `first.run_frames(1); pump; second.run_frames(1); pump` -- one
    console frozen for the whole of the other's frame, which is a frame of latency on
    every answer, in one direction. That is what mirror play exists to avoid: its cable
    is local and should carry NO latency at all. Card Fighters' Clash is the measure --
    through a mirror of its VS match, the whole-frame schedule dies at 118/102 bytes
    (its "LINK ERROR"), the interleaved one reaches 610/617 and plays.

    So: a frame of a real mirror must pump the cable many times, not twice.
    """
    from core import native
    from core.link import InProcessLink

    rom, bios = ROM.read_bytes(), BIOS.read_bytes()

    class Counting(InProcessLink):
        pumps = 0

        def pump(self):
            Counting.pumps += 1
            super().pump()

    local = native.NativeMachine(rom, bios=bios)
    peer = native.NativeMachine(rom, bios=bios)
    try:
        local.reset(bios_handoff=True)
        peer.reset(bios_handoff=True)
        Counting.pumps = 0
        pa, _pb = ListPipe.pair()
        FRAMES = 10
        # The input delay pre-fills that many frames of both streams, so this session
        # can run on its own without a peer answering.
        sess = MirrorSession(local, peer, Counting(local, peer), pa,
                             Handshake(rom_hash="r", bios_hash="b", core_version="c",
                                       delay=FRAMES + 2, host=True))
        for _ in range(FRAMES):
            assert sess.step(0) == "ran"
        real = Counting.pumps

        # CONTROL: the same session over machines with no sliced interface takes the
        # whole-frame path -- two pumps a frame. Without it, "many pumps" could just
        # mean "many frames".
        Counting.pumps = 0
        fake = MirrorSession(FakeMachine(), FakeMachine(), Counting(local, peer),
                             ListPipe.pair()[0],
                             Handshake(rom_hash="r", bios_hash="b", core_version="c",
                                       delay=FRAMES + 2, host=True))
        for _ in range(FRAMES):
            assert fake.step(0) == "ran"
        assert Counting.pumps == FRAMES * 2, "the fallback should pump twice a frame"
        assert real > FRAMES * 5, (
            f"a mirrored frame pumped the cable {real / FRAMES:.1f} times on average; "
            "a whole frame per console is the latency CFC's VS handshake cannot survive")
    finally:
        local.close(); peer.close()


# --------------------------------------------------------------------------- #
# The real thing: four consoles, two PCs' worth of session, one probe ROM.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
@pytest.mark.parametrize("wire_delay", [0, 6])
def test_two_mirrored_pcs_stay_identical_and_keep_full_speed(wire_delay):
    """Two PCs, each running BOTH consoles, exchanging only controller bytes.

    The probe ROM sends its own pad and records the other's, so "the cable works" is
    readable straight out of memory (0x400A = last byte received). What this pins:
      * every frame ran -- a six-pump wire delay costs input lag, not speed;
      * the two PCs' consoles are byte-identical, which is what makes the mirror sound.
    """
    from core import native
    from core.link import InProcessLink

    rom, bios = ROM.read_bytes(), BIOS.read_bytes()

    def one_pc(pipe, host):
        local = native.NativeMachine(rom, bios=bios)
        peer = native.NativeMachine(rom, bios=bios)
        local.reset(bios_handoff=True)
        peer.reset(bios_handoff=True)
        link = InProcessLink(local, peer)
        hs = Handshake(rom_hash="r", bios_hash="b", core_version="c",
                       delay=4, host=host)
        return MirrorSession(local, peer, link, pipe, hs)

    pa, pb = ListPipe.pair(wire_delay)
    pc1, pc2 = one_pc(pa, True), one_pc(pb, False)
    try:
        ran = 0
        for i in range(240):
            # player 1 holds RIGHT, player 2 holds UP -- distinct bits, so a console
            # that received the wrong stream is visible rather than plausible.
            ran += (pc1.step(0x08) == "ran") + (pc2.step(0x01) == "ran")
        assert ran == 480, f"the wire cost frames: {ran}/480 ran"

        # PC1's own console and PC2's mirror of it are the same console, twice.
        assert pc1.local.read(0x4000, 0x2C00) == pc2.peer.read(0x4000, 0x2C00)
        assert pc1.peer.read(0x4000, 0x2C00) == pc2.local.read(0x4000, 0x2C00)
        assert pc1.checksum() == pc2.checksum()
        assert pc1.desync_at is None and pc2.desync_at is None

        # and the emulated cable really carried the other player's buttons
        assert pc1.local.read(0x400A, 1)[0] == 0x01     # player 1 heard player 2's UP
        assert pc1.peer.read(0x400A, 1)[0] == 0x08      # player 2 heard player 1's RIGHT
    finally:
        for s in (pc1, pc2):
            s.local.close(); s.peer.close()


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_the_shell_plays_a_mirror_match_over_a_real_socket(sh, app):
    """The product path: a real Shell in mirror mode, over a real socket.

    ⚡ ONLY ONE Shell here, with the other PC standing in as a plain MirrorSession.
    Two Shells in one process reproduced the match fine on their own but took the
    whole suite down with qFatal (0xC0000409, no traceback) when they ran after
    everything else -- and a test that destabilises the suite is worse than the
    coverage it buys. One shell still exercises every line this mode added to the
    product: _begin_mirror, attach_mirror, _step_mirror and the frame loop.
    """
    import ngpc_settings as cfg
    from core import native
    from core.link import InProcessLink
    from core.native_session import NativeSession
    from core.netplay import Handshake, MirrorSession, SocketPipe

    s1, s2 = socket.socketpair()
    far_a = far_b = None
    try:
        sh.play.start(ROM)
        sh.play.held = 0x08                      # this player holds RIGHT
        sh._begin_mirror(SocketPipe(s1), host=True)
        _bring_up(sh, s2, sh.play.session._rom)
        assert sh.play.link_mode() == "mirror"

        # The other PC, built the way _begin_mirror builds its pair: same cartridge
        # image, same clock, cable in before either boots, player 1's console first.
        near = sh.play._mirror
        # ⚡ Built through NativeSession with the SAME arguments _begin_mirror uses,
        # and given the same wait-state tuning. Hand-rolled NativeMachines desynced
        # inside two frames -- which is the design's own warning, working.
        def far_console():
            return NativeSession(ROM, rom_bytes=sh.play.session._rom, autosave=False,
                                 bios_path=BIOS, save_to_rom=False, sidecar=False,
                                 flash_size=sh.play.session.machine.flash_capacity())
        far_a, far_b = far_console(), far_console()
        far_local, far_peer = far_a.machine, far_b.machine
        clock = native.RtcState(1, *shell.Shell.MIRROR_CLOCK)
        for m in (far_local, far_peer):
            if cfg.cart_wait_states(sh._settings):
                m.set_cart_wait(cfg.CART_FETCH_WAIT)
                m.set_cart_data_wait(cfg.CART_DATA_WAIT)
                m.set_ldir_cost(cfg.CART_LDIR_COST)
            m.set_rtc(clock)
            m.serial_set_enabled(True)
            m.reset(bios_handoff=True)
        far = MirrorSession(far_local, far_peer, InProcessLink(far_peer, far_local),
                            SocketPipe(s2),
                            Handshake(rom_hash=near.hs.rom_hash,
                                      bios_hash=near.hs.bios_hash,
                                      core_version=near.hs.core_version,
                                      delay=near.delay, host=False))

        for _ in range(400):
            sh.play._tick()
            far.step(0x01)                       # the other player holds UP

        assert near.rejected is None and far.rejected is None
        assert near.frames_run > 300, f"the shell barely ran: {near.frames_run}"
        assert near.desync_at is None and far.desync_at is None
        # each console heard the OTHER player's controller byte, on both PCs
        assert sh.play.machine.read(0x400A, 1)[0] == 0x01
        assert near.peer.read(0x400A, 1)[0] == 0x08
        assert far.local.read(0x400A, 1)[0] == 0x08
        # ...and the two PCs hold the same pair of consoles
        assert near.checksum() == far.checksum()
    finally:
        # ⚡ Timers before objects: a Shell left holding a running QTimer that fires
        # into a half-collected page takes the process with it (qFatal, no traceback).
        # Sockets first: on Windows a socketpair is two loopback TCP sockets, and one
        # left open is a ResourceWarning that outlives the test.
        for s in (s1, s2):
            try: s.close()
            except OSError: pass
        for sess in (far_a, far_b):
            if sess is not None:
                sess.close()


def test_two_different_input_delays_are_refused():
    """The delay decides WHICH FRAME an input is played on, so two different numbers
    are two different matches. Refused up front rather than discovered as drift."""
    pa, pb = ListPipe.pair()
    common = dict(rom_hash="R", bios_hash="B", core_version="C")
    a = MirrorSession(FakeMachine(), FakeMachine(), FakeLink(), pa,
                      Handshake(host=True, delay=3, **common))
    b = MirrorSession(FakeMachine(), FakeMachine(), FakeLink(), pb,
                      Handshake(delay=6, **common))
    b.step(0)
    assert a.step(0) == "rejected"
    assert a.rejected == "input_delay"


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_the_shell_ends_the_session_when_the_peer_goes(sh, app):
    """The shell must READ that flag, not just have it available: a lost peer ends the
    session instead of waiting for an input that can never arrive."""
    s1, s2 = socket.socketpair()
    ended = []
    try:
        sh.play.start(ROM)
        sh._begin_mirror(SocketPipe(s1), host=True)
        _bring_up(sh, s2, sh.play.session._rom)
        sh.play.mirror_ended.connect(ended.append)
        sh.play._mirror.pipe.lost = "peer closed the connection"
        sh.play._tick()
        assert ended, "the shell kept waiting for a console that had gone"
        assert sh.play._mirror is None, "the dead session was not torn down"
    finally:
        if sh._net_status is not None:
            sh._net_status.stop(); sh._net_status = None
        for s in (s1, s2):
            try: s.close()
            except OSError: pass
        sh.play.stop()


def test_the_session_notices_the_peer_going_away():
    """A peer that vanishes must not look like a slow one. Without this the session
    waits for an input that will never arrive, for ever, saying "waiting for the other
    player" -- the same unreadable failure the cable mode had to fix."""
    class DeadPipe:
        lost = "peer closed the connection"
        def send(self, data): pass
        def recv(self): return b""

    assert getattr(DeadPipe(), "lost", None), "the shell reads `lost` off the pipe"
    # and the real pipe raises that flag rather than an exception (see SocketPipe):
    s1, s2 = socket.socketpair()
    pipe = netplay.SocketPipe(s1)
    try:
        s2.close()
        for _ in range(20):                  # a few frames' worth of pumping
            pipe.send(b"x")
            pipe.recv()
            if pipe.lost:
                break
        assert pipe.lost, "a departed peer was never noticed"
    finally:
        for s in (s1, s2):
            try: s.close()
            except OSError: pass


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_stopping_the_game_lets_go_of_the_mirror(sh, app):
    """The mirror owns a SECOND console and a socket. Left attached they outlive the
    game that opened them, and the next `start()` would step a session whose local
    machine has been closed -- so `stop()` must let go of all of it."""
    s1, _s2 = socket.socketpair()
    try:
        sh.play.start(ROM)
        sh._begin_mirror(SocketPipe(s1), host=True)
        _bring_up(sh, _s2, sh.play.session._rom)
        peer = sh.play._mirror_peer_session
        assert peer is not None and sh.play.link_mode() == "mirror"

        sh.play.stop()
        assert sh.play._mirror is None, "the session outlived the game"
        assert sh.play._mirror_peer_session is None, "the second console was left open"

        # ...and a NEW session is not born condemned by the previous one's verdict: a
        # stale `_mirror_desynced` would end it on its first frame, for somebody else's
        # desync.
        sh.play._mirror_desynced = True
        sh.play._mirror_waits = 99
        sh.play.start(ROM)
        s3, s4 = socket.socketpair()
        try:
            sh._begin_mirror(SocketPipe(s3), host=True)
            _bring_up(sh, s4, sh.play.session._rom)
            assert sh.play._mirror_desynced is False, "it inherited the last verdict"
            assert sh.play._mirror_waits == 0
        finally:
            for s in (s3, s4):
                try: s.close()
                except OSError: pass
    finally:
        if sh._net_status is not None:
            sh._net_status.stop(); sh._net_status = None
        for s in (s1, _s2):
            try: s.close()
            except OSError: pass
        sh.play.stop()


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_mirror_play_refuses_what_would_rewind_this_console_only(sh, app):
    """A savestate, a rewind or a reset applied HERE and nowhere else puts the two PCs
    in different states. The checksum would notice a second later and end the match --
    a rotten way to answer a keypress. So the keypress is refused."""
    s1, s2 = socket.socketpair()
    try:
        sh.play.start(ROM)
        for _ in range(20):
            sh.play._tick()
        sh._begin_mirror(SocketPipe(s1), host=True)
        _bring_up(sh, s2, sh.play.session._rom)
        before = sh.play.machine.read(0x4000, 0x1000)

        sh.play.load_state(0)                    # would restore only this console
        sh.play.start_rewind()                   # ...and so would this
        sh.play._do_reset()                      # ...and this reboots only this one
        assert sh.play._rewinding is False, "rewind started inside a mirror match"
        assert sh.play.machine.read(0x4000, 0x1000) == before, "the console was rewound"
        assert sh.play._mirror is not None, "the session was torn down instead of the key"

        # save_state is refused too: a file the player could load back mid-match is the
        # same trap one step later.
        path = sh.play._state_path(0)
        stamp = path.stat().st_mtime_ns if path is not None and path.is_file() else None
        sh.play.save_state(0)
        if path is not None and path.is_file():
            assert path.stat().st_mtime_ns == stamp, "a state was written mid-match"
    finally:
        if sh._net_status is not None:
            sh._net_status.stop(); sh._net_status = None
        for s in (s1, s2):
            try: s.close()
            except OSError: pass
        sh.play.stop()


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_a_lobby_room_starts_the_link_it_advertised(sh, app):
    """⚡ MIRROR PLAY THROUGH THE LOBBY -- the mode the players can actually reach.

    Mirror play used to be direct-address only (an IP, a port, and whatever it takes
    to be reachable), while the lobby -- rooms, pairing, NAT traversal -- only ever
    opened the cable. That is backwards for Card Fighters' Clash, which is the game
    that NEEDS the mirror: its VS handshake gives up on latency the cable cannot avoid.

    The relay carries opaque bytes, so the same room works for either; what the joiner
    must not do is guess. So a room that says "mirror" starts the cartridge trade, and
    one that says nothing is a cable room -- which is what every room made by an older
    client says.
    """
    from core.lobby import LobbyPipe

    def client():
        # Never started: no thread, no socket. What is exercised here is the routing
        # and the pipe, and both only need the object's signals and queues.
        return lobby.LobbyClient("127.0.0.1", 1, "tester")

    sh.play.start(ROM)
    c = client()
    try:
        sh._on_lobby_linked(c, {"mode": "mirror", "role": "host", "delay": 5})
        assert sh._mirror_boot is not None, "a mirror room did not start the trade"
        assert sh.play._net_link is None, "it opened the cable as well"
        assert sh._mirror_boot.hs.delay == 5, "the room's input delay was ignored"
        assert sh._mirror_boot.hs.host is True
        sh._end_mirror_bringup()
    finally:
        c.close()

    # CONTROL: a room with no mode is the cable, and takes the other path entirely.
    c2 = client()
    try:
        sh._on_lobby_linked(c2, {"role": "guest"})
        assert sh._mirror_boot is None, "a cable room started a mirror"
        assert sh.play._net_link is not None, "a cable room did not open the cable"
        assert sh.play.link_mode() == "lobby"
    finally:
        sh._end_net_link()
        c2.close()

    # ...and the pipe reports a peer that leaves. The lobby loses one through a Qt
    # signal, not a socket error, so a session never told sits at "waiting" for ever.
    c3 = client()
    try:
        pipe = LobbyPipe(c3)
        assert pipe.lost is None
        c3.peer_left.emit()
        assert pipe.lost, "a peer leaving the room went unnoticed"
        assert pipe.recv() == b"", "a dead pipe still handed bytes over"
    finally:
        c3.close()


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_the_two_online_modes_are_exclusive(sh, app):
    """They drive the same console two different ways -- the cable relays its serial
    FIFO, the mirror owns the pad byte and runs a second console here. Both at once is
    two relays over one FIFO, so the second one asked for is refused."""
    s1, s2 = socket.socketpair()
    try:
        sh.play.start(ROM)
        sh._begin_mirror(SocketPipe(s1), host=True)
        _bring_up(sh, s2, sh.play.session._rom)

        assert sh._one_link_at_a_time(False) is True, "a cable was allowed over a mirror"
        assert sh._one_link_at_a_time(True) is False, "a mirror blocked itself"

        sh.play.detach_mirror()
        sh.play._link_peer = sh.play            # pretend a local cable is attached
        assert sh._one_link_at_a_time(True) is True, "a mirror was allowed over a cable"
        sh.play._link_peer = None
    finally:
        if sh._net_status is not None:
            sh._net_status.stop(); sh._net_status = None
        for s in (s1, s2):
            try: s.close()
            except OSError: pass
        sh.play.stop()


def test_a_failed_mirror_attempt_does_not_hijack_the_next_cable_link(sh, app):
    """⛔ `_mirror_pending` left set by a mirror attempt that timed out made the NEXT
    ordinary Host/Join quietly start a mirror session instead of a cable."""
    try:
        sh._mirror_pending = "host"
        sh._on_net_failed("timed out")
        assert sh._mirror_pending is None
    finally:
        sh.play.stop()


def test_a_long_session_does_not_hoard_state():
    """Unbounded growth is a bug that only shows up in the one session nobody tests:
    a long one. Inputs are dropped once played; a checksum whose partner never arrives
    is forgotten after a few rounds rather than kept for the whole match."""
    a, b = _pair(delay=2)
    for _ in range(netplay.CHECK_EVERY * 12):        # ~12 checksum rounds
        a.step(0x10); b.step(0x20)
    assert len(a.local_inputs) <= 4, f"played inputs were kept: {len(a.local_inputs)}"
    assert len(a.peer_inputs) <= 4
    assert len(a._mine) <= 8 and len(a._checks) <= 8

    # ...and a checksum whose partner NEVER arrives (a peer that stopped sending them)
    # must be forgotten rather than kept for the length of the match.
    # ⚠️ Not simulated by cutting the wire: with no peer input the session answers
    # "waiting" and returns BEFORE the checksum code, so that version of this test
    # exercised nothing -- it passed with the trim removed. Stuff the book instead.
    a._mine.update({100000 + i: i for i in range(50)})
    a.step(0x10)
    assert len(a._mine) <= 8, f"unanswered checksums piled up: {len(a._mine)}"


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_the_debugger_tap_reaches_the_mirror_cable(sh, app):
    """In mirror play the cable is the in-process one between the two local consoles,
    pumped by the session rather than by _pump_link. A Link tab reading zero bytes on a
    busy cable is worse than no tab at all."""
    from core.link_debug import LinkMonitor, TX

    s1, s2 = socket.socketpair()
    try:
        sh.play.start(ROM)
        sh._begin_mirror(SocketPipe(s1), host=True)
        _bring_up(sh, s2, sh.play.session._rom)
        mon = LinkMonitor()
        sh.play.set_link_monitor(mon)
        assert sh.play._mirror.link.monitor_a is mon, "the tap never reached the cable"

        far = sh.play._mirror
        for f in range(200):                     # feed it the peer's inputs by hand
            far.peer_inputs[f] = 0x01
            sh.play._tick()
        assert mon.bytes_tx > 0, "the tap saw nothing on a cable that carried bytes"
        assert mon.raw(TX), "no transmit log"
    finally:
        if sh._net_status is not None:
            sh._net_status.stop(); sh._net_status = None
        for s in (s1, s2):
            try: s.close()
            except OSError: pass
        sh.play.stop()


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_the_two_players_may_hold_DIFFERENT_cartridges(sh, app):
    """⚡ THE REASON THE IMAGES ARE TRADED AT ALL.

    Building the mirror from the LOCAL cartridge forced both players to hold the same
    image -- and a save lives inside the image, so it forced the same SAVE, which two
    players essentially never have. It also barred SNK-versus-Capcom in Card Fighters'
    Clash, and that pairing is the reason the game has a link.

    So the peer's console must be built from the bytes the PEER sent, not from ours.
    The far cartridge here differs in a byte the probe ROM never reads, which is enough
    to tell the two images apart while leaving the link behaviour intact.
    """
    s1, s2 = socket.socketpair()
    try:
        sh.play.start(ROM)

        mine = sh.play.session._rom
        theirs = bytearray(mine)
        theirs[0x1F0] ^= 0xFF               # a byte in the header padding, never read
        theirs = bytes(theirs)
        assert theirs != mine

        sh._begin_mirror(SocketPipe(s1), host=True)
        _bring_up(sh, s2, theirs)
        assert sh.play._mirror is not None, "a different cartridge was refused"

        # the mirror console really holds THEIR cartridge, not a copy of ours
        peer_cart = sh.play._mirror.peer.read(0x200000, len(theirs))
        assert peer_cart[0x1F0] == theirs[0x1F0]
        assert peer_cart[0x1F0] != mine[0x1F0], "the peer console got OUR cartridge"
        assert sh.play.machine.read(0x200000, 0x200)[0x1F0] == mine[0x1F0]

        for f in range(120):                 # and the match still runs
            sh.play._mirror.peer_inputs[f] = 0x01
            sh.play._tick()
        assert sh.play._mirror.frames_run > 100
        assert sh.play._mirror.desync_at is None
    finally:
        if sh._net_status is not None:
            sh._net_status.stop(); sh._net_status = None
        for s in (s1, s2):
            try: s.close()
            except OSError: pass
        sh.play.detach_mirror()
        sh.play.stop()


@pytest.mark.skipif(not (BIOS.exists() and ROM.exists()),
                    reason="needs the retail bios.bin (gitignored) and the probe ROM")
def test_a_cartridge_that_arrives_different_from_its_announcement_is_refused(sh, app):
    """A truncated or corrupted transfer would otherwise show up as a desync twenty
    seconds into the match, which is the hardest possible way to be told about it."""
    s1, s2 = socket.socketpair()
    try:
        sh.play.start(ROM)
        sh._begin_mirror(SocketPipe(s1), host=True)
        # The far end promises one cartridge and sends another.
        _bring_up(sh, s2, sh.play.session._rom, announce="0123456789abcdef")
        assert sh.play._mirror is None, "a cartridge that did not match was accepted"
    finally:
        if sh._net_status is not None:
            sh._net_status.stop(); sh._net_status = None
        for s in (s1, s2):
            try: s.close()
            except OSError: pass
        sh.play.stop()
