/* ============================================================================
 * NGPC CALIBRATION ROM  (v11, 2026-08-24) -- SEPARER LE COUT FIXE DU COUT PAR MOT
 *
 * CE QUE LA v10 A ETABLI. Deux boucles du meme tir, `BASE` (5 mots) a 109,4 cycles
 * par iteration et `REF` (14 mots) a 117,8, donnent :
 *      cout = 104,7 cycles FIXES par iteration + 0,93 cycle par mot lu
 * ⇒ sur cette console une iteration coute ~105 cycles fixes et la LECTURE des
 * instructions est quasi gratuite. Notre modele dit l'inverse (8,25 par mot, aucun
 * cout fixe) : il tombe juste sur `REF` PAR COMPENSATION et s'effondre sur `BASE`.
 *
 * CE QUE DEUX POINTS NE PEUVENT PAS DIRE. (1) Si la relation est vraiment lineaire.
 * (2) Ce que sont ces 105 cycles : ils melangent l'execution de `inc`+`cp`+`jr` et
 * la penalite de branche prise, et rien ne les separe.
 *
 * CE QUE CELLE-CI FAIT. **Quatre** tailles de corps, meme structure de boucle,
 * meme compteur, meme branche -- seule la quantite de travail registre change :
 *      L1 : un transfert          (le plus court)
 *      L2 : le corps de la v8     (le point de reference deja tire deux fois)
 *      L3 : ce corps x3
 *      L4 : ce corps x6           (le plus long)
 *
 * ⇒ quatre points sur une droite. La pente donne le cout PAR MOT, l'ordonnee a
 * l'origine le cout FIXE d'une iteration -- et l'alignement des quatre dit si le
 * modele lineaire tient. S'ils ne s'alignent pas, la forme est encore autre chose,
 * et c'est ca qu'il faudra savoir avant de toucher a la moindre constante.
 *
 * ⚠️ LES TAILLES REELLES EN MOTS SE LISENT DANS LE DESASSEMBLAGE, PAS ICI : le
 * compilateur decide. Elles sont relevees apres construction et notees dans le
 * README a cote des mesures -- une taille supposee invaliderait toute la droite.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define REPS   200
#define FRAMES 60

/* Le corps qui sert d'unite : chaque ligne depend de la precedente, donc le
 * compilateur ne peut ni les fusionner ni les supprimer. */
#define BLOC   v = v + w; w = w ^ v; v = v << 1; w = w + 1;

#define BOUCLE(NAME, CORPS)                                  \
u16 NAME(void) {                                             \
    u16 count; u8 frames; u8 prev, cur; u16 i;               \
    volatile u16 v; volatile u16 w;                          \
    count = 0; frames = 0; v = 1; w = 3;                     \
    prev = RAS_V;                                            \
    while (frames < FRAMES) {                                \
        for (i = 0; i < REPS; i++) { CORPS }                 \
        count++;                                             \
        cur = RAS_V;                                         \
        if (cur < prev) frames++;                            \
        prev = cur;                                          \
    }                                                        \
    return count;                                            \
}

BOUCLE(m_l1, v = w;)
BOUCLE(m_l2, BLOC)
BOUCLE(m_l3, BLOC BLOC BLOC)
BOUCLE(m_l4, BLOC BLOC BLOC BLOC BLOC BLOC)

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
    InitNGPC();
    SetBackgroundColour(RGB(2, 2, 4));
    SysSetSystemFont();
    SetPalette(P, PAL, 4, RGB(15, 15, 15), RGB(15, 15, 15), RGB(15, 15, 15));
    CpuSpeed(0);

    PrintString(P, PAL, 1, 1,  "DROITE v11 /60f");
    PrintString(P, PAL, 1, 3,  "L1   :");
    PrintString(P, PAL, 1, 4,  "L2   :");
    PrintString(P, PAL, 1, 5,  "L3   :");
    PrintString(P, PAL, 1, 6,  "L4   :");
    PrintString(P, PAL, 1, 8,  "RASV :");
    PrintString(P, PAL, 1, 10, "note les 5 nombres");
    PrintString(P, PAL, 1, 11, "et le md5 de la rom");

    while (1) {
        PrintDecimal(P, PAL, 12, 3, m_l1(),     5);
        PrintDecimal(P, PAL, 12, 4, m_l2(),     5);
        PrintDecimal(P, PAL, 12, 5, m_l3(),     5);
        PrintDecimal(P, PAL, 12, 6, m_l4(),     5);
        PrintDecimal(P, PAL, 12, 8, rasv_max(), 3);
    }
}
