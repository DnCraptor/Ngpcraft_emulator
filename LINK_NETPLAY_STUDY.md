# NgpCraft Emulator - Link And Netplay: Etat De L'art Et Feuille De Route

> Etude datee du 2026-08-04. Point de depart : « est-ce que l'approche Fightcade peut
> vraiment nous aider a stabiliser le link ? »
>
> 🔑 **Reponse courte : non, pas la partie qu'on croit — et la bonne source n'est pas
> Fightcade, c'est la scene Game Boy.** Fightcade emule UNE borne avec deux flux
> d'inputs : il n'y a pas de cable chez eux. La Game Boy a exactement notre structure,
> deux consoles independantes reliees par un cable serie, et elle a converge sur
> l'architecture qu'on a deja.

> ## ✅ VERIFIE 2026-08-04 sur `44e286b` — les deux affirmations centrales sont MESUREES
>
> Banc : `cpp/build/ngpc_core.dll`, `bios.bin`, `tests/roms/link_probe.ngc`, Python
> 3.12.10. Suite de reference `test_link_cable` + `test_netplay_mirror` +
> `test_savestate` : **54 tests verts en 90 s**.
>
> 1. **Le savestate ne porte rien du canal serie.** Capture a `rx_depth=2` /
>    `rx_read_count=1021`, 30 frames de divergence jusqu'a `rx_depth=3` /
>    `rx_read_count=1097`, restore — **tous les champs restent a la valeur d'apres les
>    30 frames**. Le restore est un no-op sur le cable.
> 2. **Deux re-simulations depuis le meme etat restaure divergent.** 60 frames :
>    run 1 finit a `rx_total=1174` sur les deux consoles, run 2 a `1175`. Un octet de
>    plus a traverse le cable. Un octet de derive en 60 frames, c'est un desync.
>
> Fige en test permanent : `tests/test_link_savestate_roundtrip.py`, ecrit d'abord en
> `xfail(strict=True)` contre ces deux chiffres.

> ## ✅ ETAPE 0 LIVREE le 2026-08-04 — le canal serie est dans l'etat serialise
>
> Fait sur un PC de deplacement, avec une toolchain mingw-w64 GCC 13.3.0 installee en
> per-user. ⚠️ **Le core livre avait ete builde avec GCC 13.1.0 (le mingw de Qt)**, donc
> avant toute modification le core INCHANGE a ete rebuilde et passe au corpus de goldens
> — **82 tests verts** — pour qu'un ecart ulterieur ne puisse pas etre mis sur le dos du
> compilateur.
>
> Ce qui a ete construit :
> - **`ngpc_link_state_t`** (`NGPC_LINK_STATE_VERSION 1`) + `ngpc_get_link_state` /
>   `ngpc_set_link_state`, meme contrat `version`/`size` que le bloc aux : un blob d'un
>   autre build est refuse en entier, jamais applique a moitie.
> - **`core.native.LinkState`** et `NativeMachine.link_state()` / `set_link_state()`.
> - **Format `NGPCST03`** = cpu + aux + link + image. `NGPCST02` et `NGPCST01` restent
>   chargeables, chacun sans le bloc qu'il precede — le motif que le fichier utilisait
>   deja pour v1.
> - **Port libretro** : meme bloc, `kStateVersion` 1 → 2, et `retro_serialize` **refuse**
>   de rendre un etat dont le cable a deborde plutot que d'en livrer un inexact.
> - Les trois tests d'acceptation passent ; smoke libretro `state=51964`, `reopen=ok`.
>
> 🔑 **UNE DEVIATION PAR RAPPORT AU PLAN, ASSUMEE.** Le plan disait « etendre
> `ngpc_aux_state_t` ». Ca a ete fait **en bloc separe** a la place, pour deux raisons
> trouvees en lisant le code : ce struct est documente comme « le CPU son, le T6W28 et
> les timers », et le cable n'est aucun des trois ; et surtout **le grossir aurait
> decale l'image memoire a l'interieur de chaque `NGPCST02` qu'un joueur possede deja**.
> Un bloc a lui seul laisse ces fichiers intacts.

---

## 1. Ce qu'on a, et ce qu'on a mesure

Deux modes coexistent, `core/link.py` et `core/netplay.py`.

**`TcpLink` — on relaie les octets du cable.** Le jeu ecrit un octet et BLOQUE sur la
reponse du peer, donc il avance d'une frame logique par aller-retour. Mesure a travers
le shell (match link Fatal Fury, compteur de logique du jeu 0x4B3C, par frame emulee) :

| aller-retour reseau | 0 ms | 33 ms | 67 ms | 134 ms |
|---|---|---|---|---|
| vitesse du jeu | 1.00 | 0.80 | 0.57 | 0.36 |

**`MirrorSession` — les deux consoles tournent sur les DEUX PC.** Le cable redevient un
tuyau local sans latence, seul le pad (1 octet) traverse le reseau. La latence part dans
le delai d'input au lieu de la vitesse. Meme banc, meme match :

| delai d'input (frames) | 0 | 2 | 4 | 8 | 16 |
|---|---|---|---|---|---|
| vitesse du jeu | 0.97 | 0.97 | 0.97 | 0.97 | 0.97 |

Autres chiffres qui comptent pour la suite :

- **3.3 ms par frame pour la paire de consoles**, sur un budget de 16.7 ms.
- `CABLE_SLICE = 400` instructions entre deux relais, avec table par jeu : The Last
  Blade casse au-dela de 400, 2000 echoue deja.
- Checksum de desync : CRC32 de `0x4000`+`0x2C00` sur les deux consoles, toutes les
  60 frames.

**Ce qui casse reellement**, tel que documente dans le code : des desyncs de determinisme
(ordre des consoles, octet de langue de la cartouche), et des echecs du protocole link du
JEU (« LINK ERROR » de Card Fighters' Clash, seuil de The Last Blade). Les coupures
reseau, elles, sont deja traitees proprement (`lost`, `_T_BYE`, `plain_network_error`).

## 2. Trous constates dans le code

### 2.1 ⛔ Le canal serie n'est PAS dans l'etat serialise — le point dur

`AuxState` (`core/native.py`, ~l.222-270, `AUX_STATE_VERSION = 1`) couvre le Z80, le
T6W28, les timers, `scanline`, `frame_count`. Il ne couvre **pas** `serial_tx` /
`serial_rx` / `serial_tx_busy` / `serial_rx_pending` / `serial_tx_cycles` /
`serial_cts_high`, qui vivent dans `Machine` (`cpp/src/core.cpp`, ~l.1136-1226) et ne
sont joignables que par `ngpc_serial_state`, **en lecture seule par conception** (« a
read-only snapshot for the debugger's Link tab »).

Le savestate du joueur, c'est `PlayPage._capture_state` : `cpu` + `AuxState` +
`read(0, 0xC000)`. Le cable n'y est nulle part. ✅ Mesure ci-dessus : le restore ne
touche pas au canal, et deux re-simulations divergent d'un octet en 60 frames.

⚠️ **Ou c'est atteignable, exactement.** `PlayPage._mirror_blocks()` refuse savestate,
rewind et reset pendant le **mirror** — donc le chemin online est protege, mais pour une
raison sans rapport (l'autre PC ne suivrait pas). Il ne garde **ni** le cable local 2
joueurs (`_link_peer`) **ni** le direct-IP (`_net_link`), ou F2 et le rewind sont actifs.
Et **la bague de rewind partage `_capture_state`** : chaque cran de rewind dans une
session cablee passe par ce trou.

✅ **Corrige le 2026-08-04** (voir le bandeau) : le cable est desormais un bloc versionne
a lui seul, et le savestate joueur est `NGPCST03` = cpu + aux + link + image.

⚡ **Le port libretro avait le meme trou, et pour lui c'etait pire.**
`Ngpcraft_emulator_lirbreto/libretro/libretro.cpp` :

```c
size_t state_size() {
    return sizeof(StateHeader) + sizeof(ngpc_cpu_t) + sizeof(ngpc_aux_state_t) +
           sizeof(ngpc_rtc_t) + kWorkImageSize;
}
```

Meme composition, meme absence. Or le netplay libretro repose entierement sur
`retro_serialize` — c'est le mecanisme exact que TGB Dual et DoubleCherryGB utilisent
pour leur « lagfree netplay » (§4.2). Le core libretro desyncherait en netplay pour
precisement la raison decrite ici. **L'etape 0 repare les deux bases d'un coup.**

### 2.2 Aucune mesure de RTT

Verifie par grep sur tous les `.py` : il n'existe **aucune** mesure d'aller-retour dans
le projet. `DEFAULT_DELAY = 3` s'applique quelle que soit la connexion, et
`_ask_mirror_delay` fait taper un nombre a la main aux deux joueurs (« ping en ms / 17,
plus un »). Sans mesure, on ne peut pas distinguer « le link est instable » de « la ligne
de ce joueur est mauvaise », donc aucun rapport de bug n'est interpretable.

### 2.3 TCP pour tout, y compris l'octet d'input par frame

Head-of-line blocking : un paquet perdu bloque des inputs **deja arrives** le temps du
retransmit et du RTO. C'est vecu comme « le link a lache », alors que c'est le transport.

### 2.4 Pas de timesync par avantage de frame

`step()` renvoie `"waiting"` et le stall tombe entierement sur le cote en retard. Le
jitter est concentre au lieu d'etre lisse.

### 2.5 Le desync est detecte, pas diagnostique

`desync_at` dit qu'il y en a eu un. Il ne dit pas pourquoi, et il n'y a pas de quoi
rejouer la session pour bissecter.

### 2.6 Une partie de l'etat du cable est cote Python, hors de toute serialisation

Constate en ecrivant le test : `InProcessLink.bytes_ab` / `bytes_ba` ont du etre remis a
la main pour que les deux runs soient comparables. Ce sont des compteurs, donc benins —
mais ils montrent que « l'etat du cable » est aujourd'hui **reparti des deux cotes de la
frontiere FFI**. C'est un argument de plus pour §6 « le vrai correctif ».

## 3. Fightcade — ce qu'on a regarde

FBNeo + GGPO (`src/burner/win32/fbn_ggpo.cpp`). `save_game_state` / `load_game_state`
serialisent via `BurnAreaScan(ACB_FULLSCANL)`, `advance_frame` rejoue une frame pendant
un rollback, `ggpo_set_frame_delay(ggpo, delay)` fixe un delai court (2 frames) et le
rollback absorbe le reste. Autour : un perfmon (`ping`, `kbps_sent`,
`predict/recv/send_queue_len`, `local_frames_behind`, `remote_frames_behind`, graphe de
fairness), des spectateurs via `ggpo_start_streaming()`, et les « detectors » — des
`.inf` d'une ligne par evenement, `player1=P1 Win,68K RAM,gts,0xDE62,0,8`, qui donnent
au classement le resultat du match.

⛔ **Le point structurel : Fightcade n'a pas de cable.** Une borne, deux flux d'inputs,
pas de FIFO serie, pas de RTS/CTS, pas de handshake applicatif entre deux machines.
Notre partie difficile est exactement celle qu'ils n'ont jamais eu a resoudre.

⚡ **Et GGPO resout la latence, pas la stabilite.** Le rollback supprime du delai
ressenti ; il ne rend rien plus deterministe. Chez nous la vitesse est deja reglee (0.97
contre 0.36), donc ce qui resterait a gagner c'est 2-3 frames de feeling. Sur un
savestate incomplet (§2.1, desormais mesure), ajouter du rollback ne stabilise pas le
link : ca fabrique des desyncs.

A garder quand meme : le **synctest**, le **perfmon**, l'idee du **replay par log
d'inputs**. A ne pas copier : leur `ComputeIncrementalChecksum` n'est compile qu'en debug
et n'est pas valide en release — notre CRC32 toutes les 60 frames est plus rigoureux.

## 4. Game Boy — la vraie source

### 4.1 Le camp « relais d'octets », et son plafond

**BGB** est le protocole de reference. Paquets de 8 octets, et surtout : chaque cote
envoie son timestamp **en horloges 2 MiHz** et maintient l'ecart avec celui recu, « so
each side can, at the right times, **wait for the remote side**, for synchronization ».
Il y a meme un `sync3` avec `b2=0` qui ne transfere rien et sert uniquement a faire
avancer l'horloge du peer.

**GBE+** pousse plus loin avec son « hard sync » : 32 cycles, pause, on attend l'autre,
on recommence. Son seuil est **par peripherique** — 32 pour le multi DMG/GBC, 4 pour
l'infrarouge GBC, 40 pour le HuC-1.

⚡ C'est notre `CABLE_SLICE = 400`, avec notre commentaire « correctness figure, not a
tuning knob » et notre table par jeu. **Convergence independante sur le meme design.**
Mais les deux paient le meme prix — un aller-retour reseau toutes les 32 cycles ou tous
les 400 instructions, c'est du LAN. C'est le plafond qu'on a deja mesure et deja quitte.

### 4.2 Le camp « mirroir », et il a gagne

- **TGB Dual** emule les deux Game Boy cote a cote dans une seule instance, et fait
  partie des deux seuls cores libretro a supporter le netplay.
- **BizHawk** fait pareil avec **DualGambatte** — deux GB traites comme un seul systeme,
  ce qui est obligatoire chez eux puisque tout doit etre deterministe et serialisable
  pour le TAS.
- **mGBA** a son `GBASIOLockstepCoordinator`, player 0 maitre d'horloge — mais **en un
  seul process**. Le link reseau y est une demande ouverte depuis 2021 (issue #2379),
  toujours non livree. L'emulateur le plus serieux sur le sujet n'a jamais expedie le
  cable en reseau.

🔑 **Et la confirmation independante la plus nette vient de DoubleCherryGB** (fork de TGB
Dual), qui propose litteralement nos deux modes et documente lequel marche :

> « The netpacket api is activated when you set emulated gameboys to 1. […] only meant
> for trading purposes and **may be too slow for actual Multiplayer**. For
> Multiplayer-sessions please set the amount of emulated gameboys to **2 or higher**,
> because this will activate the old savestate sync for **lagfree netplay**. »

Un GB emule = relais du cable = trop lent, reserve au troc. Deux GB emules = seuls les
inputs traversent = « lagfree ». C'est mot pour mot notre `TcpLink` a 0.36 contre notre
`MirrorSession` a 0.97, ecrit par quelqu'un qui ne nous connait pas.

## 5. Lecons tirees

**L1 — Notre choix d'architecture est valide de l'exterieur.** Le mirroir n'est pas un
pari, c'est la seule approche qui a livre du link Game Boy jouable en ligne.

**L2 — Le relais d'octets est un cul-de-sac hors LAN.** Mesure chez nous, mesure chez
eux, abandonne par les deux. `TcpLink` reste utile pour le LAN et le debug, pas au-dela.

**L3 — 🔑 Tous ceux qui ont fait marcher ca ont mis les deux consoles ET le cable DANS le
core**, avec le relais pilote par l'horloge serie du materiel. Nous avons les deux
consoles en Python et le cable relaye par une heuristique de tranche d'instructions qui
traverse la frontiere FFI a chaque pompage. `CABLE_SLICE = 400` est une approximation du
temps-cable en instructions, alors que le core sait deja compter en cycles serie
(`serial_tick()`). **Le handshake VS de Card Fighters' Clash et le seuil de The Last
Blade ne sont pas deux bugs separes : ce sont deux symptomes de la meme approximation.**

**L4 — Notre seuil est global la ou GBE+ le rend configurable.** Une seule valeur doit
satisfaire tous les jeux, calee sur le pire cas connu. Le jour ou un jeu exige moins de
400, il n'y a aucun mecanisme.

**L5 — Le rollback n'est pas une impasse, mais son prerequis n'est pas negociable.**
TGB Dual + sync par savestate le font tourner au-dessus d'une paire mirroree. Ca ne
marche que parce que le core serialise l'integralite de l'etat, cable inclus. Donc §2.1
passe de « prerequis du rollback » a **« a faire, independamment »** — et la mesure du
2026-08-04 le confirme : le trou est reel, pas theorique.

**L6 — Ne pas confondre les deux problemes.** GGPO resout la latence. Le notre est la
stabilite. Les outils ne sont pas les memes.

## 6. Ce qui est envisage

### ✅ Etape 0 — le serie dans l'etat serialise — FAITE le 2026-08-04
Voir le bandeau en tete pour ce qui a ete construit et pourquoi c'est un bloc separe
plutot qu'une extension de `ngpc_aux_state_t`.

⚠️ **Ce qui reste ouvert de cette etape :**
- `NGPC_LINK_FIFO_MAX` vaut 1024 par cote. La profondeur mesuree avec la ROM sonde en
  in-process est de **2-3**, mais un pont socket livre ce qu'une rafale reseau a apporte
  et n'a pas de plafond naturel. Le getter leve `overflow` au lieu de tronquer en
  silence, et libretro refuse alors de serialiser — **mais rien cote shell Python ne
  regarde encore ce drapeau**. A brancher sur un message joueur.
- La DLL de `cpp/build/` a ete rebuildee en GCC 13.3.0 sur cette machine. Le PC
  principal doit la reconstruire avec sa propre toolchain ; c'est un artefact, pas du
  source.

### ✅ Hors plan, livre le 2026-08-06 — la session directe etait une porte a sens unique

Rapport d'un auteur homebrew (« never seems to actually get through »). Rien a voir avec le
transport, tout avec la **session** : l'`accept()` de l'hote n'avait aucun timeout, `cancel()`
n'etait appelable depuis aucun menu, et le garde « une tentative a la fois » avalait donc en
silence tous les clics suivants jusqu'a la fin de la session.

Livre : attente bornee et annulable (entree de menu + fermeture du panneau hote), join qui
**reessaie** au lieu d'echouer sur le premier `ECONNREFUSED`, messages d'erreur qui nomment
une cause au lieu d'un `WinError`, et propagation pause/vitesse/avance-rapide entre les deux
consoles d'un meme PC. **Aucune ligne sous `cpp/`** — voir DEVLOG 2026-08-06 pour les quatre
passes de verification et les neuf defauts qu'elles ont sortis (dont un QThread detruit en
cours d'execution).

⚠️ **Ce que ca ne resout pas, et qu'il faut dire aux joueurs** : derriere un NAT sans
redirection — ou derriere du CGNAT, ou aucune redirection n'est possible — rien ne peut
arriver, quel que soit le bouton. Le mode direct est realistement du LAN/Tailscale ; le
lobby-relais est la reponse au reste.

### Etape 1 — `CABLE_SLICE` par jeu
Le sortir de la constante globale et le mettre dans `core/quirks.py`, sur le modele des
seuils par peripherique de GBE+. Peu cher, deplace le probleme sans le resoudre, mais
supprime le risque « un jeu exige moins de 400 ».

### Etape 2 — mesure du RTT + perfmon
Timestamp piggyback sur `_T_CHECK`. Puis surfacer ce qui existe deja (`stalls`,
`bytes_in/out`, `frames_run`) plus le RTT. ⚡ Prerequis de tout diagnostic, et de
l'etape 3.

### Etape 3 — timesync par avantage de frame
Chaque cote annonce son numero de frame ; celui qui est en AVANCE saute volontairement
une frame. Lisse le jitter au lieu de le concentrer sur le retardataire.

### Etape 4 — synctest continu
Sauver / restaurer / rejouer chaque frame en local et comparer, a la `ggpo_start_synctest`.
Nos desyncs connus ont ete trouves au raisonnement et par un test a quatre consoles ;
ceci les sort automatiquement. **Le meilleur emprunt de toute l'etude.** ⚡ L'etape 0
etant faite, il est desormais possible : avant elle il aurait ete rouge en permanence
sur toute session cablee. `test_resimulation_from_a_restored_state_reproduces_the_cable`
en est deja la version a une seule mesure.

### Etape 5 — UDP + inputs redondants pour la session
Pour la stabilite, pas pour la latence : supprime le head-of-line blocking de TCP sur les
lignes qui perdent (§2.3), qui transforme une perte isolee en gel de plusieurs centaines
de millisecondes. Les 16 dernieres frames d'input tiennent en 16 octets. Le transport
reste TCP pour l'echange de cartouche, qui a besoin de fiabilite.

### Etape 6 — replay = log d'inputs
Une session mirror est deterministe depuis le power-on avec le flux d'inputs complet,
donc `(frame, pad_a, pad_b)` **est** le replay. `core/movie.py` existe deja. Donne les
replays et la bissection de desync (§2.5) d'un coup.

### Le vrai correctif — la paire et le cable dans le core
Faire descendre les deux consoles et le cable cote C++, avec le transfert declenche a la
fin du registre a decalage plutot que toutes les 400 instructions. **Supprime le reglage
de slice au lieu de le deplacer** (L3), et donne l'etape 0 gratuitement : un `serialize`
unique couvre les deux consoles et le cable, exactement comme `retro_serialize` chez
libretro. ⚠️ Ce n'est pas un petit refactor — c'est une API « double machine » cote core.
Mais c'est la seule ligne de cette liste qui attaque la cause, et §2.6 montre que l'etat
du cable est deja eparpille des deux cotes de la FFI.

### Non retenu pour l'instant — le rollback
Cout : 3.3 ms par frame rejouee, soit ~10 ms pour 3 frames sur un budget de 16.7, en pic
et au pire moment. Gain : ~50 ms de delai ressenti. Contre : une nouvelle surface de
desync sur le sous-systeme deja fragile. Et la ludotheque NGPC penche vers le tour par
tour (Card Fighters' Clash est un jeu de cartes) ; le versus temps reel existe mais les
timings d'un portable 60 Hz sont bien plus larges que ceux d'un SF3 sur borne. **A
rediscuter apres l'etape 4**, jamais avant.

### Non retenu — les detectors
Rien a voir avec la stabilite : c'est une feature de classement. Format directement
transposable et la machinerie existe (`core/watches.py`, `core/ramsearch.py`), donc a
garder en reserve — mais ca ne repond pas a la question posee.

## 7. Ce qui invaliderait ce document

- **Si le versus temps reel s'avere un usage reel de la base de joueurs** : la section
  « non retenu — rollback » se rediscute, apres synctest.
- ✅ **Resolu le 2026-08-04** : « reste-t-il de l'etat cable hors du core ? » — oui,
  `InProcessLink.bytes_ab/ba` (§2.6). Benin en soi, mais ca renforce « le vrai
  correctif ».
- **Si le RTT mesure (etape 2) montre que nos joueurs sont surtout en LAN** : les etapes
  3 et 5 perdent l'essentiel de leur interet.
- **Si un jeu exige un `CABLE_SLICE` sous 400** avant l'etape 1 : l'etape 1 passe en
  urgence.

## 8. Sources

- BGB link protocol — https://bgb.bircd.org/bgblink.html
- DoubleCherryGB (fork TGB Dual) — https://github.com/TimOelrichs/doublecherryGB-libretro
- TGB Dual (libretro) — https://docs.libretro.com/library/tgb_dual/
- GBE+ serial over TCP/IP — https://github.com/shonumi/gbe-plus/issues/82
- mGBA multiplayer — https://deepwiki.com/mgba-emu/mgba/9.3-multiplayer-support
- mGBA networked link cable (ouvert depuis 2021) — https://github.com/mgba-emu/mgba/issues/2379
- BizHawk Dual Gameboy — https://tasvideos.org/Forum/Topics/17307
- Fightcade FBNeo (GGPO) — https://github.com/fightcadeorg/fightcade-fbneo
- Fightcade detectors — https://github.com/fightcadeorg/fightcade-detectors

Sources internes : `core/link.py`, `core/netplay.py`, `core/native.py`,
`core/savestate.py`, `ngpc_shell.py` (`_capture_state`, `_apply_state`,
`_mirror_blocks`), `cpp/src/core.cpp`, `tests/test_link_savestate_roundtrip.py`,
et `Ngpcraft_emulator_lirbreto/libretro/libretro.cpp`.
