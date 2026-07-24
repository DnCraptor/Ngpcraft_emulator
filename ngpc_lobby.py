"""The online lobby dialog: pseudo, browse open games, create / join.

Sits on top of core.lobby.LobbyClient. When a pairing succeeds it emits `linked`
with the connected client, and the shell wires it to the running console (its
serial bytes then relay through the server). The player must be running a
compatible game -- the list shows each game's title for that reason.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QCheckBox, QInputDialog,
    QMessageBox, QApplication, QTextEdit,
)

from core.lobby import LobbyClient

DEFAULT_PORT = 7788


def _lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _public_ip(timeout: float = 4.0) -> str:
    """Best-effort public IP (what a peer over the internet dials), via a couple
    of tiny plain-text services. Empty string if none answer."""
    import urllib.request
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip",
                "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode("utf-8", "ignore").strip()
                if ip and len(ip) < 64 and ip.count(".") == 3:
                    return ip
        except Exception:
            continue
    return ""


class _PublicIPProbe(QThread):
    got = pyqtSignal(str)

    def run(self) -> None:
        self.got.emit(_public_ip())


class HostInfoDialog(QDialog):
    """Shown when you host a DIRECT game: your ready-to-paste Discord line (with
    your public IP auto-detected) plus an honest explainer of port-forwarding and
    its risks. Non-modal -- hosting keeps running behind it."""

    def __init__(self, game_name: str, port: int, fr: bool, parent=None):
        super().__init__(parent)
        self._game = game_name or "?"
        self._port = int(port)
        self._fr = fr
        self._pub = ""
        self.setWindowTitle("Héberger — infos de connexion" if fr
                            else "Host — connection info")
        self.setMinimumWidth(560)

        v = QVBoxLayout(self)
        lan = _lan_ip()

        # Prominent, unmissable framing: this mode is not for posting to strangers.
        warn = QLabel(
            ("⚠️ <b>Mode avancé.</b> À réserver aux personnes à l'aise avec la sécurité "
             "informatique, ou à partager <b>uniquement avec des gens de confiance que tu "
             "connais</b> — pas dans une liste publique d'inconnus. Détails plus bas."
             if fr else
             "⚠️ <b>Advanced mode.</b> Best for people comfortable with IT security, or "
             "shared <b>only with trusted people you know</b> — not posted to a public list "
             "of strangers. Details below."))
        warn.setTextFormat(Qt.TextFormat.RichText); warn.setWordWrap(True)
        warn.setStyleSheet("background:#5a1f1f; color:#ffd9d9; padding:8px; border-radius:6px;")
        v.addWidget(warn)

        head = (f"Tu héberges sur le port <b>{port}</b>."
                if fr else f"You are hosting on port <b>{port}</b>.")
        h = QLabel(head); h.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(h)
        v.addWidget(QLabel(
            (f"• Même réseau (LAN) : ton ami tape <b>{lan}:{port}</b>"
             if fr else f"• Same network (LAN): your friend dials <b>{lan}:{port}</b>")))
        self._pub_lbl = QLabel(
            "• Internet : détection de ton IP publique…" if fr
            else "• Internet: detecting your public IP…")
        self._pub_lbl.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self._pub_lbl)

        v.addWidget(QLabel("À partager (Discord, message, où tu veux) :" if fr
                           else "Share this (Discord, chat, anywhere):"))
        self._paste = QLineEdit(); self._paste.setReadOnly(True)
        self._paste.setText(self._line(lan))       # LAN line until public IP arrives
        v.addWidget(self._paste)
        copy = QPushButton("📋 Copier mes infos" if fr else "📋 Copy my info")
        copy.clicked.connect(self._copy)
        v.addWidget(copy)

        risks = QTextEdit(); risks.setReadOnly(True); risks.setMinimumHeight(230)
        risks.setHtml(self._help_html())
        v.addWidget(risks)

        close = QPushButton("Fermer" if fr else "Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)

        self._probe = _PublicIPProbe(self)
        self._probe.got.connect(self._on_public_ip)
        self._probe.start()

    def _line(self, ip: str) -> str:
        tail = "venez jouer !" if self._fr else "come join!"
        return f"🎮 {self._game} — {ip}:{self._port} — {tail}"

    def _on_public_ip(self, ip: str) -> None:
        self._pub = ip
        if ip:
            self._pub_lbl.setText(
                (f"• Internet : ton ami tape <b>{ip}:{self._port}</b> "
                 "(nécessite l'ouverture de port ci-dessous)"
                 if self._fr else
                 f"• Internet: your friend dials <b>{ip}:{self._port}</b> "
                 "(needs the port-forward below)"))
            self._paste.setText(self._line(ip))
        else:
            self._pub_lbl.setText(
                "• Internet : IP publique introuvable (hors ligne ?)" if self._fr
                else "• Internet: could not detect a public IP (offline?)")

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._paste.text())

    def _help_html(self) -> str:
        if self._fr:
            return (
                "<b>Comment ça marche</b> — pour jouer à travers internet, ta box doit "
                "laisser passer les connexions entrantes vers ton PC. Dans son interface "
                f"(souvent 192.168.1.1), section <i>Redirection de port / NAT</i>, redirige "
                f"le port <b>{self._port}</b> (TCP) vers l'IP locale de ton PC "
                f"(<b>{_lan_ip()}</b>).<br><br>"
                "<b>⚠️ Les risques, sans langue de bois — il y a DEUX niveaux :</b><br><br>"
                "<b>1) Le port lui-même — risque faible.</b> Il n'expose que <b>ce "
                "programme</b> (l'émulateur), pas tout ton PC : pas de login, pas d'accès à "
                "tes fichiers, pas d'exécution de commandes, le jeu tourne en bac à sable. "
                "Pire cas réaliste : quelqu'un fait planter ta partie.<br><br>"
                "<b>2) Publier ton IP — LE vrai risque.</b> Ton IP publique, c'est ton "
                "<b>adresse maison</b> en ligne. Qui l'a peut <b>scanner TOUS tes ports</b> "
                "(pas juste celui-ci) pour trouver d'autres failles chez toi (box, NAS, "
                "caméra…), et peut te <b>DDoS / couper ta connexion</b> (griefing).<br><br>"
                "<b>➡ La règle :</b> partager avec un <b>ami de confiance</b> (en privé) = "
                "OK. <b>Poster à des inconnus</b> = à éviter — tu diffuses ton adresse.<br>"
                "<b>➡ Bonnes pratiques :</b> n'ouvre le port que pendant que tu joues et "
                "<b>referme la redirection après</b>.<br>"
                "<b>➡ Pour des inconnus / si tu n'es pas à l'aise :</b> <b>Tailscale</b>, "
                "<b>playit.gg</b> ou le <b>salon en ligne</b> te font rejoindre "
                "<b>sans exposer ton IP ni ouvrir de port</b>.")
        return (
            "<b>How it works</b> — to play over the internet, your router must allow "
            "incoming connections to your PC. In its admin page (often 192.168.1.1), under "
            f"<i>Port forwarding / NAT</i>, forward port <b>{self._port}</b> (TCP) to your "
            f"PC's local IP (<b>{_lan_ip()}</b>).<br><br>"
            "<b>⚠️ The risks, straight — there are TWO levels:</b><br><br>"
            "<b>1) The port itself — low risk.</b> It only exposes <b>this program</b> (the "
            "emulator), not your whole PC: no login, no file access, no command execution, "
            "the game runs sandboxed. Realistic worst case: someone crashes your session.<br><br>"
            "<b>2) Publishing your IP — the real risk.</b> Your public IP is your online "
            "<b>home address</b>. Whoever has it can <b>scan ALL your ports</b> (not just "
            "this one) for other weak spots (router, NAS, camera…), and can <b>DDoS / knock "
            "you offline</b> (griefing).<br><br>"
            "<b>➡ The rule:</b> sharing with a <b>trusted friend</b> (privately) = fine. "
            "<b>Posting to strangers</b> = avoid — you're broadcasting your address.<br>"
            "<b>➡ Good practice:</b> only forward while playing and <b>remove the forward "
            "after</b>.<br>"
            "<b>➡ For strangers / if unsure:</b> <b>Tailscale</b>, <b>playit.gg</b> or the "
            "<b>online lobby</b> let people reach you <b>without exposing your IP or opening "
            "a port</b>.")


class LobbyDialog(QDialog):
    linked = pyqtSignal(object)        # the connected LobbyClient, once paired

    def __init__(self, settings, game_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Online link — lobby")
        self.setMinimumSize(460, 420)
        self._settings = settings
        self._game_name = game_name or "?"
        self._client: LobbyClient | None = None
        self._hosting = False

        v = QVBoxLayout(self)

        # --- connection row -------------------------------------------------
        form = QFormLayout()
        self._pseudo = QLineEdit(str(settings.value("online/pseudo", "", type=str)))
        self._pseudo.setPlaceholderText("your nickname")
        self._server = QLineEdit(str(settings.value("online/server", "", type=str)))
        self._server.setPlaceholderText("server address, e.g. myserver.fly.dev:7788")
        form.addRow("Nickname", self._pseudo)
        form.addRow("Server", self._server)
        v.addLayout(form)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._toggle_connect)
        v.addWidget(self._connect_btn)

        self._status = QLabel("Not connected.")
        self._status.setWordWrap(True)
        v.addWidget(self._status)

        v.addWidget(QLabel("Open games (running: %s)" % self._game_name))
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _i: self._join())
        v.addWidget(self._list, 1)

        row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(lambda: self._client and self._client.refresh())
        self._create_btn = QPushButton("Create game…")
        self._create_btn.clicked.connect(self._create)
        self._join_btn = QPushButton("Join")
        self._join_btn.clicked.connect(self._join)
        for b in (self._refresh_btn, self._create_btn, self._join_btn):
            b.setEnabled(False)
            row.addWidget(b)
        v.addLayout(row)

        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        v.addWidget(close)

    # ---- connection --------------------------------------------------------
    def _toggle_connect(self) -> None:
        if self._client is not None:
            self._teardown_client()
            self._status.setText("Not connected.")
            self._connect_btn.setText("Connect")
            self._enable_actions(False)
            return
        pseudo = self._pseudo.text().strip() or "Player"
        addr = self._server.text().strip()
        if not addr:
            QMessageBox.warning(self, "Server", "Enter the server address.")
            return
        host, _, p = addr.partition(":")
        port = int(p) if p.strip().isdigit() else DEFAULT_PORT
        self._settings.setValue("online/pseudo", pseudo)
        self._settings.setValue("online/server", addr)

        self._client = LobbyClient(host.strip(), port, pseudo)
        self._client.connected.connect(self._on_connected)
        self._client.disconnected.connect(self._on_disconnected)
        self._client.game_list.connect(self._on_game_list)
        self._client.created.connect(self._on_created)
        self._client.joined.connect(self._on_joined)
        self._client.error.connect(self._on_error)
        self._status.setText("Connecting…")
        self._connect_btn.setText("Disconnect")
        self._client.start()

    def _teardown_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._hosting = False

    def _enable_actions(self, on: bool) -> None:
        for b in (self._refresh_btn, self._create_btn, self._join_btn):
            b.setEnabled(on)

    # ---- client signals ----------------------------------------------------
    def _on_connected(self) -> None:
        self._status.setText("Connected. Create a game or join one.")
        self._enable_actions(True)
        self._client.refresh()

    def _on_disconnected(self, reason: str) -> None:
        self._status.setText(f"Disconnected: {reason}")
        self._connect_btn.setText("Connect")
        self._enable_actions(False)
        self._client = None

    def _on_game_list(self, games: list) -> None:
        self._list.clear()
        for g in games:
            lock = " 🔒" if g.get("private") else ""
            item = QListWidgetItem(
                f"{g.get('name','?')}{lock}   —   {g.get('game','?')}   "
                f"(by {g.get('creator','?')})")
            item.setData(Qt.ItemDataRole.UserRole, g)
            self._list.addItem(item)
        if not games:
            self._list.addItem("(no open games — create one)")

    def _on_created(self, room: str) -> None:
        self._hosting = True
        self._status.setText(
            f"Hosting room {room} — waiting for a player to join…")

    def _on_joined(self, info: dict) -> None:
        # paired: hand the live client to the shell and close
        self.linked.emit(self._client)
        self._client = None          # ownership moves to the shell
        self.accept()

    def _on_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Lobby", msg)

    # ---- actions -----------------------------------------------------------
    def _create(self) -> None:
        if self._client is None:
            return
        name, ok = QInputDialog.getText(self, "Create game", "Server name:")
        if not ok or not name.strip():
            return
        private = QMessageBox.question(
            self, "Private game?",
            "Make this a PRIVATE game (password required)?") == QMessageBox.StandardButton.Yes
        password = ""
        if private:
            password, ok = QInputDialog.getText(
                self, "Password", "Set a password:", QLineEdit.EchoMode.Password)
            if not ok:
                return
        self._client.create(name.strip(), self._game_name,
                            public=not private, password=password)

    def _join(self) -> None:
        if self._client is None:
            return
        item = self._list.currentItem()
        g = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(g, dict):
            return
        password = ""
        if g.get("private"):
            password, ok = QInputDialog.getText(
                self, "Password", "Password:", QLineEdit.EchoMode.Password)
            if not ok:
                return
        self._client.join(g["room"], password)

    def reject(self) -> None:
        self._teardown_client()
        super().reject()
