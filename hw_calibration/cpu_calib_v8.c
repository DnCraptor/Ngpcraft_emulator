/* ============================================================================
 * NGPC CALIBRATION ROM  (v8, 2026-08-23) -- LE COUT D'UNE INTERRUPTION
 *
 * POURQUOI. Toutes les ROM precedentes mesurent du code qui tourne SANS
 * interruptions. Or le defaut qui reste ouvert est un split rasteur : Cool
 * Boarders coupe INTT0 (niveau a 0 dans 0x0073), fait son travail, le
 * re-autorise -- et la ligne ou son split tombe depend du DEBIT dans cette
 * fenetre, interruptions comprises. Notre modele facture l'entree en
 * interruption 36 cycles (18 etats x 2) la ou l'ancien n'en compte que 18.
 * PERSONNE N'A MESURE CE NOMBRE SUR SILICIUM.
 *
 * CE QUE CA MESURE. Le meme lot de travail, trois fois :
 *   WORK0 : INTT0 INTERDIT (niveau 0)                    -> reference
 *   WORK1 : INTT0 autorise, TREG0=1  -> une IRQ par LIGNE (~152/trame)
 *   WORK4 : INTT0 autorise, TREG0=4  -> une IRQ toutes les 4 lignes (~38)
 *
 * Timer 0 est en mode 00 = BROCHE EXTERNE : il compte les impulsions H-blank
 * du K2GE, pas une horloge interne. C'est exactement le montage de Cool
 * Boarders.
 *
 * ⚠️ AUCUN GESTIONNAIRE N'EST INSTALLE, ET C'EST VOULU. Deviner le vecteur
 * utilisateur du BIOS ajouterait une inconnue a une mesure qui n'en veut pas.
 * Le stub du BIOS fait ce qu'il fait -- le meme sur console et sur emulateur --
 * et c'est ce que Cool Boarders paie aussi. On mesure le COUT TOTAL d'une
 * interruption prise, pas celui d'un handler choisi.
 *
 * COMMENT LIRE. Chaque ligne = nombre de lots finis en 60 trames (~1 s).
 *   WORK0 doit matcher l'emulateur : c'est le controle, deja couvert par v1/v2.
 *   (WORK0 - WORK1) / (WORK0 - WORK4) doit valoir ~4 sur les deux machines :
 *     sinon le cout n'est pas lineaire en nombre d'interruptions et le modele
 *     est faux dans sa FORME, pas seulement dans sa valeur.
 *   Le rapport (WORK0 - WORK1) silicium / emulateur donne DIRECTEMENT le
 *     facteur d'erreur sur le cout d'une interruption.
 *   LINE = controle : changements de ligne vus en 60 trames, attendu ~11940
 *     (199 x 60). Il valide que la boucle de scrutation suit le rasteur.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V   (*(volatile u8 *)0x8009)
/* TRUN est deja defini par ngpc.h (sans volatile) : on garde le sien. */
#define K_TREG0 (*(volatile u8 *)0x0022)
#define K_T01MOD (*(volatile u8 *)0x0024)
#define K_INTET10 (*(volatile u8 *)0x0073)

#define REPS   200
#define FRAMES 60

/* Un lot de travail volontairement BANAL : additions et decalages sur des
 * variables volatiles, donc ni optimise ni domine par une seule classe
 * d'instruction. Ce qu'on compare n'est pas sa valeur absolue mais ce que les
 * interruptions lui retirent. */
u16 work_batches(void)
{
    u16 count; u8 frames; u8 prev; u8 cur; u16 i;
    volatile u16 v; volatile u16 w;

    count = 0; frames = 0; v = 1; w = 3;
    prev = RAS_V;
    while (frames < FRAMES) {
        for (i = 0; i < REPS; i++) {
            v = v + w;
            w = w ^ v;
            v = v << 1;
            w = w + 1;
        }
        count++;
        cur = RAS_V;
        if (cur < prev) frames++;      /* le compteur de ligne a reboucle */
        prev = cur;
    }
    return count;
}

/* ⚠️ CECI NE COMPTE PAS LES INTERRUPTIONS. Sans gestionnaire installe on ne peut
 * pas les compter, et j'avais d'abord etiquete cette mesure « IRQN » -- ce qui
 * aurait fait lire un nombre pour un autre. Elle compte les CHANGEMENTS DE LIGNE
 * vus par le CPU en 60 trames : un controle de la longueur de trame, attendu
 * ~11940 (199 x 60), redondant avec RASV mais pris par un autre chemin. Un
 * chiffre nettement plus BAS voudrait dire que le CPU rate des lignes en les
 * scrutant, donc que la boucle de mesure elle-meme est trop lente. */
u16 lines_seen(void)
{
    u16 count; u8 frames; u8 prev; u8 cur;
    count = 0; frames = 0;
    prev = RAS_V;
    while (frames < FRAMES) {
        cur = RAS_V;
        if (cur != prev) {
            count++;                   /* une ligne de plus = une impulsion TI0 */
            if (cur < prev) frames++;
        }
        prev = cur;
    }
    return count;
}

void irq_off(void)  { K_INTET10 = (u8)(K_INTET10 & 0xF8); }
void irq_on(void)   { K_INTET10 = (u8)((K_INTET10 & 0xF8) | 0x03); }

void timer_setup(u8 period)
{
    TRUN   = (u8)(TRUN & 0xFE);        /* timer 0 a l'arret pendant le reglage */
    K_T01MOD = (u8)(K_T01MOD & 0xFC);      /* mode 00 = broche externe TI0 */
    K_TREG0  = period;
    TRUN   = (u8)(TRUN | 0x81);        /* prediviseur + timer 0 en marche */
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

void main(void)
{
    u16 w0; u16 w1; u16 w4; u16 n;

    InitNGPC();
    SetBackgroundColour(RGB(2, 2, 4));
    SysSetSystemFont();
    SetPalette(P, PAL, 4, RGB(15, 15, 15), RGB(15, 15, 15), RGB(15, 15, 15));
    CpuSpeed(0);

    PrintString(P, PAL, 1, 1,  "IRQ CALIB v8 /60f");
    PrintString(P, PAL, 1, 3,  "WORK0:");
    PrintString(P, PAL, 1, 4,  "WORK1:");
    PrintString(P, PAL, 1, 5,  "WORK4:");
    PrintString(P, PAL, 1, 7,  "LINE :");
    PrintString(P, PAL, 1, 9,  "RASV :");
    PrintString(P, PAL, 1, 11, "note les 5 nombres");
    PrintString(P, PAL, 1, 12, "et le md5 de la rom");

    while (1) {
        irq_off();
        timer_setup(1);
        w0 = work_batches();
        PrintDecimal(P, PAL, 12, 3, w0, 5);

        irq_on();
        timer_setup(1);
        w1 = work_batches();
        irq_off();
        PrintDecimal(P, PAL, 12, 4, w1, 5);

        irq_on();
        timer_setup(4);
        w4 = work_batches();
        irq_off();
        PrintDecimal(P, PAL, 12, 5, w4, 5);

        timer_setup(1);
        n = lines_seen();
        PrintDecimal(P, PAL, 12, 7, n, 5);

        PrintDecimal(P, PAL, 12, 9, rasv_max(), 3);
    }
}
