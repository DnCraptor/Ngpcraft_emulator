/* ============================================================================
 * NGPC CALIBRATION ROM  (v19, 2026-08-29) -- LE VIDAGE DE FILE AU RETOUR D'IRQ
 *
 * POURQUOI ELLE EXISTE.
 *
 * Le modele de recouvrement EN OCTETS (file de 4 octets, un octet par 4 cycles) a ete
 * valide par la v18 la ou il comptait : ses DEUX pentes tombent sur le silicium
 * (4,08 cy/nop contre 4,03 ; 20,08 cy/charge contre 20,29), quand le credit en cycles
 * se trompe de 10 % sur la seconde. Mais son ORDONNEE -- le cout FIXE d'une
 * interruption -- vaut 139,1 cy contre **111,1** mesures.
 *
 * Quatre pistes ont ete fermees COTE MODELE, toutes par la mesure :
 *   - le fetch BIOS hors de la file           -> VRAIE cause, corrigee (156,1 -> 139,1)
 *   - la file rechargee pendant l'acceptation -> insuffisante (au PLAFOND : 126,6)
 *   - le cout du mot BIOS                     -> insuffisant  (a 1 cy/octet : 129,0)
 *   - `branch_taken_extra` compte deux fois   -> REFUTE (WORK0, sans aucune IRQ, bouge)
 *
 * ⇒ Il ne reste rien a deduire. Cette ROM ne cherche pas une constante : elle teste une
 * PREDICTION que le modele en octets fait et que le credit en cycles ne fait pas.
 *
 * ===========================================================================
 * LA PREDICTION, ET CE QUI LA REFUTERAIT
 * ===========================================================================
 * Une interruption vide la file d'instructions. Au RETOUR (`reti`), le code interrompu
 * repart donc file VIDE, et sa premiere instruction paye son fetch plein tarif -- soit
 * 4 cycles par OCTET. Le modele en octets predit alors que **le cout d'une interruption
 * depend de la LARGEUR des instructions dans lesquelles elle revient** :
 *
 *      revenir dans un flot de `nop` (1 octet)          -> ~4 cy de calage
 *      revenir dans un flot de `ld XWA,#imm32` (5 o.)   -> ~20 cy de calage
 *
 * ⇒ **~16 cycles d'ecart sur ~111**, soit 14 % : largement au-dessus du bruit de ces
 * ROM. Le credit en cycles, lui, ne predit presque rien (son avance est plafonnee en
 * cycles, pas en octets).
 *
 * ⛔ SI LE SILICIUM DONNE UN COUT PLAT, la piste « la file se vide au retour » est
 * REFUTEE, et avec elle la facon dont nous facturons TOUS les transferts de controle
 * pris -- pas seulement l'interruption. C'est un resultat negatif qui vaut le tir.
 *
 * ⚠️ CE QUI REND LA MESURE PROPRE. Chaque largeur est mesuree DEUX FOIS, interruptions
 * interdites (page 0) puis autorisees (page 1), avec le MEME lot de travail. Le cout
 * propre de la boucle -- qui, lui, depend evidemment de la largeur -- se SIMPLIFIE dans
 * la difference. Ce qu'on lit est le cout MARGINAL d'une interruption, largeur par
 * largeur.
 *
 *      cout par IRQ (largeur k) = (FEN/I_k - FEN/W_k) / (IRQ_PAR_FEN / I_k)
 *
 * ⚠️ Le gestionnaire est VIDE et IDENTIQUE aux quatre largeurs : il ne peut donc pas
 * expliquer une pente. Et le vecteur utilisateur Timer 0 (0x6FD4) est celui que le SDK
 * ecrit deja lui-meme dans `InitNGPC` -- pas de vecteur invente.
 *
 * ===========================================================================
 * PAGE 0 -- interruptions INTERDITES   W1 / W2 / W3 / W5  (les references)
 * PAGE 1 -- interruptions AUTORISEES   I1 / I2 / I3 / I5  (gestionnaire VIDE)
 * PAGE 2 -- RASV, la validite du tir
 * ===========================================================================
 * Les quatre largeurs, meme famille (charge immediate) sauf `nop` :
 *      1 -> nop                    (1 octet)
 *      2 -> ld A,0x01              (largeur a VERIFIER a l'octet apres compilation)
 *      3 -> ld WA,0x0101           (idem)
 *      5 -> ld XWA,0x01010101      (5 octets, deja verifie par la v18)
 * ⛔ Les largeurs annoncees ci-dessus sont des ATTENTES : elles doivent etre relues sur
 * la ROM construite (trace du coeur) avant tout depouillement. « Ca assemble » ne dit
 * rien des octets -- c'est un piege deja paye ailleurs sur ce projet.
 *
 * NAVIGATION : GAUCHE / DROITE. Numero de page en chiffre ligne 1.
 * ⛔ Pas de variable globale (cf. la v6). Ligne-cle "0123456789" en ligne 17.
 *
 * ===========================================================================
 * ⚠️ NOTE AJOUTEE APRES CONSTRUCTION (le binaire n'a PAS ete refait)
 * ===========================================================================
 * La prediction ci-dessus -- « le cout depend de la largeur du code de retour » -- a ete
 * REFUTEE en emulation avant tout tir : nos deux modeles rendent un cout PLAT sur 2/3/5
 * octets, une instruction large calant plus MAIS rechargeant plus. La ROM a donc ete mise
 * de cote.
 *
 * ⚡ ELLE DISCRIMINE POURTANT, SUR UN AUTRE AXE, ET C'EST LA QUESTION OUVERTE : la
 * RISTOURNE. Nos modeles laissent les cycles de l'ISR recharger la file du flot
 * interrompu, ce qui rend une interruption MOINS CHERE dans une boucle limitee par le bus
 * (`ld XWA`, 5 octets) que dans une boucle limitee par l'execution (`nop`, 1 octet).
 * Prediction : `nop` plus cher de +18,0 cy (credit) ou +9 a +12 (file en octets).
 * ⇒ Un cout PLAT au silicium refute la ristourne pour les deux modeles, et confirme du
 *   meme coup que notre sur-facturation (`data_access_cycles` sur un `PUSH (mem)`,
 *   `branch_taken_extra` sur un `reti`) est reelle -- l'annexe B donne 110 cy pour ce
 *   chemin, le silicium 111,1.
 * Le montage n'a pas bouge d'un octet : ce sont les DEUX COLONNES de la page 0 contre la
 * page 1 qui portent l'information, pas la pente 2->5.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * Depouillement : hw_calibration/v19_gate.py
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

#define OP1   __asm(" nop");
#define OP2   __asm(" ld A,0x01");
#define OP3   __asm(" ld WA,0x0101");
#define OP5   __asm(" ld XWA,0x01010101");

#define X4(m)  m m m m
#define X16(m) X4(m) X4(m) X4(m) X4(m)

/* --- le gestionnaire : VIDE, et le meme pour les quatre largeurs --------- */
void __interrupt h_empty(void) { }

/* ⛔ 60 tours, pas plus : un bloc doit rester TRES court devant une trame. La boucle
 * exterieure compte les trames en guettant `RAS_V` qui redescend ; si un bloc approche
 * la duree d'une trame, deux lectures enjambent une bascule et la trame n'est pas
 * comptee -- le montage compte faux SANS LE DIRE (piege rencontre sur la v17). */
u16 m_w1(void)
{
    u16 count; u8 frames; u8 prev, cur;
    count = 0; frames = 0; prev = RAS_V;
    while (frames < FRAMES) {
        __asm(" push BC"); __asm(" push XWA");
        __asm(" ld BC,60");
        __asm("W1L:");
        X16(OP1)
        __asm(" djnz BC,W1L");
        __asm(" pop XWA"); __asm(" pop BC");
        count++;
        cur = RAS_V; if (cur < prev) frames++; prev = cur;
    }
    return count;
}

u16 m_w2(void)
{
    u16 count; u8 frames; u8 prev, cur;
    count = 0; frames = 0; prev = RAS_V;
    while (frames < FRAMES) {
        __asm(" push BC"); __asm(" push XWA");
        __asm(" ld BC,60");
        __asm("W2L:");
        X16(OP2)
        __asm(" djnz BC,W2L");
        __asm(" pop XWA"); __asm(" pop BC");
        count++;
        cur = RAS_V; if (cur < prev) frames++; prev = cur;
    }
    return count;
}

u16 m_w3(void)
{
    u16 count; u8 frames; u8 prev, cur;
    count = 0; frames = 0; prev = RAS_V;
    while (frames < FRAMES) {
        __asm(" push BC"); __asm(" push XWA");
        __asm(" ld BC,60");
        __asm("W3L:");
        X16(OP3)
        __asm(" djnz BC,W3L");
        __asm(" pop XWA"); __asm(" pop BC");
        count++;
        cur = RAS_V; if (cur < prev) frames++; prev = cur;
    }
    return count;
}

u16 m_w5(void)
{
    u16 count; u8 frames; u8 prev, cur;
    count = 0; frames = 0; prev = RAS_V;
    while (frames < FRAMES) {
        __asm(" push BC"); __asm(" push XWA");
        __asm(" ld BC,60");
        __asm("W5L:");
        X16(OP5)
        __asm(" djnz BC,W5L");
        __asm(" pop XWA"); __asm(" pop BC");
        count++;
        cur = RAS_V; if (cur < prev) frames++; prev = cur;
    }
    return count;
}

void irq_off(void) { K_INTET10 = (u8)(K_INTET10 & 0xF8); }
void irq_on(void)  { K_INTET10 = (u8)((K_INTET10 & 0xF8) | 0x03); }

/* Timer 0 sur la broche externe TI0 (mode 00) : une impulsion par LIGNE. Repris tel
 * quel des ROM v8, v16 et v18, qui ont toutes tourne sur silicium. */
void timer_setup(void)
{
    TRUN     = (u8)(TRUN & 0xFE);
    K_T01MOD = (u8)(K_T01MOD & 0xFC);
    K_TREG0  = 1;
    TRUN     = (u8)(TRUN | 0x81);
}

u16 run_width(u8 width, u8 enable)
{
    u16 r;
    timer_setup();
    TI0_INT = h_empty;           /* le SDK ecrit deja ce vecteur dans InitNGPC */
    if (enable) irq_on(); else irq_off();
    if      (width == 1) r = m_w1();
    else if (width == 2) r = m_w2();
    else if (width == 3) r = m_w3();
    else                 r = m_w5();
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
        PrintString(P, PAL, 1, 1, "SANS IRQ p");
        PrintString(P, PAL, 1, 3, "W1 nop    :");
        PrintString(P, PAL, 1, 4, "W2 ld A   :");
        PrintString(P, PAL, 1, 5, "W3 ld WA  :");
        PrintString(P, PAL, 1, 6, "W5 ld XWA :");
    } else if (page == 1) {
        PrintString(P, PAL, 1, 1, "AVEC IRQ p");
        PrintString(P, PAL, 1, 3, "I1 nop    :");
        PrintString(P, PAL, 1, 4, "I2 ld A   :");
        PrintString(P, PAL, 1, 5, "I3 ld WA  :");
        PrintString(P, PAL, 1, 6, "I5 ld XWA :");
    } else {
        PrintString(P, PAL, 1, 1, "VALIDITE p");
        PrintString(P, PAL, 1, 3, "RASV      :");
        PrintString(P, PAL, 1, 5, "doit valoir 198");
        PrintString(P, PAL, 1, 6, "sinon tout est");
        PrintString(P, PAL, 1, 7, "a jeter.");
        PrintString(P, PAL, 1, 9, "note aussi le md5");
    }
    PrintDecimal(P, PAL, 11, 1, (u16)page, 1);
}

void main(void)
{
    u8 page, shown, i;
    u16 v;

    /* ⛔ REPRIS MOT POUR MOT DE LA v18 : `CpuSpeed(0)` force le gear 0 (6,144 MHz).
     * Sans lui la ROM mesure une AUTRE horloge que les jeux, et tous les chiffres sont
     * a jeter sans que rien ne le dise. `SysSetSystemFont` arme la fonte que la table
     * chiffre->tuile du depouillement suppose. */
    InitNGPC();
    SetBackgroundColour(RGB(2, 2, 4));
    SysSetSystemFont();
    SetPalette(P, PAL, 4, RGB(15, 15, 15), RGB(15, 15, 15), RGB(15, 15, 15));
    CpuSpeed(0);
    irq_off();
    page = 0; shown = 255;

    for (;;) {
        if (page != shown) { draw_labels(page); shown = page; }

        if (page == 0) {
            for (i = 0; i < 4; i++) {
                v = run_width((u8)(i == 3 ? 5 : i + 1), 0);
                PrintDecimal(P, PAL, 12, (u8)(3 + i), v, 5);
            }
        } else if (page == 1) {
            for (i = 0; i < 4; i++) {
                v = run_width((u8)(i == 3 ? 5 : i + 1), 1);
                PrintDecimal(P, PAL, 12, (u8)(3 + i), v, 5);
            }
        } else {
            PrintDecimal(P, PAL, 12, 3, (u16)rasv_max(), 5);
        }
        page = pad_page(page);
    }
}
