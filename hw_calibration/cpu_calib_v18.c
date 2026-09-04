/* ============================================================================
 * NGPC CALIBRATION ROM  (v18, 2026-08-27) -- DECOMPOSER LE COUT D'UNE INTERRUPTION
 *
 * POURQUOI ELLE DEBLOQUE TOUT LE RESTE.
 *
 * Le modele de recouvrement EN OCTETS (file de 4 octets, un octet par 4 cycles) est
 * ecrit et il reproduit toutes les mesures directes : v14 pages 1-4 a +-0,7 %, v15, et
 * surtout la v16 page 0 a +0,4 % la ou l'ancien credit en cycles se trompait de 40 %.
 * Il ne decroche que sur le corpus -- et dans TOUTES les configurations essayees, la
 * pire case est `WORK1`, a 11-12,4 %, quand aucune autre ne depasse 2,3 %.
 * **Hors `WORK1`, le modele en octets tourne a ~1,1 %.**
 *
 * Or `WORK1` est le cout d'une interruption, et la v16 page 1 l'a deja chiffre
 * independamment : **114,2 cy sur console contre 132,4 chez nous**, +16 %, sur quatre
 * cadences avec une droite a 0,54 %. Tant que l'interruption est fausse de 16 %, elle
 * empoisonne le seul corpus capable d'arbitrer le modele de file -- et elle l'empoisonne
 * DEUX FOIS PLUS sous ce modele-la.
 *
 * ⛔ ET LA v16 NE POUVAIT PAS ALLER PLUS LOIN. Elle mesure le cout COMPLET d'une IRQ --
 * entree materielle, aiguillage du BIOS, gestionnaire, `reti` -- en un seul nombre. Pour
 * savoir OU sont les 18 cycles manquants il faut faire varier le gestionnaire, donc en
 * installer un a soi. C'est ce que fait cette ROM.
 *
 * ⚠️ CE QUI REND CA SUR. Le vecteur utilisateur Timer 0 est en **0x6FD4**, et le
 * template le declare deja (`TI0_INT`, un pointeur de fonction) : `InitNGPC()` y installe
 * `DummyFunction` a chaque demarrage. On ne fait donc qu'ecrire un pointeur la ou le SDK
 * en ecrit deja un -- pas de vecteur invente, pas de convention supposee.
 *
 * NAVIGATION : GAUCHE / DROITE. Numero de page en chiffre ligne 1.
 * ⛔ Pas de variable globale (cf. la v6). Ligne-cle "0123456789" en ligne 17.
 *
 * ===========================================================================
 * PAGE 0 -- L'ECHELLE DE `nop` : separer le FIXE du CONTENU
 * ===========================================================================
 * Le meme lot de travail, avec une impulsion TI0 par ligne (~152/trame), sous quatre
 * regimes qui ne different QUE par le gestionnaire installe :
 *      W0    interruptions interdites          -> reference, aucune IRQ
 *      N0    gestionnaire VIDE (juste le retour)
 *      N8    gestionnaire = 8 `nop`
 *      N24   gestionnaire = 24 `nop`
 *
 * LECTURE. Le cout d'un bloc contre le nombre de `nop` dans le gestionnaire est une
 * droite :
 *   - sa PENTE est le cout d'un `nop` execute DANS un gestionnaire. Il doit valoir ce
 *     qu'un `nop` coute ailleurs (~4 cy). S'il en coute plus, ce n'est pas l'entree qui
 *     est fausse, c'est la facon dont on facture le code d'un ISR ;
 *   - son ORDONNEE (extrapolee a zero `nop`), moins `W0`, est le cout FIXE d'une
 *     interruption : entree materielle + aiguillage du BIOS + retour. C'est ce nombre-la
 *     que la v16 melangeait avec le reste.
 * ⇒ Un seul des deux peut etre faux, et on saura lequel.
 *
 * ===========================================================================
 * PAGE 1 -- L'ECHELLE DE CHARGES : dans quel etat la file redemarre-t-elle ?
 * ===========================================================================
 * Meme echelle, mais avec des `ld XWA,#imm32` (5 octets pour 5 etats, franchement
 * limitees par le bus) au lieu de `nop` (1 octet, a l'equilibre).
 *      L0 / L2 / L4 / L8   gestionnaire = 0, 2, 4 ou 8 charges
 *
 * ⚡ POURQUOI CETTE PAGE EXISTE. Une interruption VIDE la file d'instructions : les
 * premieres instructions du gestionnaire payent donc leur fetch plein tarif. Un `nop`
 * est a l'equilibre bus/execution et ne le montre pas ; une charge de 5 octets, si. La
 * pente de cette page contre celle de la page 0 dit si notre modele redemarre la file
 * dans le bon etat apres une interruption -- exactement la piece que le modele en octets
 * ne facture peut-etre pas.
 *
 * ⚠️ `push XWA` / `pop XWA` encadrent les charges DANS TOUS les gestionnaires de la page,
 * y compris `L0` : un gestionnaire qui ecrase XWA sans le sauver corromprait le code
 * interrompu. Etant dans les quatre, ils se simplifient dans les differences.
 *
 * ===========================================================================
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * Depouillement : hw_calibration/v18_gate.py
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

#define NOP1  __asm(" nop");
#define NOP4  NOP1 NOP1 NOP1 NOP1
#define NOP8  NOP4 NOP4
#define LDW   __asm(" ld XWA,0x01010101");
#define LDW2  LDW LDW

/* --- les gestionnaires -------------------------------------------------- */
void __interrupt h_n0(void)  { }
void __interrupt h_n8(void)  { NOP8 }
void __interrupt h_n24(void) { NOP8 NOP8 NOP8 }

void __interrupt h_l0(void)  { __asm(" push XWA"); __asm(" pop XWA"); }
void __interrupt h_l2(void)  { __asm(" push XWA"); LDW2 __asm(" pop XWA"); }
void __interrupt h_l4(void)  { __asm(" push XWA"); LDW2 LDW2 __asm(" pop XWA"); }
void __interrupt h_l8(void)  { __asm(" push XWA"); LDW2 LDW2 LDW2 LDW2 __asm(" pop XWA"); }

/* --- le lot de travail, identique sous tous les regimes ------------------ */
#define WORK LDW2 LDW2 LDW2 LDW2 LDW2 LDW2 LDW2 LDW2

/* ⛔ 60 tours, pas plus : un bloc doit rester TRES court devant une trame. La boucle
 * exterieure compte les trames en guettant `RAS_V` qui redescend ; si un bloc approche
 * la duree d'une trame, deux lectures enjambent une bascule et la trame n'est pas
 * comptee -- le montage compte faux SANS LE DIRE (piege rencontre sur la v17). */
u16 m_work(void)
{
    u16 count; u8 frames; u8 prev, cur;
    count = 0; frames = 0;
    prev = RAS_V;
    while (frames < FRAMES) {
        __asm(" push BC"); __asm(" push XWA");
        __asm(" ld BC,60");
        __asm("WRKL:");
        WORK
        __asm(" djnz BC,WRKL");
        __asm(" pop XWA"); __asm(" pop BC");
        count++;
        cur = RAS_V;
        if (cur < prev) frames++;
        prev = cur;
    }
    return count;
}

void irq_off(void) { K_INTET10 = (u8)(K_INTET10 & 0xF8); }
void irq_on(void)  { K_INTET10 = (u8)((K_INTET10 & 0xF8) | 0x03); }

/* Timer 0 sur la broche externe TI0 (mode 00) : une impulsion par LIGNE. Repris tel
 * quel des ROM v8 et v16, qui ont tourne sur silicium. */
void timer_setup(void)
{
    TRUN     = (u8)(TRUN & 0xFE);
    K_T01MOD = (u8)(K_T01MOD & 0xFC);
    K_TREG0  = 1;
    TRUN     = (u8)(TRUN | 0x81);
}

u16 work_with(Interrupt *h, u8 enable)
{
    u16 r;
    timer_setup();
    TI0_INT = h;                 /* le SDK ecrit deja ce vecteur dans InitNGPC */
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
        PrintString(P, PAL, 1, 1, "IRQ NOP  p");
        PrintString(P, PAL, 1, 3, "W0 aucune :");
        PrintString(P, PAL, 1, 4, "N0 vide   :");
        PrintString(P, PAL, 1, 5, "N8  8 nop :");
        PrintString(P, PAL, 1, 6, "N24 24nop :");
    } else if (page == 1) {
        PrintString(P, PAL, 1, 1, "IRQ LOAD p");
        PrintString(P, PAL, 1, 3, "L0  0 ld  :");
        PrintString(P, PAL, 1, 4, "L2  2 ld  :");
        PrintString(P, PAL, 1, 5, "L4  4 ld  :");
        PrintString(P, PAL, 1, 6, "L8  8 ld  :");
    } else {
        PrintString(P, PAL, 1, 1, "VALIDITE p");
        PrintString(P, PAL, 1, 3, "RASV      :");
        PrintString(P, PAL, 1, 5, "doit valoir 198");
        PrintString(P, PAL, 1, 6, "sinon tout est");
        PrintString(P, PAL, 1, 7, "a jeter.");
        PrintString(P, PAL, 1, 9, "note aussi le md5");
    }
}

#define STEPW(row, h, en)                                     \
    PrintDecimal(P, PAL, 12, (row), work_with((h), (en)), 5);  \
    page = pad_page(page);                                    \
    if (page != drawn) break;

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
    irq_off();

    page = 0;
    drawn = 0xFF;

    while (1) {
        if (page != drawn) { draw_labels(page); drawn = page; }
        PrintDecimal(P, PAL, 11, 1, page, 1);
        switch (page) {
        case 0:
            do { STEPW(3, h_n0, 0) STEPW(4, h_n0, 1)
                 STEPW(5, h_n8, 1) STEPW(6, h_n24, 1) } while (0);
            break;
        case 1:
            do { STEPW(3, h_l0, 1) STEPW(4, h_l2, 1)
                 STEPW(5, h_l4, 1) STEPW(6, h_l8, 1) } while (0);
            break;
        default:
            do { STEP(3, rasv_max) } while (0);
            break;
        }
    }
}
