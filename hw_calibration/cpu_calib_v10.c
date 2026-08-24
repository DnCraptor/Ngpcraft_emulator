/* ============================================================================
 * NGPC CALIBRATION ROM  (v10, 2026-08-24) -- LE PONT ENTRE DEUX CAMPAGNES
 *
 * POURQUOI CELLE-CI EXISTE. Les classes d'instructions mesurees par `cpu_calib_v3`
 * (campagne de JUILLET) donnent aujourd'hui BASE +4,3 % · ADD +6,2 % · MUL +6,1 % ·
 * DIV +6,8 % · CSEQ +3,7 % · CRND +3,6 % · RRND +4,4 % -- **sept classes du meme
 * cote**. Tentant d'y voir un biais du modele.
 *
 * ⛔ MAIS LE MODELE EST CALE SUR LE TIR v8 D'AOUT, ET LES CLASSES SUR UNE CAMPAGNE
 * DE JUILLET : autre ROM, autre session. Soustraire deux tirs qui ne sont pas
 * comparables est une faute que ce projet a deja payee une passe entiere (voir
 * OPEN_ITEMS §4bis, « une reference silicium n'est pas datable »). Le +5 % peut
 * donc etre un biais du modele **ou** l'ecart entre deux campagnes -- et rien dans
 * les donnees existantes ne permet de trancher.
 *
 * CE QUE CELLE-CI FAIT, ET C'EST TOUT SON INTERET. Elle mesure **dans LE MEME TIR** :
 *   REF   : la boucle de travail EXACTE de la ROM v8 -- le pont
 *   BASE / SHIFT / ADD / MUL / DIV / MEM : les classes de la v3
 *
 * ⇒ LECTURE. Le modele donne REF = 260 et le silicium v8 a donne 261.
 *   - si `REF` retombe vers **261** et que les classes restent hautes, le biais est
 *     REEL et il est dans le modele ;
 *   - si `REF` sort lui aussi ~5 % au-dessus de 261, alors ce n'est pas le modele :
 *     c'est l'ecart entre la campagne de juillet et celle d'aout, et le « biais »
 *     n'a jamais existe.
 * Dans les deux cas la question est tranchee par UN tir, sans rien soustraire entre
 * deux dates.
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define REPS   200
#define FRAMES 60

/* ⚠️ REPRISE MOT POUR MOT DE LA v8. Si ce corps de boucle differe, meme d'une
 * instruction, le pont ne relie plus rien -- c'est la SEULE chose qui donne son
 * sens a cette ROM. */
u16 m_ref(void)
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
        if (cur < prev) frames++;
        prev = cur;
    }
    return count;
}

/* Les classes, reprises telles quelles de la v3 pour rester comparables a sa
 * campagne : changer le corps changerait ce qu'on compare. */
#define MEASURE(NAME, OP)                                    \
u16 NAME(void) {                                             \
    u16 count; u8 frames; u8 prev, cur; u16 i;               \
    volatile u16 v; volatile u16 w;                          \
    count = 0; frames = 0; v = 1; w = 3;                     \
    prev = RAS_V;                                            \
    while (frames < FRAMES) {                                \
        for (i = 0; i < REPS; i++) { OP; }                   \
        count++;                                             \
        cur = RAS_V;                                         \
        if (cur < prev) frames++;                            \
        prev = cur;                                          \
    }                                                        \
    return count;                                            \
}

MEASURE(m_base,  v = w)
MEASURE(m_shift, v = w << 5)
MEASURE(m_add,   v = v + w)
MEASURE(m_mul,   v = v * w)
MEASURE(m_div,   v = w / (v | 1))
MEASURE(m_mem,   *(volatile u8 *)0x4200 = (u8)v)

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

    PrintString(P, PAL, 1, 1,  "PONT CALIB v10/60f");
    PrintString(P, PAL, 1, 3,  "REF  :");
    PrintString(P, PAL, 1, 4,  "BASE :");
    PrintString(P, PAL, 1, 5,  "SHIFT:");
    PrintString(P, PAL, 1, 6,  "ADD  :");
    PrintString(P, PAL, 1, 7,  "MUL  :");
    PrintString(P, PAL, 1, 8,  "DIV  :");
    PrintString(P, PAL, 1, 9,  "MEM  :");
    PrintString(P, PAL, 1, 11, "RASV :");
    PrintString(P, PAL, 1, 13, "note les 8 nombres");
    PrintString(P, PAL, 1, 14, "et le md5 de la rom");

    while (1) {
        PrintDecimal(P, PAL, 12, 3,  m_ref(),    5);
        PrintDecimal(P, PAL, 12, 4,  m_base(),   5);
        PrintDecimal(P, PAL, 12, 5,  m_shift(),  5);
        PrintDecimal(P, PAL, 12, 6,  m_add(),    5);
        PrintDecimal(P, PAL, 12, 7,  m_mul(),    5);
        PrintDecimal(P, PAL, 12, 8,  m_div(),    5);
        PrintDecimal(P, PAL, 12, 9,  m_mem(),    5);
        PrintDecimal(P, PAL, 12, 11, rasv_max(), 3);
    }
}
