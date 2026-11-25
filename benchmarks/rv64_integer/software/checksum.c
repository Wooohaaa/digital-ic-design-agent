unsigned long checksum(void)
{
    volatile unsigned long samples[64];
    unsigned long sum = 0;

    for (unsigned long i = 0; i < 64; ++i)
        samples[i] = (i << 32) | (i ^ 0x55);
    for (unsigned long i = 0; i < 64; ++i)
        sum += samples[i];

    return sum;
}
