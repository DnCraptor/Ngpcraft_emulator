/* ============================================================================
 * NGPC CALIBRATION ROM  (v16, 2026-08-27) -- LES DEUX LIGNES QUI RESTENT
 *
 * Apres la campagne v13/v14/v15, le corpus des 26 cases silicium est a 0,67 %
 * d'ecart moyen. Il reste exactement deux choses au-dessus de 1,5 %, et cette ROM
 * les vise -- voir OPEN_ITEMS.md.
 *
 * NAVIGATION : GAUCHE / DROITE. Le numero de page est affiche en chiffre ligne 1.
 * ⛔ Pas de variable globale (cf. la v6, qui bootait chez nous et PLANTAIT la
 * console). Ligne-cle "0123456789" en ligne 17 pour le banc emulateur.
 *
 * ===========================================================================
 * PAGE 0 -- LE BUS PEUT-IL PRENDRE DE L'AVANCE PENDANT UN CALCUL LONG ?
 * ===========================================================================
 * LE TROU. `biu_slack` = 16 cycles (une file de 4 octets a 4 cy l'octet) est un
 * nombre DEDUIT, jamais mesure. Il porte tout le recouvrement fetch/execution, et
 * `branch_taken_extra = 4` a ete cale AVEC lui en place : les deux se tiennent.
 *
 * ⛔ ET LA PAGE 0 DE LA v15 NE POUVAIT PAS Y REPONDRE PROPREMENT. Elle lisait des
 * MARGES (k=1->2, 2->4, 4->8) alors que l'avance est depensee UNE FOIS par tour :
 * elle se voit dans l'ORDONNEE, pas dans la pente. Les marges silicium etaient a
 * plein tarif partout, ce qui est compatible avec « aucune avance » comme avec
 * « une avance constante » -- le montage ne separait pas les deux.
 *
 * LE MONTAGE, QUI LES SEPARE. Une chaine de HUIT charges `ld XWA,#imm32` (40 octets
 * pour 40 etats : franchement limitee par le bus, 160 cy de fetch contre 80
 * d'execution), dans laquelle on INSERE k divisions `div WA,E` (2 octets pour ~26 cy
 * d'execution : elles ne consomment presque pas de bus et occupent le processeur).
 *      L8   8 charges, 0 division   <- la reference
 *      D1   8 charges, 1 division
 *      D2   8 charges, 2 divisions
 *      D3   8 charges, 3 divisions
 * La boucle reste limitee par le bus jusqu'a k=3 (184 cy de fetch contre 159
 * d'execution), donc si le bus peut travailler PENDANT une division, l'execution de
 * celle-ci est GRATUITE et son cout marginal se reduit a ses 2 octets, soit ~8 cy.
 *
 * LECTURE. La pente contre k est le cout marginal d'une division inseree :
 *      ~8 cy    ⇒ recouvrement TOTAL : le bus travaille pendant tout le calcul ;
 *      ~34 cy   ⇒ recouvrement NUL : execution et fetch se serialisent (8 + 26) ;
 *      entre    ⇒ le recouvrement est BORNE, et la borne est l'avance reelle,
 *                 en cycles, par division : (34 - pente).
 * ⇒ C'est `biu_slack` mesure au lieu d'etre deduit. Notre modele le donne a 16 cy.
 *
 * ===========================================================================
 * PAGE 1 -- CE QUE COUTE VRAIMENT UNE INTERRUPTION, A QUATRE CADENCES
 * ===========================================================================
 * LE TROU. `WORK1` est la derniere case du corpus au-dessus de 1,5 % : silicium 218,
 * nous 208, soit -4,6 %. Or l'entree en interruption est deja au MINIMUM des quatre
 * valeurs documentees par Toshiba (28/24/22/18 etats, indexees sur la largeur de bus
 * de la zone de pile) : elle ne peut pas descendre sans sortir de la table.
 *
 * ⚠️ Le tir v8 ne donnait que DEUX cadences (une IRQ par ligne, une toutes les
 * quatre). Deux points donnent une droite qu'on ne peut pas verifier -- et c'est
 * exactement l'erreur qui avait coute trois documents a la v10.
 *
 * LE MONTAGE. Le meme lot de travail, cinq fois, sous cinq regimes d'interruption.
 * Timer 0 sur la broche externe TI0 (mode 00), donc une impulsion par LIGNE :
 *      W0   INTT0 interdit (niveau 0)          -> reference, aucune interruption
 *      W1   TREG0 = 1   -> une IRQ par ligne      (~152 par trame)
 *      W2   TREG0 = 2   -> une IRQ / 2 lignes     (~76)
 *      W4   TREG0 = 4   -> une IRQ / 4 lignes     (~38)
 *      W8   TREG0 = 8   -> une IRQ / 8 lignes     (~19)
 *
 * LECTURE. Le cout d'un bloc contre le NOMBRE d'interruptions qu'il subit est une
 * droite ; sa pente est le cout COMPLET d'une interruption prise -- entree, stub
 * BIOS et `reti` compris. Quatre points, donc la droite est verifiable.
 * ⛔ CE N'EST PAS UNE MESURE DE L'ENTREE SEULE. Aucun gestionnaire n'est installe
 * (volontaire : installer un vecteur utilisateur est le genre de chose qui fait
 * booter une ROM chez nous et planter la console). Si la pente matche apres nos
 * corrections, l'entree a 18 etats est innocente et le sujet est clos ; si elle ne
 * matche pas, il faudra une ROM qui installe SON gestionnaire pour separer l'entree
 * de ce que le stub fait -- mais on saura alors que ca vaut le risque.
 *
 * ⚠️ W0 EST LE CONTROLE QUI VALIDE LE TIR : il ne fait intervenir aucune
 * interruption et doit retomber sur le meme nombre que la reference du meme lot de
 * travail. S'il derive, le reglage du timer a fuit et les quatre autres ne veulent
 * rien dire.
 *
 * ===========================================================================
 * ENCODAGES -- A RELEVER AU DESASSEMBLAGE APRES CONSTRUCTION, JAMAIS SUPPOSES
 * ===========================================================================
 *   40 xx xx xx xx  ld XWA,#imm32   | CD 51  div WA,E   | D9 1C dd  djnz BC,dd
 * ⛔ Le deplacement du `djnz` est sur 8 BITS : corps <= ~125 octets.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * Depouillement : hw_calibration/v16_gate.py
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define FRAMES 60

/* TRUN est deja defini par ngpc.h (sans volatile) : on garde le sien. */
#define K_TREG0   (*(volatile u8 *)0x0022)
#define K_T01MOD  (*(volatile u8 *)0x0024)
#define K_INTET10 (*(volatile u8 *)0x0073)

#define LDW   __asm(" ld XWA,0x01010101");
#define DIVE  __asm(" div WA,E");

/* --- page 0 : huit charges, k divisions inserees ----------------------- */
#define L2   LDW LDW
#define L8   L2 L2 L2 L2
#define P_L8 L8
#define P_D1 L2 L2 DIVE L2 L2
#define P_D2 L2 DIVE L2 L2 DIVE L2
#define P_D3 L2 DIVE L2 DIVE L2 DIVE L2

/* --- page 1 : le lot de travail, identique sous les cinq regimes ------- */
#define WORK L8 L8

#define BLOC(NAME, TRIPS, BODY)                                   \
u16 NAME(void) {                                                  \
    u16 count; u8 frames; u8 prev, cur;                           \
    count = 0; frames = 0;                                        \
    prev = RAS_V;                                                 \
    while (frames < FRAMES) {                                     \
        __asm(" push BC"); __asm(" push DE"); __asm(" push XWA"); \
        __asm(" ld DE,3");                                        \
        __asm(" ld WA,1");                                        \
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

BLOC(m_l8, 150, P_L8)
BLOC(m_d1, 150, P_D1)
BLOC(m_d2, 150, P_D2)
BLOC(m_d3, 150, P_D3)

BLOC(m_work, 100, WORK)

/* Reglage du timer 0 sur la broche externe TI0 : une impulsion par ligne, et une
 * interruption tous les `period` lignes. Repris tel quel de la ROM v8, qui a tourne
 * sur silicium -- ⛔ ne pas « ameliorer » un montage deja tire. */
void irq_off(void)  { K_INTET10 = (u8)(K_INTET10 & 0xF8); }
void irq_on(void)   { K_INTET10 = (u8)((K_INTET10 & 0xF8) | 0x03); }

void timer_setup(u8 period)
{
    TRUN     = (u8)(TRUN & 0xFE);       /* timer 0 a l'arret pendant le reglage */
    K_T01MOD = (u8)(K_T01MOD & 0xFC);   /* mode 00 = broche externe TI0 */
    K_TREG0  = period;
    TRUN     = (u8)(TRUN | 0x81);       /* prediviseur + timer 0 en marche */
}

u16 work_at(u8 period, u8 enable)
{
    u16 r;
    timer_setup(period);
    if (enable) irq_on(); else irq_off();
    r = m_work();
    irq_off();
    return r;
}

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
        PrintString(P, PAL, 1, 1, "AVANCE   p");
        PrintString(P, PAL, 1, 3, "L8 0 div  :");
        PrintString(P, PAL, 1, 4, "D1 1 div  :");
        PrintString(P, PAL, 1, 5, "D2 2 div  :");
        PrintString(P, PAL, 1, 6, "D3 3 div  :");
    } else if (page == 1) {
        PrintString(P, PAL, 1, 1, "IRQ      p");
        PrintString(P, PAL, 1, 3, "W0 aucune :");
        PrintString(P, PAL, 1, 4, "W1 /ligne :");
        PrintString(P, PAL, 1, 5, "W2 /2 lig :");
        PrintString(P, PAL, 1, 6, "W4 /4 lig :");
        PrintString(P, PAL, 1, 7, "W8 /8 lig :");
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

#define STEPW(row, per, en)                                   \
    PrintDecimal(P, PAL, 12, (row), work_at((per), (en)), 5);  \
    page = pad_page(page);                                    \
    if (page != drawn) break;

void main(void)
{
    u8 page; u8 drawn;

    InitNGPC();
    SetBackgroundColour(RGB(2, 2, 4));
    SysSetSystemFont();
    SetPalette(P, PAL, 4, RGB(15, 15, 15), RGB(15, 15, 15), RGB(15, 15, 15));
    CpuSpeed(0);
    irq_off();

    page = 0;
    drawn = 0xFF;

    while (1) {
        if (page != drawn) { draw_labels(page); drawn = page; }
        PrintDecimal(P, PAL, 11, 1, page, 1);
        switch (page) {
        case 0:
            do { STEP(3, m_l8) STEP(4, m_d1) STEP(5, m_d2) STEP(6, m_d3) } while (0);
            break;
        case 1:
            do { STEPW(3, 1, 0) STEPW(4, 1, 1) STEPW(5, 2, 1)
                 STEPW(6, 4, 1) STEPW(7, 8, 1) } while (0);
            break;
        default:
            do { STEP(3, rasv_max) } while (0);
            break;
        }
    }
}
