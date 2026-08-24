/* ============================================================================
 * NGPC CALIBRATION ROM  (v12, 2026-08-24) -- LE MEME CODE A QUATRE ADRESSES
 *
 * LE FAIT A EXPLIQUER. Les ROM v10 et v11 contiennent la MEME boucle, octet pour
 * octet -- `ld WA,BC` / `inc 1,DE` / `cp DE,0x00C8` / `jr C`, dix octets --, et le
 * silicium a lu **281** dans l'une et **678** dans l'autre. Facteur 2,4. La seule
 * chose qui differe est l'ADRESSE : 0x2016C0 contre 0x20165E. Notre modele, lui,
 * a lu 715 et 720 : il est **aveugle a l'adresse**.
 *
 * ⚠️ CE QUI N'EST PAS AFFIRME ICI. Que la cause soit l'alignement sur la file de
 * 4 octets. C'est plausible -- 0x2016C0 est aligne sur 4, 0x20165E ne l'est pas --
 * mais deux fois aujourd'hui une cause nommee trop vite s'est revelee fausse. Cette
 * ROM ne suppose rien : elle place la MEME boucle a quatre adresses differentes et
 * laisse les quatre nombres parler.
 *
 * COMMENT LES ADRESSES SONT DECALEES. Par des fonctions de bourrage de tailles
 * differentes intercalees entre les mesures. Les adresses REELLES et leur reste
 * modulo 4 sont relevees au desassemblage APRES construction et notees dans le
 * README -- jamais supposees.
 *
 * LECTURE. Quatre nombres egaux ⇒ l'adresse n'y est pour rien, et l'ecart v10/v11
 * vient d'ailleurs (il faudra alors chercher ce qui differe VRAIMENT entre les deux
 * ROMs). Quatre nombres qui se groupent par reste modulo 4 ⇒ c'est l'alignement, et
 * l'ampleur se lit directement.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define REPS   200
#define FRAMES 60

#define BOUCLE(NAME)                                         \
u16 NAME(void) {                                             \
    u16 count; u8 frames; u8 prev, cur; u16 i;               \
    volatile u16 v; volatile u16 w;                          \
    count = 0; frames = 0; v = 1; w = 3;                     \
    prev = RAS_V;                                            \
    while (frames < FRAMES) {                                \
        for (i = 0; i < REPS; i++) { v = w; }                \
        count++;                                             \
        cur = RAS_V;                                         \
        if (cur < prev) frames++;                            \
        prev = cur;                                          \
    }                                                        \
    return count;                                            \
}

/* Bourrage : chaque `nop` fait un octet, donc chaque fonction decale la suivante
 * d'un cran different. Ce sont ces decalages qui produisent les quatre adresses. */
void pad1(void) { __asm(" nop"); }
void pad2(void) { __asm(" nop"); __asm(" nop"); }
void pad3(void) { __asm(" nop"); __asm(" nop"); __asm(" nop"); }

BOUCLE(m_a1)
void filler1(void) { __asm(" nop"); }
BOUCLE(m_a2)
void filler2(void) { __asm(" nop"); __asm(" nop"); }
BOUCLE(m_a3)
void filler3(void) { __asm(" nop"); __asm(" nop"); __asm(" nop"); }
BOUCLE(m_a4)

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

    /* garder les bourrages : sans appel, l'editeur de liens peut les retirer et
     * les decalages disparaitraient avec eux. */
    pad1(); pad2(); pad3();

    PrintString(P, PAL, 1, 1,  "ALIGN v12 /60f");
    PrintString(P, PAL, 1, 3,  "A1   :");
    PrintString(P, PAL, 1, 4,  "A2   :");
    PrintString(P, PAL, 1, 5,  "A3   :");
    PrintString(P, PAL, 1, 6,  "A4   :");
    PrintString(P, PAL, 1, 8,  "RASV :");
    PrintString(P, PAL, 1, 10, "note les 5 nombres");
    PrintString(P, PAL, 1, 11, "et le md5 de la rom");

    while (1) {
        PrintDecimal(P, PAL, 12, 3, m_a1(),     5);
        PrintDecimal(P, PAL, 12, 4, m_a2(),     5);
        PrintDecimal(P, PAL, 12, 5, m_a3(),     5);
        PrintDecimal(P, PAL, 12, 6, m_a4(),     5);
        PrintDecimal(P, PAL, 12, 8, rasv_max(), 3);
    }
}
