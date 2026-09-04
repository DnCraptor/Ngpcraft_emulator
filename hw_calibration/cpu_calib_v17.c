/* ============================================================================
 * NGPC CALIBRATION ROM  (v17, 2026-08-27) -- `mul` ET `div` EN FORME **MOT**
 *
 * POURQUOI CETTE ROM EXISTE, ET POURQUOI ELLE EST PETITE.
 *
 * La v16 a montre que notre modele de recouvrement avait la mauvaise FORME : un
 * credit d'avance en CYCLES plafonne par un scalaire, la ou la machine a une FILE de
 * 4 OCTETS remplie a un octet par 4 cycles. Le modele en octets a ete ecrit, et sur
 * le montage v16 page 0 il rend **26,6 cy/division contre 26,5 mesures** -- exact,
 * avec la taille documentee de la file et AUCUN parametre libre (le credit en cycles
 * rendait 16,0).
 *
 * ⛔ MAIS ON NE PEUT PAS L'ARMER, et pour une raison qui n'est pas le modele. Arme,
 * il laisse le corpus a -1 % uniforme -- ce qui est bon -- SAUF trois cases :
 *      MUL   -9,0 %      DIV   -6,0 %      WORK1  -12,4 %
 * Et ces trois-la sont exactement les constantes qui n'ont JAMAIS ete mesurees sous
 * le modele courant :
 *   - les classes MUL/DIV de la ROM v2 sont `v = v * w` sur des `u16`, donc la forme
 *     **MOT** (19 etats / 56 cycles) -- heritee de l'epoque ou l'attente de fetch
 *     valait 10 cy/mot, corrigee deux fois depuis. La v14 n'a mesure que la forme
 *     OCTET, et son en-tete le dit : « ne pas deplacer la forme mot par symetrie » ;
 *   - `WORK1` est le cout d'une interruption, qui aura de toute facon a etre repris
 *     APRES que le modele de file soit fixe.
 *
 * ⚡ Armer le modele en octets avant de mesurer ces constantes, ce serait refaire
 * l'erreur qui a deja coute deux fois : poser un nombre juste par-dessus des nombres
 * faux, puis conclure que c'est le nombre juste qui est mauvais. Cette ROM ne fait
 * donc qu'UNE chose, et c'est voulu.
 *
 * NAVIGATION : GAUCHE / DROITE. Numero de page en chiffre ligne 1.
 * ⛔ Pas de variable globale (cf. la v6). Ligne-cle "0123456789" en ligne 17.
 *
 * ===========================================================================
 * LE MONTAGE (pages 0 et 1) -- identique a la v14 pages 3/4, en MOT
 * ===========================================================================
 * Unite = `ld XWA,#imm32` (5 octets, recharge l'operande a une constante) puis
 * l'operation en forme mot. k unites par tour, k = 1, 2, 4, 8 ; la pente contre k
 * donne le cout d'une unite, degage de l'enveloppe de boucle.
 *
 *   page 0   DIV mot   `div XWA,DE`     DA 50
 *   page 1   MUL mot   `mul XWA,DE`     DA 40
 *
 * ⚡ ET LA CHARGE SE SIMPLIFIE DANS LA DIFFERENCE. Les deux unites ne different que
 * par UN OCTET (0x50 contre 0x40), donc `DIV - MUL` est le surcout de la division sur
 * la multiplication sans aucune hypothese sur le reste. C'est le nombre le plus
 * robuste des deux pages.
 *
 * ⛔⛔ SEULEMENT 60 TOURS PAR BLOC, ET C'EST UNE CONTRAINTE DU HARNAIS, PAS UN CHOIX.
 * La boucle exterieure compte les trames en guettant `RAS_V` qui REDESCEND. Si un bloc
 * dure plus d'une trame, deux lectures consecutives de `RAS_V` peuvent enjamber une
 * bascule entiere : la trame n'est pas comptee, et le bloc s'etire au lieu de durer 60
 * trames. Avec 200 tours de huit divisions MOT (~56 cy), un bloc coute ~90 000 cycles
 * contre 102 485 dans une trame -- juste sous le seuil, et le montage se cassait :
 * `D8` sortait a 397 alors que `D4` valait 101, c'est-a-dire PLUS RAPIDE avec DEUX FOIS
 * plus de travail. 60 tours mettent le bloc a ~27 000 cycles, largement au-dessous.
 * ⚡ Regle : un bloc doit rester tres court devant une trame, sinon l'horloge de
 * reference du montage compte faux -- et elle compte faux SANS LE DIRE.
 *
 * ⚠️ `DE` VAUT 3 ET N'EST JAMAIS TOUCHE : pas de division par zero, et l'operande est
 * la meme a chaque tour puisque `ld XWA,#` recharge le dividende. Aucun chemin
 * degenere, aucune dependance aux donnees.
 *
 * ⛔ `DA 50` / `DA 40` sont la forme REGISTRE-DIRECT de la famille D8..DF, celle qui
 * est HW-prouvee (`D9 50` verifie sur console le 2026-07-06) -- PAS le mis-encode
 * `D0 50`, qui designe la memoire.
 *
 * LECTURE. Le depouillement (`v17_gate.py`) balaie les couts mot et rend ceux qui
 * reproduisent les deux pentes. ⛔ Il les cherche AVEC le modele de file arme : c'est
 * tout l'objet de la ROM, et les chercher sous l'ancien modele ne servirait a rien.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define FRAMES 60

/* ⛔ 0x00020001 / 3 = 43691 : le quotient TIENT dans 16 bits. Un dividende plus grand
 * (0x00030001) donne 65536,3 et fait DEBORDER le quotient -- notre coeur a un chemin
 * separe pour ce cas et le silicium peut le facturer autrement. On ne mesure pas une
 * instruction sur son chemin degenere. */
#define LDW   __asm(" ld XWA,0x00020001");
#define DIVW  __asm(" div XWA,DE");
#define MULW  __asm(" mul XWA,DE");

#define D1   LDW DIVW
#define D2   D1 D1
#define D4   D2 D2
#define D8   D4 D4
#define P1   LDW MULW
#define P2   P1 P1
#define P4   P2 P2
#define P8   P4 P4

#define BLOC(NAME, TRIPS, BODY)                                   \
u16 NAME(void) {                                                  \
    u16 count; u8 frames; u8 prev, cur;                           \
    count = 0; frames = 0;                                        \
    prev = RAS_V;                                                 \
    while (frames < FRAMES) {                                     \
        __asm(" push BC"); __asm(" push DE"); __asm(" push XWA"); \
        __asm(" ld DE,3");                                        \
        __asm(" ld BC," #TRIPS);                                  \
        __asm(#NAME "L:");                                        \
        BODY                                                      \
        __asm(" djnz BC," #NAME "L");                             \
        __asm(" pop XWA"); __asm(" pop DE"); __asm(" pop BC");    \
        count++;                                                  \
        cur = RAS_V;                                              \
        if (cur < prev) frames++;                                 \
        prev = cur;                                               \
    }                                                             \
    return count;                                                 \
}

BLOC(m_d1, 60, D1)
BLOC(m_d2, 60, D2)
BLOC(m_d4, 60, D4)
BLOC(m_d8, 60, D8)

BLOC(m_p1, 60, P1)
BLOC(m_p2, 60, P2)
BLOC(m_p4, 60, P4)
BLOC(m_p8, 60, P8)

u8 rasv_max(void)
{
    u8 mx; u8 r; u16 s;
    mx = 0;
    for (s = 0; s < 40000; s++) { r = RAS_V; if (r > mx && r < 250) mx = r; }
    return mx;
}

#define PAL 0
#define P   SCR_1_PLANE
#define NPAGES 3

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
        PrintString(P, PAL, 1, 1, "DIV MOT  p");
        PrintString(P, PAL, 1, 3, "D1 1 div  :");
        PrintString(P, PAL, 1, 4, "D2 2 div  :");
        PrintString(P, PAL, 1, 5, "D4 4 div  :");
        PrintString(P, PAL, 1, 6, "D8 8 div  :");
    } else if (page == 1) {
        PrintString(P, PAL, 1, 1, "MUL MOT  p");
        PrintString(P, PAL, 1, 3, "P1 1 mul  :");
        PrintString(P, PAL, 1, 4, "P2 2 mul  :");
        PrintString(P, PAL, 1, 5, "P4 4 mul  :");
        PrintString(P, PAL, 1, 6, "P8 8 mul  :");
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
            do { STEP(3, m_d1) STEP(4, m_d2) STEP(5, m_d4) STEP(6, m_d8) } while (0);
            break;
        case 1:
            do { STEP(3, m_p1) STEP(4, m_p2) STEP(5, m_p4) STEP(6, m_p8) } while (0);
            break;
        default:
            do { STEP(3, rasv_max) } while (0);
            break;
        }
    }
}
