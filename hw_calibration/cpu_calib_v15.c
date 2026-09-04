/* ============================================================================
 * NGPC CALIBRATION ROM  (v15, 2026-08-27) -- CE QUE LA v14 NE POUVAIT PAS SEPARER
 *
 * La campagne v14 a ferme quatre grandeurs (octet lu, MUL, DIV, lecture 32 bits) et
 * fait tomber l'ecart du corpus de 4,78 % a 1,30 %. Il reste exactement deux trous,
 * et aucun des deux ne se comble par le raisonnement -- d'ou cette ROM.
 *
 * NAVIGATION : GAUCHE / DROITE. Le numero de page est affiche en chiffre ligne 1.
 * ⛔ Pas de variable globale (cf. la v6, qui bootait chez nous et PLANTAIT la console).
 * Ligne-cle "0123456789" en ligne 17 pour le banc emulateur.
 *
 * ===========================================================================
 * PAGE 0 -- DE COMBIEN LA FILE PEUT-ELLE PRENDRE DE L'AVANCE ?
 * ===========================================================================
 * LE TROU. Sur la page 0 de la v14, nos PENTES matchent le silicium mais notre
 * NIVEAU est ~18 % trop rapide : ces boucles melangent une instruction longue
 * (`mul`) et de longues charges fetch-bound, et c'est exactement le melange ou le
 * RECOUVREMENT fetch/execution decide du resultat. Notre `biu_slack` vaut 16 cycles
 * (une file de 4 octets a 4 cy l'octet) -- un nombre DEDUIT, jamais mesure.
 *
 * LE MONTAGE. Corps = `div WA,E` (2 octets pour ~32 cycles : il ne consomme presque
 * pas de bus et laisse la file prendre toute l'avance qu'elle peut) suivi de k
 * charges `ld XWA,#imm32` (5 octets pour 5 etats : franchement fetch-bound, elles
 * DEPENSENT cette avance). k = 1, 2, 4, 8.
 *
 * LECTURE. Les premieres charges sont payees avec l'avance accumulee et coutent donc
 * MOINS que leur prix plein ; une fois l'avance epuisee, chaque charge suivante coute
 * plein tarif. Le prix plein est deja connu -- v14 page 1, 4,03 cy/octet, soit
 * **20,15 cy par charge**. Donc :
 *   - la pente entre k=4 et k=8 doit retomber sur ~20,15 (controle du montage) ;
 *   - ce que les premieres charges coutent EN MOINS que 20,15 chacune est l'avance
 *     que la file avait reellement prise, en cycles.
 * ⇒ Ce nombre est `biu_slack` mesure au lieu d'etre deduit.
 *
 * ===========================================================================
 * PAGE 1 -- UN ACCES MEMOIRE COUTE-T-IL PAR OCTET OU PAR ACCES ?
 * ===========================================================================
 * LE TROU. La v14 page 2 a mesure une lecture 32 bits a 16,22 cy contre 12,09 chez
 * nous. On a donc ajoute un cout par OCTET de donnee -- et il cale la page 2 au
 * cycle pres, MAIS il ne corrige PAS le `MEM` du corpus (+6,6 %, dernier ecart
 * notable) et il degrade `WORK1`. Le champ est reste desarme pour cette raison.
 * ⚡ La v14 ne pouvait pas trancher : elle n'a mesure QU'UNE largeur.
 *
 * LE MONTAGE. Huit lectures par tour, a trois largeurs, plus un controle de
 * linearite :
 *      RB8   8 x `ld A,(XHL)`      8 octets deplaces
 *      RW8   8 x `ld WA,(XHL)`    16 octets
 *      RL8   8 x `ld XWA,(XHL)`   32 octets
 *      RL16 16 x `ld XWA,(XHL)`   64 octets
 * LECTURE. Cout PAR OCTET ⇒ RB8 < RW8 < RL8 dans un rapport 1:2:4 sur la part
 * memoire. Cout PAR ACCES ⇒ les trois se valent, et seul le nombre d'acces compte.
 * Un intermediaire ⇒ un cout fixe par acces PLUS un cout par octet, et les quatre
 * nombres donnent les deux termes.
 * ⛔ Si les trois se valent, notre `data_wait_q16` est faux DE FORME et pas seulement
 * de valeur : ne pas le rearmer en changeant le nombre.
 *
 * ===========================================================================
 * PAGE 2 -- ET LES ECRITURES ? (jamais mesurees, jamais facturees)
 * ===========================================================================
 * Ce coeur facture 0 pour une ecriture memoire, et rien ne l'a jamais contraint --
 * exactement le statut qu'avait la lecture avant la v14. Meme montage, meme largeurs,
 * en ecriture. ⛔ On ne facture pas ce qu'on n'a pas mesure, donc tant que cette page
 * n'est pas tiree, l'ecriture reste a 0 -- mais alors on sait que c'est un trou, pas
 * une mesure.
 *
 * ===========================================================================
 * ENCODAGES -- A RELEVER AU DESASSEMBLAGE APRES CONSTRUCTION, JAMAIS SUPPOSES
 * ===========================================================================
 * Prefixes de source : 83 = (XHL) octet, 93 = (XHL) mot, A3 = (XHL) long.
 * `D9 1C dd` djnz BC (forme registre-direct D8..DF, PAS le mis-encode `D0 1C`).
 * ⛔ Le deplacement du `djnz` est sur 8 BITS : corps <= ~125 octets, sinon asm900
 * refuse (« Out of range for relative reference »).
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define FRAMES 60

#define LDW   __asm(" ld XWA,0x01010101");
#define DIVE  __asm(" div WA,E");
#define RDB   __asm(" ld A,(XHL)");
#define RDW   __asm(" ld WA,(XHL)");
#define RDL   __asm(" ld XWA,(XHL)");
#define WRB   __asm(" ld (XHL),A");
#define WRW   __asm(" ld (XHL),WA");
#define WRL   __asm(" ld (XHL),XWA");

/* --- page 0 : profondeur de file --------------------------------------- */
#define Q1   DIVE LDW
#define Q2   DIVE LDW LDW
#define Q4   DIVE LDW LDW LDW LDW
#define Q8   DIVE LDW LDW LDW LDW LDW LDW LDW LDW

/* --- page 1 : lectures, trois largeurs --------------------------------- */
#define R8(X)  X X X X X X X X
#define RB_8   R8(RDB)
#define RW_8   R8(RDW)
#define RL_8   R8(RDL)
#define RL_16  R8(RDL) R8(RDL)

/* --- page 2 : ecritures, trois largeurs -------------------------------- */
#define WB_8   R8(WRB)
#define WW_8   R8(WRW)
#define WL_8   R8(WRL)
#define WL_16  R8(WRL) R8(WRL)

/* push/pop encadrent le bloc : le C tient ses variables dans ces memes registres.
 * XHL pointe la RAM de travail pour les pages 1 et 2 ; la page 0 l'ecrase sans le
 * lire, ce qui est sans effet. */
#define BLOC(NAME, TRIPS, BODY)                                   \
u16 NAME(void) {                                                  \
    u16 count; u8 frames; u8 prev, cur;                           \
    count = 0; frames = 0;                                        \
    prev = RAS_V;                                                 \
    while (frames < FRAMES) {                                     \
        __asm(" push BC"); __asm(" push DE"); __asm(" push XWA"); \
        __asm(" push XHL");                                       \
        __asm(" ld DE,3");                                        \
        __asm(" ld WA,1");                                        \
        __asm(" ld XHL,0x00004800");                              \
        __asm(" ld BC," #TRIPS);                                  \
        __asm(#NAME "L:");                                        \
        BODY                                                      \
        __asm(" djnz BC," #NAME "L");                             \
        __asm(" pop XHL");                                        \
        __asm(" pop XWA"); __asm(" pop DE"); __asm(" pop BC");    \
        count++;                                                  \
        cur = RAS_V;                                              \
        if (cur < prev) frames++;                                 \
        prev = cur;                                               \
    }                                                             \
    return count;                                                 \
}

BLOC(m_q1, 200, Q1)
BLOC(m_q2, 200, Q2)
BLOC(m_q4, 200, Q4)
BLOC(m_q8, 200, Q8)

BLOC(m_rb, 250, RB_8)
BLOC(m_rw, 250, RW_8)
BLOC(m_rl, 250, RL_8)
BLOC(m_rl2, 250, RL_16)

BLOC(m_wb, 250, WB_8)
BLOC(m_ww, 250, WW_8)
BLOC(m_wl, 250, WL_8)
BLOC(m_wl2, 250, WL_16)

u8 rasv_max(void)
{
    u8 mx; u8 r; u16 s;
    mx = 0;
    for (s = 0; s < 40000; s++) { r = RAS_V; if (r > mx && r < 250) mx = r; }
    return mx;
}

#define PAL 0
#define P   SCR_1_PLANE
#define NPAGES 4

u8 pad_page(u8 page)
{
    u8 j;
    j = JOYPAD;
    if ((j & J_RIGHT) && page < (NPAGES - 1)) page++;
    else if ((j & J_LEFT) && page > 0)        page--;
    return page;
}

void draw_labels(u8 page)
{
    ClearScreen(P);
    PrintString(P, PAL, 1, 17, "0123456789");
    PrintString(P, PAL, 1, 15, "GAUCHE/DROITE=page");

    if (page == 0) {
        PrintString(P, PAL, 1, 1, "FILE     p");
        PrintString(P, PAL, 1, 3, "Q1 div+1l :");
        PrintString(P, PAL, 1, 4, "Q2 div+2l :");
        PrintString(P, PAL, 1, 5, "Q4 div+4l :");
        PrintString(P, PAL, 1, 6, "Q8 div+8l :");
    } else if (page == 1) {
        PrintString(P, PAL, 1, 1, "LECTURE  p");
        PrintString(P, PAL, 1, 3, "RB  8 x 1o:");
        PrintString(P, PAL, 1, 4, "RW  8 x 2o:");
        PrintString(P, PAL, 1, 5, "RL  8 x 4o:");
        PrintString(P, PAL, 1, 6, "RL 16 x 4o:");
    } else if (page == 2) {
        PrintString(P, PAL, 1, 1, "ECRITURE p");
        PrintString(P, PAL, 1, 3, "WB  8 x 1o:");
        PrintString(P, PAL, 1, 4, "WW  8 x 2o:");
        PrintString(P, PAL, 1, 5, "WL  8 x 4o:");
        PrintString(P, PAL, 1, 6, "WL 16 x 4o:");
    } else {
        PrintString(P, PAL, 1, 1, "VALIDITE p");
        PrintString(P, PAL, 1, 3, "RASV      :");
        PrintString(P, PAL, 1, 5, "doit valoir 198");
        PrintString(P, PAL, 1, 6, "sinon tout est");
        PrintString(P, PAL, 1, 7, "a jeter.");
        PrintString(P, PAL, 1, 9, "note aussi le md5");
    }
}

#define STEP(row, fn)                                 \
    PrintDecimal(P, PAL, 12, (row), fn(), 5);         \
    page = pad_page(page);                            \
    if (page != drawn) break;

void main(void)
{
    u8 page; u8 drawn;

    InitNGPC();
    SetBackgroundColour(RGB(2, 2, 4));
    SysSetSystemFont();
    SetPalette(P, PAL, 4, RGB(15, 15, 15), RGB(15, 15, 15), RGB(15, 15, 15));
    CpuSpeed(0);

    page = 0;
    drawn = 0xFF;

    while (1) {
        if (page != drawn) { draw_labels(page); drawn = page; }
        PrintDecimal(P, PAL, 11, 1, page, 1);
        switch (page) {
        case 0:
            do { STEP(3, m_q1) STEP(4, m_q2) STEP(5, m_q4) STEP(6, m_q8) } while (0);
            break;
        case 1:
            do { STEP(3, m_rb) STEP(4, m_rw) STEP(5, m_rl) STEP(6, m_rl2) } while (0);
            break;
        case 2:
            do { STEP(3, m_wb) STEP(4, m_ww) STEP(5, m_wl) STEP(6, m_wl2) } while (0);
            break;
        default:
            do { STEP(3, rasv_max) } while (0);
            break;
        }
    }
}
