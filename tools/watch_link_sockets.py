"""EST-CE QUE L'EMULATEUR ECOUTE VRAIMENT ? -- la question que l'ecran ne repond pas.

Un joueur voit « en attente de connexion..... » et en conclut « ca ne se connecte pas ».
Mais ce texte est pose par `_start_net` AVANT que le fil de fond ait bind quoi que ce
soit, et il n'est jamais repris si l'attente EXPIRE (HOST_TIMEOUT_S = 300 s). Les deux
etats -- « j'ecoute, personne ne vient » et « je n'ecoute plus depuis dix minutes » --
s'affichent donc pareil.

Cette sonde regarde les VRAIES sockets des processus NgpCraftEmulator, deux fois par
seconde, et journalise chaque apparition/disparition d'ecoute et chaque connexion
etablie. C'est la seule facon de savoir laquelle des deux on regarde.

Usage:  python tools/watch_link_sockets.py [--seconds 300]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PS = ("Get-NetTCPConnection -ErrorAction SilentlyContinue | "
      "Where-Object { $_.OwningProcess -in ("
      "  (Get-Process -Name NgpCraftEmulator -ErrorAction SilentlyContinue).Id) } | "
      "ForEach-Object { \"$($_.State)|$($_.LocalAddress):$($_.LocalPort)|"
      "$($_.RemoteAddress):$($_.RemotePort)|$($_.OwningProcess)\" }")


def snapshot() -> set[str]:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", PS],
                             capture_output=True, text=True, timeout=10)
    except Exception:                                    # noqa: BLE001
        return set()
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--log", type=Path,
                    default=Path(__file__).resolve().parent.parent
                    / "watches" / "link_sockets.log")
    args = ap.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# sonde sockets NgpCraftEmulator — {time.strftime('%H:%M:%S')}"]
    seen: set[str] = set()
    end = time.time() + args.seconds
    while time.time() < end:
        now = snapshot()
        for gone in sorted(seen - now):
            lines.append(f"{time.strftime('%H:%M:%S')}  DISPARU   {gone}")
        for new in sorted(now - seen):
            lines.append(f"{time.strftime('%H:%M:%S')}  APPARU    {new}")
        seen = now
        args.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        time.sleep(0.5)
    lines.append(f"# fin — {time.strftime('%H:%M:%S')}, "
                 f"{len(lines) - 1} evenements")
    args.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
