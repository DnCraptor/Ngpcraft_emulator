/* ============================================================================
 * NGPC CALIBRATION ROM  (v13, 2026-08-27) -- LE VIDAGE DE FILE A LA BRANCHE
 *
 * LA QUESTION. Une branche PRISE coute-t-elle plus cher que sa ligne de table,
 * parce que la file de prefetch est jetee et doit etre refaite ?
 *
 * POURQUOI MAINTENANT. L'hypothese est qu'un transfert de controle pris paye un
 * cycle de bus entier de rechargement de file.
 * Nous l'avions essayee le 21/08 et REFUTEE -- non par la valeur mais
 * par le SIGNE : un vidage ne peut que ralentir, or contre le silicium ce coeur
 * est deja lent (REG -4 %, boucles -6 %), et l'armer poussait tous les points du
 * mauvais cote (REG -7, LOOP -11, aller-retour 1118 -> 1084). Mais une refutation
 * par le signe ne dit pas OU se trouve la verite, seulement qu'un des bouts est
 * faux. Le silicium tranche.
 *
 * ⚠️ CE QUI N'EST PAS AFFIRME ICI. Ni que le vidage existe, ni qu'il n'existe
 * pas. Cette ROM ne mesure pas un vidage : elle fait varier la DENSITE DE
 * BRANCHES d'un facteur 8 a travail constant, et laisse quatre nombres dire quel
 * modele les predit. C'est la lecon de v10-v12 : deux points se font demolir par
 * quatre, et une cause nommee trop vite est fausse une fois sur deux.
 *
 * LE MONTAGE. Quatre blocs. Chacun execute EXACTEMENT 640 unites de corps, avec
 * 1, 2, 4 ou 8 unites par branche prise :
 *
 *      bloc   BC init   unites/tour   total unites   branches prises   corps
 *      u1       640          1             640             640         12 o.
 *      u2       320          2             640             320         24 o.
 *      u4       160          4             640             160         48 o.
 *      u8        80          8             640              80         96 o.
 *
 * Cout du bloc = 640 x cout_unite + (640/U) x cout_branche. Quatre points, une
 * droite : la PENTE est le cout d'une branche prise, l'ORDONNEE le cout des 640
 * unites.
 *
 * ⚡ ET C'EST LA PENTE QUI REPOND, PAS LES NOMBRES. L'ordonnee absorbe TOUT le
 * cout du travail -- si notre `mul` ou notre `ld` est mal cote, l'ordonnee bouge
 * et la pente ne bouge pas. La ROM est donc immunisee contre les erreurs de cout
 * qu'elle ne cherche pas. C'est le contraire des v1-v9, qui comparaient des
 * niveaux absolus et se faisaient contaminer par n'importe quelle constante fausse.
 *
 * ⛔⛔ LE PIEGE QUI A TUE LA PREMIERE VERSION DE CETTE ROM -- A LIRE AVANT DE
 * TOUCHER AU CORPS. Le premier montage avait un corps fait UNIQUEMENT de `mul`,
 * choisi parce qu'une instruction execute-bound remplit la file et rend le credit
 * d'avance maximal a l'instant de la branche. Resultat : les deux modeles ont
 * rendu LES MEMES NOMBRES (224/263/286/299 des deux cotes). Le raisonnement etait
 * a l'envers. Un vidage ne coute pas le credit qu'on jette, il coute le credit
 * qui MANQUE ENSUITE : apres la branche, une seule `mul` (2 octets pour 28 cycles)
 * reconstitue tout le credit en une instruction, et la perte devient invisible.
 * ⇒ Le corps doit CONSOMMER du credit apres la branche. D'ou sa forme actuelle.
 *
 * L'UNITE DE CORPS, ET POURQUOI DANS CET ORDRE (12 octets) :
 *   ld XWA,#imm32   5 octets pour  5 etats -- fetch-bound, CONSOMME du credit
 *   ld XHL,#imm32   5 octets pour  5 etats -- idem
 *   mul WA,E        2 octets pour ~14 etats -- execute-bound, RECONSTRUIT du credit
 * La `mul` est en DERNIER : la branche tombe donc credit au maximum, et si le
 * vidage existe ce sont les deux charges du tour suivant qui payent plein tarif.
 * Inverser l'ordre rendrait la ROM aveugle -- c'est exactement l'erreur ci-dessus.
 *
 * POURQUOI L'OPERANDE NE DERIVE NI DANS UN BLOC NI ENTRE LES BLOCS. `ld XWA,#`
 * recharge A a une constante a chaque unite, donc `mul WA,E` calcule toujours le
 * meme produit ; et `mul` n'ecrit que WA, sans toucher ni E (=3) ni BC (compteur
 * de `djnz`). Aucun chemin degenere, aucune dependance aux donnees : si `mul`
 * avait un cout variable selon ses operandes, il frapperait les quatre blocs a
 * l'identique et ne polluerait pas la pente.
 *
 * ENCODAGES RELEVES AU DESASSEMBLAGE, PAS SUPPOSES (regle du projet) :
 *   40 xx xx xx xx  ld XWA,#imm32   | 43 xx xx xx xx  ld XHL,#imm32
 *   CD 41           mul WA,E        | D9 1C dd        djnz BC,dd
 *   31 xx xx        ld BC,#         | DA AB  ld DE,3  | D8 A9  ld WA,1
 * Verifie sur la ROM construite : deplacements du `djnz` = -15 / -27 / -51 / -99,
 * soit des corps de 12 / 24 / 48 / 96 octets = 1 / 2 / 4 / 8 unites. `D9 1C` est
 * la forme registre-direct de la famille D8..DF, HW-prouvee (boucle OAM de
 * Ganbare) -- PAS le mis-encode `D0 1C`.
 *
 * ⛔ PAS DE VARIABLE GLOBALE, VOLONTAIREMENT. La v6 bootait chez nous et
 * PLANTAIT la console, avec pour seuls suspects des buffers globaux et
 * `ld xhl,_symbol`. Cette ROM garde la forme de la v12, qui a tourne sur
 * silicium. Le banc emulateur relit les nombres dans le plan de tuiles a l'aide
 * de la ligne-cle "0123456789" ; voir `flush_gate.py`.
 *
 * PREDICTIONS DE NOTRE COEUR (timing silicium, meme ROM, md5 ci-dessous) :
 *      drapeau desarme (vidage nul)  141 / 155 / 163 / 167  pente ~12,2 cy/branche
 *      drapeau arme (vidage total)   120 / 141 / 155 / 163  pente ~24,1 cy/branche
 * Les deux jeux ne sont PAS un simple facteur d'echelle l'un de l'autre (rapports
 * 1,175 / 1,099 / 1,052 / 1,025) : ils different de FORME, pas seulement de niveau.
 *
 * LECTURE DU TIR :
 *   - quatre points alignes                ⇒ le montage est sain ;
 *   - pente ~12 cy/branche                 ⇒ PAS de vidage ;
 *   - pente ~24 cy/branche                 ⇒ vidage TOTAL ;
 *   - pente entre les deux                 ⇒ vidage PARTIEL : lire le credit
 *                                            conserve que rend `flush_gate.py` ;
 *   - droite qui ne ferme pas, RASV != 198 ⇒ NE RIEN CONCLURE. Un point isole qui
 *                                            contredit le modele est d'abord
 *                                            SUSPECT (lecon v10 : le `BASE = 281`
 *                                            que trois ROM n'ont jamais reproduit).
 *
 * ============================ RESULTAT DU TIR ==============================
 * TIR SILICIUM 2026-08-27, RASV 198, deux lectures (jitter +-1 sur chaque case) :
 *      u1 125/124   u2 141/140   u4 151/152   u8 156/157
 *      ⇒ pente 17,5 a 18,8 cy/branche, ordonnee ~37 800, droite fermee a 0,15/0,60 %
 *
 * ⚖️ LES DEUX REGLAGES EXTREMES SONT FAUX. La pente tombe a 45-55 % du chemin entre
 * eux -- si pres du milieu que la premiere version de `flush_gate.py`, qui tranchait
 * sur la mediane, BASCULAIT DE VERDICT selon le cote du jitter. C'est le defaut qui a
 * ete corrige : la porte rend desormais le CREDIT CONSERVE, pas un oui/non.
 *
 * ⇒ CE QUE LE SILICIUM DIT : une branche prise coute REELLEMENT ~+6 cycles de plus
 * que sa ligne de table -- la refutation du 21/08 rejetait a raison le vidage TOTAL
 * et a tort le vidage tout court -- mais environ la MOITIE d'un vidage complet.
 * Le booleen avait la mauvaise FORME, pas la mauvaise valeur.
 *
 * ⛔ ET LE NOMBRE N'EST PAS DERIVE. `branch_flush_keep = 6` reproduit cette ROM ;
 * la valeur structurellement attendue (un mot de file = ~8 cy) donne 16,1 cy/branche,
 * HORS de la bande mesuree. On ne sait donc pas encore POURQUOI c'est la moitie.
 * Rien n'est arme : defaut inchange. Avant d'armer, rejouer les ROM v1-v12 contre
 * leurs tirs enregistres -- sinon c'est un nombre cale sur une seule boucle, ce que
 * la v2 avait precisement attrape sur `cart_data_wait`.
 * ===========================================================================
 *
 * Construit avec la toolchain OFFICIELLE Toshiba cc900. Gear 0 (6,144 MHz).
 * ==========================================================================*/

#include "ngpc.h"
#include "carthdr.h"
#include "library.h"

#define RAS_V (*(volatile u8 *)0x8009)
#define FRAMES 60

/* Une UNITE de corps. L'ordre est le montage : voir l'en-tete. */
#define MUL1  __asm(" ld XWA,0x01010101"); __asm(" ld XHL,0x02020202"); __asm(" mul WA,E");
#define MUL2  MUL1 MUL1
#define MUL4  MUL2 MUL2
#define MUL8  MUL4 MUL4

/* push/pop encadrent le bloc : le C tient ses variables dans ces memes
 * registres, et l'asm les ecrase. */
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

BLOC(m_u1, 640, MUL1)
BLOC(m_u2, 320, MUL2)
BLOC(m_u4, 160, MUL4)
BLOC(m_u8,  80, MUL8)

/* Validite du tir : RAS.V doit plafonner a 198. Toute autre valeur = la trame
 * n'est pas celle qu'on croit et les quatre nombres ne veulent rien dire. */
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

    PrintString(P, PAL, 1, 1,  "FLUSH v13 /60f");
    PrintString(P, PAL, 1, 3,  "U1 640br:");
    PrintString(P, PAL, 1, 4,  "U2 320br:");
    PrintString(P, PAL, 1, 5,  "U4 160br:");
    PrintString(P, PAL, 1, 6,  "U8  80br:");
    PrintString(P, PAL, 1, 8,  "RASV    :");
    PrintString(P, PAL, 1, 10, "note les 5 nombres");
    PrintString(P, PAL, 1, 11, "et le md5 de la rom");
    /* Cle de decodage : le banc emulateur lit la tuile de chaque chiffre ici et
     * s'en sert pour relire les nombres dans le plan, sans variable globale.
     * ⛔ Pas de tableau global : c'est le motif (globals + `ld xhl,_symbol`) qui
     * faisait booter la v6 chez nous et PLANTER la console. */
    PrintString(P, PAL, 1, 13, "0123456789");

    while (1) {
        PrintDecimal(P, PAL, 12, 3, m_u1(), 5);
        PrintDecimal(P, PAL, 12, 4, m_u2(), 5);
        PrintDecimal(P, PAL, 12, 5, m_u4(), 5);
        PrintDecimal(P, PAL, 12, 6, m_u8(), 5);
        PrintDecimal(P, PAL, 12, 8, rasv_max(), 3);
    }
}
