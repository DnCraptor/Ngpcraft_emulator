"""Two-player LINK CABLE play on one PC -- two windows, one keyboard.

This is the GUI over the emulated link cable (core/link.py). Two independent
NGPC consoles run side by side in their own windows and are wired together by an
InProcessLink, exactly as two real consoles joined by a cable: each runs its own
copy of the game, and only the serial bytes cross between them.

    python ngpc_link_play.py <rom> [--bios bios.bin]

Controls (both players on one keyboard, tracked globally so focus does not
matter):

    Player 1 : Arrow keys = D-pad, K = A, L = B, Enter = OPTION
    Player 2 : W A S D     = D-pad, F = A, G = B, Tab   = OPTION

Player 1's audio is played (the two games sound near-identical). This is the
first playable version: video + input + link + P1 audio. It deliberately reuses
the shared core rather than the full debugger PlayPage, so it stays small.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QObject, QEvent
from PyQt6.QtGui import QImage, QPixmap, QKeyEvent
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout

from core.native_session import NativeSession
from core.link import InProcessLink
from core import native
import ngpc_input as ipt

SCREEN_W, SCREEN_H = 160, 152
JOYPAD_PORT = 0x00B0

# key -> button bit, per player. Chosen so the two clusters never overlap.
P1_KEYS = {
    Qt.Key.Key_Up: ipt.UP, Qt.Key.Key_Down: ipt.DOWN,
    Qt.Key.Key_Left: ipt.LEFT, Qt.Key.Key_Right: ipt.RIGHT,
    Qt.Key.Key_K: ipt.A, Qt.Key.Key_L: ipt.B, Qt.Key.Key_Return: ipt.OPTION,
    Qt.Key.Key_Enter: ipt.OPTION,
}
P2_KEYS = {
    Qt.Key.Key_W: ipt.UP, Qt.Key.Key_S: ipt.DOWN,
    Qt.Key.Key_A: ipt.LEFT, Qt.Key.Key_D: ipt.RIGHT,
    Qt.Key.Key_F: ipt.A, Qt.Key.Key_G: ipt.B, Qt.Key.Key_Tab: ipt.OPTION,
}


def frame_to_qimage(fb) -> QImage:
    """A frame (SCREEN_W*SCREEN_H of 12-bit 0x0RGB) -> an RGB32 QImage.

    Same expansion the shell uses: each 4-bit channel scaled by 17 to fill 0..255.
    """
    buf = bytearray(SCREEN_W * SCREEN_H * 4)
    i = 0
    for c in fb:
        buf[i] = ((c >> 8) & 0x0F) * 17      # B
        buf[i + 1] = ((c >> 4) & 0x0F) * 17  # G
        buf[i + 2] = (c & 0x0F) * 17         # R
        buf[i + 3] = 0xFF
        i += 4
    return QImage(bytes(buf), SCREEN_W, SCREEN_H,
                  QImage.Format.Format_RGB32).copy()


class PlayerWindow(QMainWindow):
    """One console's screen. Rendering only -- input is global (see LinkPlay)."""

    def __init__(self, title: str, scale: int = 3):
        super().__init__()
        self.setWindowTitle(title)
        self._scale = scale
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        self.setCentralWidget(central)
        self.setFixedSize(SCREEN_W * scale, SCREEN_H * scale)

    def show_frame(self, img: QImage) -> None:
        self._label.setPixmap(
            QPixmap.fromImage(img).scaled(SCREEN_W * self._scale,
                                          SCREEN_H * self._scale))


class LinkPlay(QObject):
    """Coordinator: two sessions + link, driven from ONE timer.

    Each tick injects both players' input, advances both consoles one frame,
    pumps the link (relaying the serial bytes), then repaints both windows. The
    core is single-threaded, so stepping A then B on the Qt thread is the safe
    pattern.
    """

    def __init__(self, rom: Path, bios: Path | None):
        super().__init__()
        # Player 1 owns the save; player 2 is a guest (no autosave -> no file fight).
        self.a = NativeSession(rom, bios_path=bios, autosave=False)
        self.b = NativeSession(rom, bios_path=bios, autosave=False)
        self.link = InProcessLink(self.a.machine, self.b.machine)

        self.win_a = PlayerWindow("NGPC Link — Player 1")
        self.win_b = PlayerWindow("NGPC Link — Player 2")
        self.win_a.move(80, 120)
        self.win_b.move(80 + SCREEN_W * 3 + 40, 120)

        self._held = set()          # currently-pressed Qt keys (global)

        # Player 1 audio (best effort; silent if the platform has no sink).
        self._pending = bytearray()
        self._sink = None
        self._audio = None
        self._init_audio()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ---- audio (player 1 only) --------------------------------------------
    def _init_audio(self) -> None:
        try:
            from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
            fmt = QAudioFormat()
            fmt.setSampleRate(native.NativeMachine.AUDIO_RATE_HZ)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            dev = QMediaDevices.defaultAudioOutput()
            if dev is None or dev.isNull():
                return
            self._sink = QAudioSink(dev, fmt, self)
            self._sink.setBufferSize(
                int(0.10 * native.NativeMachine.AUDIO_RATE_HZ) * 4)
            self._audio = self._sink.start()
        except Exception:
            self._sink = self._audio = None

    def _drain_audio(self) -> None:
        if self._audio is None or self._sink is None or not self._pending:
            return
        free = self._sink.bytesFree()
        take = min(free, len(self._pending))
        take -= take % 4
        if take > 0:
            self._audio.write(bytes(self._pending[:take]))
            del self._pending[:take]

    # ---- input (global: whichever window has focus, both players work) ----
    def _joypad(self, keys: dict) -> int:
        v = 0
        for k, bit in keys.items():
            if k in self._held:
                v |= bit
        return v & 0x7F

    def eventFilter(self, obj, e):  # noqa: N802
        t = e.type()
        if t == QEvent.Type.KeyPress and isinstance(e, QKeyEvent):
            if not e.isAutoRepeat():
                self._held.add(Qt.Key(e.key()))
            return False
        if t == QEvent.Type.KeyRelease and isinstance(e, QKeyEvent):
            if not e.isAutoRepeat():
                self._held.discard(Qt.Key(e.key()))
            return False
        return False

    # ---- run --------------------------------------------------------------
    def start(self) -> None:
        self.win_a.show()
        self.win_b.show()
        self._timer.start(16)       # ~60 Hz

    def _tick(self) -> None:
        # inject each player's input, advance both consoles one frame
        self.a.machine.write(JOYPAD_PORT, bytes([self._joypad(P1_KEYS)]))
        self.b.machine.write(JOYPAD_PORT, bytes([self._joypad(P2_KEYS)]))
        self.a.run_frames(1)
        self.b.run_frames(1)

        # relay the cable
        self.link.pump()

        # repaint both screens
        self.win_a.show_frame(frame_to_qimage(self.a.machine.framebuffer()))
        self.win_b.show_frame(frame_to_qimage(self.b.machine.framebuffer()))

        # player 1 audio
        if self._sink is not None:
            self._pending += self.a.machine.audio()
            self._drain_audio()

    def close(self) -> None:
        self._timer.stop()
        if self._sink is not None:
            self._sink.stop()
        self.link.disconnect()
        self.a.close()
        self.b.close()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    rom = Path(argv[1])
    bios = None
    if "--bios" in argv:
        bios = Path(argv[argv.index("--bios") + 1])
    else:
        default_bios = Path(__file__).resolve().parent / "bios.bin"
        if default_bios.exists():
            bios = default_bios

    app = QApplication(argv)
    play = LinkPlay(rom, bios)
    app.installEventFilter(play)         # global key capture for both players
    play.start()
    rc = app.exec()
    play.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
