/* ============================================================================
 * NGPC CALIBRATION ROM  (v14, 2026-08-27) -- CINQ PAGES DE MESURES
 *
 * Suite de la v13, qui a etabli qu'une branche prise coute ~+6 cycles de plus que
 * sa ligne de table mais n'a pas dit POURQUOI c'est ce chiffre-la. Cette ROM prend
 * en une fois tout ce qui restait ouvert dans hw_calibration/README.md.
 *
 * NAVIGATION : GAUCHE / DROITE change de page. Chaque page se re-mesure en boucle ;
 * les nombres se stabilisent en quelques secondes. Le pad est relu apres CHAQUE
 * nombre, donc le changement de page repond en ~1 s.
 *
 * ⛔ PAS DE VARIABLE GLOBALE, comme la v13 : la v6 bootait chez nous et PLANTAIT la
 * console, seuls suspects les buffers globaux et `ld xhl,_symbol`. Chaque page
 * affiche des valeurs vivantes, rien n'est stocke.
 *
 * ===========================================================================
 * PAGE 0 -- LE VIDAGE DE FILE, PAR TROIS ROTATIONS DU MEME CORPS
 * ===========================================================================
 * ⚡ LE MONTAGE LE PLUS PROPRE DE TOUTE LA SERIE. L'unite de corps fait 12 octets
 * et trois instructions, TOUJOURS LES MEMES :
 *      ld XWA,#imm32   5 o. /  5 etats -- fetch-bound, CONSOMME le credit d'avance
 *      ld XHL,#imm32   5 o. /  5 etats -- idem
 *      mul WA,E        2 o. / ~14 etats -- execute-bound, RECONSTRUIT le credit
 * Les trois pages A / B / C sont les trois ROTATIONS de cette unite :
 *
 *      A : ld ld mul   la branche tombe juste apres la `mul`   credit ~16 cy
 *      B : ld mul ld   une charge a deja consomme              credit  ~5 cy
 *      C : mul ld ld   deux charges ont tout consomme          credit  ~0 cy
 *
 * ⇒ MEMES OCTETS, MEME TRAVAIL, MEME NOMBRE DE BRANCHES. La seule chose qui change
 * est le credit que la file a EN MAIN au moment de la branche. Aucune constante de
 * COUT D'INSTRUCTION ne peut produire un ecart entre A, B et C, puisque les trois
 * executent exactement les memes instructions : tout ecart vient de la machinerie
 * de credit de la file.
 *
 * ⚠️ MAIS UN ECART N'EST PAS A LUI SEUL LA PREUVE D'UN VIDAGE. Notre modele, vidage
 * DESARME, rend deja A1=141 B1=131 C1=141 : la SATURATION du credit (le plafond de
 * `biu_slack`) depend de l'ordre des instructions, donc les rotations se separent
 * meme sans vidage. C'est ce qui rend la page utile -- elle sonde la machinerie de
 * credit dans son ensemble -- mais il faut comparer le MOTIF des cinq nombres a
 * chaque modele candidat, jamais conclure d'un seul ecart.
 *
 * A1/B1/C1 : 640 unites, UNE branche par unite (640 branches).
 * A8/C8    : 640 unites, HUIT unites par branche (80 branches) -- le controle
 *            d'echelle : l'ecart A-C doit se diviser par ~8.
 *
 * LECTURE. Le depouillement (`v14_gate.py`) compare le MOTIF complet des cinq
 * nombres a chaque modele candidat et rend celui qui le reproduit. A la main, les
 * deux reperes qui portent le plus :
 *   A1 - C1  : le cout du credit perdu quand la branche tombe file pleine plutot
 *              que file vide. Zero chez nous vidage desarme.
 *   A8 - C8  : le meme, avec huit fois moins de branches. Si l'effet est bien
 *              PAR BRANCHE, (A1-C1) doit valoir ~8 x (A8-C8).
 *
 * ===========================================================================
 * PAGE 1 -- LE COUT D'UN OCTET LU, EN DIRECT
 * ===========================================================================
 * `fetch_wait_q4 = 33` (8,25 cy/mot) n'a jamais ete mesure directement : il a ete
 * AJUSTE pour encadrer la ROM v8, donc il porte les erreurs de tout ce qui
 * l'entoure. Ici, des chaines de `ld XWA,#imm32` : 5 octets pour 5 etats, soit
 * ~20,6 cy de bus contre 10 cy d'execution -- deux fois plus de bus que de calcul,
 * la boucle est franchement limitee par le bus. Le cout par tour est alors
 * proportionnel aux OCTETS LUS, et la pente contre k donne le cout d'un octet.
 * 12, 16, 20 et 24 charges par tour. ⛔ DEUX BORNES ENCADRENT CE CHOIX, et aucune
 * n'est esthetique : en dessous de ~10 charges le credit d'avance absorbe les
 * increments et les quatre points ne ferment plus une droite (la premiere version,
 * k = 4..16, donnait 3,2 % d'ecart dans notre propre modele) ; au-dessus de ~24 le
 * corps depasse 125 octets et le DEPLACEMENT 8 BITS du `djnz` ne porte plus --
 * asm900 refuse avec « Out of range for relative reference ».
 *
 * ===========================================================================
 * PAGE 2 -- LE COUT D'UNE LECTURE MEMOIRE
 * ===========================================================================
 * `MEM` est le PIRE ecart du corpus : +12,1 % sur v2 comme sur v10, quand tout le
 * reste est a +5 %. Deux fois l'erreur commune, donc quelque chose lui est propre.
 * Corps = k x `ld XWA,(XHL)` avec XHL en RAM ; la pente contre k donne le cout d'une
 * lecture, degage de l'enveloppe de boucle qui polluait la mesure de v2.
 * ⛔ LECTURE 32 BITS, PAS 8. `ld A,(XHL)` fait 2 octets pour 4 etats, soit 8 cy
 * d'execution contre 8,25 de bus : pile a l'equilibre, si bien que la boucle oscille
 * autour du seuil de calage et que les quatre points ne ferment pas une droite
 * (5,3 % d'ecart dans notre propre modele). La forme longue est franchement dominee
 * par la memoire et sort de ce regime.
 * M1/M2/M4/M8 = 4, 8, 12 et 16 lectures par tour, meme raison qu'en page 1.
 *
 * ===========================================================================
 * PAGES 3 et 4 -- DIV ET MUL, MESUREES L'UNE CONTRE L'AUTRE
 * ===========================================================================
 * `DIV` word = 56 cy a ete cale a l'epoque ou l'attente de fetch valait 10 cy/mot ;
 * le tir v8 a ensuite epingle celle-ci a 8,25, et un nombre cale par-dessus un
 * nombre faux ne survit pas a la correction du second. Meme unite pour les deux
 * pages -- `ld WA,#imm16` puis l'operation -- si bien que la charge se SIMPLIFIE
 * dans la difference DIV - MUL : cette difference est le surcout de la division
 * sur la multiplication, sans hypothese sur le cout de la charge.
 *
 * ===========================================================================
 * ENCODAGES -- RELEVES AU DESASSEMBLAGE APRES CONSTRUCTION, JAMAIS SUPPOSES
 * ===========================================================================
 *   40 xx xx xx xx  ld XWA,#imm32   | 43 xx xx xx xx  ld XHL,#imm32
 *   CD 41  mul WA,E                 | CD 51  div WA,E
 *   D9 1C dd  djnz BC,dd            | 31 xx xx  ld BC,#
 * `D9 1C` est la forme registre-direct de la famille D8..DF, HW-prouvee (boucle OAM
 * de Ganbare) -- PAS le mis-encode `D0 1C`.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * Depouillement : hw_calibration/v14_gate.py
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define FRAMES 60

/* --- briques ---------------------------------------------------------- */
#define LDW  __asm(" ld XWA,0x01010101");
#define LDH  __asm(" ld XHL,0x02020202");
#define MULE __asm(" mul WA,E");
#define DIVE __asm(" div WA,E");
#define LDW16 __asm(" ld WA,0x0101");
#define RD   __asm(" ld XWA,(XHL)");

/* --- page 0 : les trois rotations ------------------------------------- */
#define UA1  LDW LDH MULE
#define UB1  LDW MULE LDH
#define UC1  MULE LDW LDH
#define UA8  UA1 UA1 UA1 UA1 UA1 UA1 UA1 UA1
#define UC8  UC1 UC1 UC1 UC1 UC1 UC1 UC1 UC1

/* --- page 1 : chaines fetch-bound ------------------------------------- */
/* ⛔ LES POINTS COMMENCENT A 8, PAS A 1. Avec k petit, le credit d'avance de la file
 * absorbe les premieres charges et le cout par unite n'est PAS constant : la premiere
 * version (k = 4, 8, 12, 16) ne fermait pas une droite a mieux que 3,2 % dans notre
 * propre modele, et une pente lue sur des points courbes ne vaut rien. */
#define F4_  LDW LDW LDW LDW
#define F12_ F4_ F4_ F4_
#define F16_ F12_ F4_
#define F20_ F16_ F4_
#define F24_ F20_ F4_

/* --- page 2 : lectures memoire ---------------------------------------- */
/* ⛔ Meme raison qu'en page 1 : k = 1, 2, 4, 8 donnait 11,3 % d'ecart a la droite,
 * l'increment M1->M2 ne coutant que 3 cy quand M4->M8 en coute 6,9. Hors du regime
 * ou le credit absorbe, les points s'alignent. */
#define M4_  RD RD RD RD
#define M8_  M4_ M4_
#define M12_ M8_ M4_
#define M16_ M8_ M8_

/* --- pages 3/4 : div et mul, meme enveloppe --------------------------- */
#define D1   LDW16 DIVE
#define D2   D1 D1
#define D4   D2 D2
#define D8   D4 D4
#define P1   LDW16 MULE
#define P2   P1 P1
#define P4   P2 P2
#define P8   P4 P4

/* push/pop encadrent le bloc : le C tient ses variables dans ces memes registres,
 * et l'asm les ecrase. XHL est arme sur la RAM pour la page 2 ; les autres pages
 * l'ecrasent sans le lire, ce qui est sans effet. */
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
        __asm(" ld XHL,0x00004000");                              \
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

BLOC(m_a1, 640, UA1)
BLOC(m_b1, 640, UB1)
BLOC(m_c1, 640, UC1)
BLOC(m_a8,  80, UA8)
BLOC(m_c8,  80, UC8)

BLOC(m_f4,  100, F12_)
BLOC(m_f8,  100, F16_)
BLOC(m_f12, 100, F20_)
BLOC(m_f16, 100, F24_)

BLOC(m_m1, 250, M4_)
BLOC(m_m2, 250, M8_)
BLOC(m_m4, 250, M12_)
BLOC(m_m8, 250, M16_)

BLOC(m_d1, 200, D1)
BLOC(m_d2, 200, D2)
BLOC(m_d4, 200, D4)
BLOC(m_d8, 200, D8)

BLOC(m_p1, 200, P1)
BLOC(m_p2, 200, P2)
BLOC(m_p4, 200, P4)
BLOC(m_p8, 200, P8)

/* Validite du tir : RAS.V doit plafonner a 198. Toute autre valeur = la trame n'est
 * pas celle qu'on croit et AUCUN nombre de cette ROM ne veut rien dire. */
u8 rasv_max(void)
{
    u8 mx; u8 r; u16 s;
    mx = 0;
    for (s = 0; s < 40000; s++) { r = RAS_V; if (r > mx && r < 250) mx = r; }
    return mx;
}

#define PAL 0
#define P   SCR_1_PLANE
#define NPAGES 6

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
    /* Cle de decodage pour le banc emulateur : il lit la tuile de chaque chiffre
     * ici et s'en sert pour relire les nombres, sans variable globale. */
    PrintString(P, PAL, 1, 17, "0123456789");
    PrintString(P, PAL, 1, 15, "GAUCHE/DROITE=page");

    if (page == 0) {
        PrintString(P, PAL, 1, 1, "VIDAGE   p");
        PrintString(P, PAL, 1, 3, "A1 ldldmul:");
        PrintString(P, PAL, 1, 4, "B1 ldmulld:");
        PrintString(P, PAL, 1, 5, "C1 mulldld:");
        PrintString(P, PAL, 1, 6, "A8 x8     :");
        PrintString(P, PAL, 1, 7, "C8 x8     :");
    } else if (page == 1) {
        PrintString(P, PAL, 1, 1, "FETCH    p");
        PrintString(P, PAL, 1, 3, "F 12 ld   :");
        PrintString(P, PAL, 1, 4, "F 16 ld   :");
        PrintString(P, PAL, 1, 5, "F 20 ld   :");
        PrintString(P, PAL, 1, 6, "F 24 ld   :");
    } else if (page == 2) {
        PrintString(P, PAL, 1, 1, "MEM RAM  p");
        PrintString(P, PAL, 1, 3, "M  4 rd   :");
        PrintString(P, PAL, 1, 4, "M  8 rd   :");
        PrintString(P, PAL, 1, 5, "M 12 rd   :");
        PrintString(P, PAL, 1, 6, "M 16 rd   :");
    } else if (page == 3) {
        PrintString(P, PAL, 1, 1, "DIV      p");
        PrintString(P, PAL, 1, 3, "D1  1 div :");
        PrintString(P, PAL, 1, 4, "D2  2 div :");
        PrintString(P, PAL, 1, 5, "D4  4 div :");
        PrintString(P, PAL, 1, 6, "D8  8 div :");
    } else if (page == 4) {
        PrintString(P, PAL, 1, 1, "MUL      p");
        PrintString(P, PAL, 1, 3, "P1  1 mul :");
        PrintString(P, PAL, 1, 4, "P2  2 mul :");
        PrintString(P, PAL, 1, 5, "P4  4 mul :");
        PrintString(P, PAL, 1, 6, "P8  8 mul :");
    } else {
        PrintString(P, PAL, 1, 1, "VALIDITE p");
        PrintString(P, PAL, 1, 3, "RASV      :");
        PrintString(P, PAL, 1, 5, "doit valoir 198");
        PrintString(P, PAL, 1, 6, "sinon tout est");
        PrintString(P, PAL, 1, 7, "a jeter.");
        PrintString(P, PAL, 1, 9, "note aussi le md5");
        PrintString(P, PAL, 1, 10, "de la rom.");
    }
}

/* Une mesure, puis on rend la main au pad : changer de page repond en ~1 s. */
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
        /* Le numero de page, en CHIFFRE : c'est ce qui rend la navigation du banc
         * emulateur deterministe. Sans lui, un appui maintenu franchit plusieurs
         * pages (le pad n'est relu qu'entre deux mesures, soit toutes les ~60
         * trames) et le banc lit les nombres d'une autre page sans le savoir. */
        PrintDecimal(P, PAL, 11, 1, page, 1);
        switch (page) {
        case 0:
            do { STEP(3, m_a1) STEP(4, m_b1) STEP(5, m_c1)
                 STEP(6, m_a8) STEP(7, m_c8) } while (0);
            break;
        case 1:
            do { STEP(3, m_f4) STEP(4, m_f8) STEP(5, m_f12)
                 STEP(6, m_f16) } while (0);
            break;
        case 2:
            do { STEP(3, m_m1) STEP(4, m_m2) STEP(5, m_m4)
                 STEP(6, m_m8) } while (0);
            break;
        case 3:
            do { STEP(3, m_d1) STEP(4, m_d2) STEP(5, m_d4)
                 STEP(6, m_d8) } while (0);
            break;
        case 4:
            do { STEP(3, m_p1) STEP(4, m_p2) STEP(5, m_p4)
                 STEP(6, m_p8) } while (0);
            break;
        default:
            do { STEP(3, rasv_max) } while (0);
            break;
        }
    }
}
