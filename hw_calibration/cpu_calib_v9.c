/* ============================================================================
 * NGPC CALIBRATION ROM  (v9, 2026-08-24) -- LE COUT D'UN TRANSFERT MICRO-DMA
 *
 * POURQUOI. Notre `micro_dma_service` facture ZERO. La datasheet donne 8 etats
 * par transfert octet/mot, 12 en 4 octets, 5 en mode compteur -- mais ces
 * chiffres n'ont jamais ete verifies sur silicium, et les armer a l'aveugle
 * ralentit Fatal Fury de 4,3 % alors que des jeux sont deja signales trop
 * lents. Cette ROM mesure le cout REEL.
 *
 * MONTAGE. Timer 0 sur la broche externe TI0 (le H-blank du K2GE), TREG0 = 1 :
 * une source d'interruption par LIGNE, ~152 par trame. Le canal 0 du micro-DMA
 * est arme sur ce vecteur : chaque impulsion declenche UN TRANSFERT au lieu de
 * vectoriser le processeur. Le seul cout restant est donc le temps de BUS pris
 * par le transfert -- exactement ce qu'on cherche, et separe du cout d'entree
 * en interruption (deja mesure a 111 cycles par la ROM v8).
 *
 * CE QU'ELLE AFFICHE. Le meme lot de travail, trois fois :
 *   WORK0 : rien d'arme                       -> reference
 *   WORKD : DMA arme, un transfert par ligne  -> reference moins le cout du bus
 *   WORKC : DMA arme en MODE COMPTEUR         -> la datasheet le donne moins cher
 *   DMAC  : ce qui reste du compteur de transferts, pour savoir COMBIEN ont eu lieu
 *   RASV  : controle de longueur de trame, doit valoir 198
 *
 * ⚠️ ET UN CONTROLE QUI VAUT A LUI SEUL LE DEPLACEMENT. Si `DMAC` n'a pas bouge,
 * le canal n'a jamais tourne sur la console -- et comme notre coeur compare
 * `DMA0V` a l'INDICE du vecteur (16) alors que le materiel y attend peut-etre le
 * vecteur lui-meme (0x40), c'est exactement le genre d'ecart que ce chiffre
 * revele. Un WORKD egal a WORK0 avec un DMAC intact ne dit pas « le transfert est
 * gratuit », il dit « le canal ne s'est pas arme ».
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V     (*(volatile u8 *)0x8009)
#define K_TREG0   (*(volatile u8 *)0x0022)
#define K_T01MOD  (*(volatile u8 *)0x0024)
#define K_INTET10 (*(volatile u8 *)0x0073)
#define K_DMA0V   (*(volatile u8 *)0x007C)

#define REPS   200
#define FRAMES 60

/* Le meme lot banal que la v8 : ce qu'on compare n'est pas sa valeur absolue,
 * mais ce que les transferts lui retirent. */
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
        if (cur < prev) frames++;
        prev = cur;
    }
    return count;
}

/* Les registres du micro-DMA sont des registres de CONTROLE : inaccessibles en C,
 * il faut `ldc`. Source et destination fixes en RAM, hors de tout ce que le jeu
 * touche ; le compteur est mis au maximum pour ne pas s'epuiser en 60 trames
 * (~9120 transferts attendus). */
/* ⛔ AUCUN PARAMETRE NI RETOUR DANS CES FONCTIONS, ET C'EST DELIBERE. L'assembleur
 * inline relatif a la pile (`(xsp+N)`) casse la pile avec cette toolchain -- c'est un
 * piege deja paye ici. Les valeurs passent donc par des GLOBALES, dont l'adresse est
 * absolue et que le compilateur ne peut pas deplacer sous nos pieds. */
u8  g_mode = 0x10;
u16 g_left = 0;

void dma_setup(void)
{
    __asm(" ld xwa,0x004500");
    __asm(" ldc dmas0,xwa");
    __asm(" ld xwa,0x004600");
    __asm(" ldc dmad0,xwa");
    __asm(" ld wa,0xffff");
    __asm(" ldc dmac0,wa");
    __asm(" ld a,(_g_mode)");
    __asm(" ldc dmam0,a");
}

void dma_read_left(void)
{
    __asm(" ldc wa,dmac0");
    __asm(" ld (_g_left),wa");
}

void timer_setup(u8 period)
{
    TRUN     = (u8)(TRUN & 0xFE);
    K_T01MOD = (u8)(K_T01MOD & 0xFC);   /* mode 00 = broche externe TI0 */
    K_TREG0  = period;
    TRUN     = (u8)(TRUN | 0x81);
}

void irq_off(void) { K_INTET10 = (u8)(K_INTET10 & 0xF8); }
void irq_on(void)  { K_INTET10 = (u8)((K_INTET10 & 0xF8) | 0x03); }

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
    u16 w0; u16 wd; u16 wc; u16 reste;

    InitNGPC();
    SetBackgroundColour(RGB(2, 2, 4));
    SysSetSystemFont();
    SetPalette(P, PAL, 4, RGB(15, 15, 15), RGB(15, 15, 15), RGB(15, 15, 15));
    CpuSpeed(0);

    PrintString(P, PAL, 1, 1,  "DMA CALIB v9 /60f");
    PrintString(P, PAL, 1, 3,  "WORK0:");
    PrintString(P, PAL, 1, 4,  "WORKD:");
    PrintString(P, PAL, 1, 5,  "WORKC:");
    PrintString(P, PAL, 1, 7,  "DMAC :");
    PrintString(P, PAL, 1, 9,  "RASV :");
    PrintString(P, PAL, 1, 11, "note les 5 nombres");
    PrintString(P, PAL, 1, 12, "et le md5 de la rom");

    while (1) {
        /* 1 : rien d'arme. */
        irq_off();
        K_DMA0V = 0;
        timer_setup(1);
        w0 = work_batches();
        PrintDecimal(P, PAL, 12, 3, w0, 5);

        /* 2 : un transfert d'OCTET par ligne. mode 0x10 = (DMAD)<-(DMAS), fixe. */
        g_mode = 0x10;
        dma_setup();
        K_DMA0V = 16;                  /* le vecteur INTT0 */
        irq_on();
        timer_setup(1);
        wd = work_batches();
        dma_read_left();
        reste = g_left;
        irq_off();
        K_DMA0V = 0;
        PrintDecimal(P, PAL, 12, 4, wd, 5);

        /* 3 : mode COMPTEUR (rien ne bouge), que la datasheet donne moins cher. */
        g_mode = 0x14;
        dma_setup();
        K_DMA0V = 16;
        irq_on();
        timer_setup(1);
        wc = work_batches();
        irq_off();
        K_DMA0V = 0;
        PrintDecimal(P, PAL, 12, 5, wc, 5);

        PrintDecimal(P, PAL, 12, 7, reste, 5);
        PrintDecimal(P, PAL, 12, 9, rasv_max(), 3);
    }
}
