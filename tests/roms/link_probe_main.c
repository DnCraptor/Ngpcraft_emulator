#include "ngpc.h"
#include "carthdr.h"
#include "library.h"
#include "serial.h"

/* Headless link-cable proof. Robust against the serial.c wrappers' unreliable
 * RETURN values (cc900 -O3 "No return value"): the received byte is delivered
 * through the *out pointer, which IS written explicitly, and we drive the drain
 * loop from the BIOS RX ring count in RAM (0x6D01) rather than com_get_data's
 * return code. Each console sends its raw controller byte (port 0xB0, injected
 * per-machine by the harness) and records the last byte received + a total. */
volatile u8  g_last_rx  = 0;
volatile u16 g_rx_total = 0;
volatile u16 g_tx_count = 0;

void main(void)
{
   u8 rx, n;

   InitNGPC();

   com_init();
   com_recv_start();

   while (1)
   {
      com_create_data(*(volatile u8 *)0x00B0);   /* queue our controller byte */
      com_send_start();
      g_tx_count++;

      /* drain exactly the bytes the BIOS RX ring holds (0x6D01 = RX count) */
      n = *(volatile u8 *)0x006D01;
      while (n)
      {
         com_get_data(&rx);
         g_last_rx = rx;
         g_rx_total++;
         n--;
      }

      /* bounded busy delay in place of WaitVsync (the BIOS VBlank->0x6FCC user
       * hook does not advance in hand-off mode, which is orthogonal to the link
       * and would otherwise hang this loop). Paces the exchange to a few per
       * emulated frame so the rings do not flood. */
      {
         u16 d;
         for (d = 0; d < 3000; d++)
            __asm(" nop");
      }
   }
}
