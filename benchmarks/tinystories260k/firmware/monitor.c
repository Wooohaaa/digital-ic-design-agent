#include <stdint.h>
#define REG(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define DATA 0x40000000u
#define STATUS 0x40000004u
#define GPIO 0x40000008u
#define MODEL ((const void *)0x10000000u)
#define MODEL_BYTES 1056540u
#include "model_contract.h"
static int model_valid;
int ts_init(const void *);
int ts_forward(int,int);
const float *ts_logits(void);
/* newlib libm error reporting; no heap, libc startup or OS is used. */
int *__errno(void) { static int error; return &error; }
static void putc_uart(char c) { while(!(REG(STATUS)&2)) {} REG(DATA)=(uint8_t)c; }
static void puts_uart(const char *s) { while(*s) putc_uart(*s++); }
static int getc_uart(void) { while(!(REG(STATUS)&1)) {} return REG(DATA)&255; }
static uint32_t get32(void) { uint32_t x=0; for(int i=0;i<4;i++) x|=(uint32_t)getc_uart()<<(8*i); return x; }
static void hex32(uint32_t x) { for(int i=7;i>=0;i--) putc_uart("0123456789abcdef"[(x>>(i*4))&15]); }
static void dec(unsigned x) { char b[10]; int n=0; do { b[n++]='0'+x%10; x/=10; } while(x); while(n) putc_uart(b[--n]); }
static void spi_set(unsigned cs,unsigned sck,unsigned mosi) { REG(GPIO)=8|cs|(sck<<1)|(mosi<<2); }
static unsigned spi_byte(unsigned x) {
    unsigned y=0;
    for(int i=7;i>=0;i--) {
        spi_set(0,0,(x>>i)&1); spi_set(0,1,(x>>i)&1);
        y=(y<<1)|(REG(GPIO)&1); spi_set(0,0,(x>>i)&1);
    }
    return y;
}
static void cs_end(void) { spi_set(1,0,0); }
static void command(unsigned c) { spi_set(0,0,0); spi_byte(c); }
static void address(uint32_t a) { spi_byte(a>>16); spi_byte(a>>8); spi_byte(a); }
static int flash_wait(void) {
    for(unsigned i=0;i<100000;i++) {
        command(5); unsigned status=spi_byte(0); cs_end();
        if(!(status&1)) return 0;
    }
    return -1;
}
static void wren(void) { command(6); cs_end(); }
static uint32_t flash_id(void) {
    command(0x9f); unsigned a=spi_byte(0), b=spi_byte(0), c=spi_byte(0); cs_end();
    return (a<<16)|(b<<8)|c;
}
static uint32_t crc_image(void) {
    const volatile uint32_t *p=(const volatile uint32_t *)MODEL;
    uint32_t crc=0xffffffffu;
    for(unsigned i=0;i<MODEL_BYTES/4;i++) {
        uint32_t word=p[i];
        for(int j=0;j<4;j++) {
            crc^=(word>>(8*j))&255;
            for(int k=0;k<8;k++) crc=(crc>>1)^(0xedb88320u&-(crc&1));
        }
    }
    return ~crc;
}
static void upload(void) {
    model_valid=0;
    unsigned length=get32(), expected=get32();
    unsigned id=flash_id();
    if(length!=MODEL_BYTES || expected!=MODEL_CRC32 || id!=0x20ba18) {
        cs_end(); REG(GPIO)=1; puts_uart("ERROR flash-id/length\n"); return;
    }
    puts_uart("UPLOAD\n");
    unsigned char page[256];
    for(unsigned offset=0;offset<length;offset+=256) {
        unsigned n=length-offset; if(n>256) n=256;
        for(unsigned i=0;i<n;i++) page[i]=getc_uart();
        if(!(offset&4095)) {
            wren(); command(0x20); address(offset); cs_end();
            if(flash_wait()) goto fail;
        }
        wren(); command(2); address(offset);
        for(unsigned i=0;i<n;i++) spi_byte(page[i]);
        cs_end(); if(flash_wait()) goto fail;
        putc_uart('+');
    }
    REG(GPIO)=1;
    unsigned actual=crc_image(); puts_uart("\nCRC "); hex32(actual); putc_uart('\n');
    model_valid=(actual==MODEL_CRC32);
    puts_uart(actual==expected?"DONE\n":"ERROR crc\n"); return;
fail:
    cs_end(); REG(GPIO)=1; puts_uart("ERROR flash-timeout\n");
}
static void generate(int diagnostic) {
    unsigned steps=getc_uart(), count=getc_uart(), prompt[64];
    if(!count || count>64 || steps<count || steps>64) { puts_uart("ERROR bounds\n"); return; }
    for(unsigned i=0;i<count;i++) { unsigned lo=getc_uart(); prompt[i]=lo|((unsigned)getc_uart()<<8); }
    for(unsigned i=0;i<count;i++) if(prompt[i]>=512) { puts_uart("ERROR token\n"); return; }
    if(!model_valid) model_valid=(crc_image()==MODEL_CRC32);
    if(!model_valid) { puts_uart("ERROR image-crc\n"); return; }
    if(ts_init(MODEL)) { model_valid=0; puts_uart("ERROR checkpoint\n"); return; }
    int next=1;
    for(unsigned pos=0;pos<steps;pos++) {
        int token=pos<count?(int)prompt[pos]:next;
        next=ts_forward(token,(int)pos);
        if(next<0) { puts_uart("ERROR inference\n"); return; }
        if(diagnostic) {
            const float *result=ts_logits();
            puts_uart("LOGITS "); dec(pos);
            for(unsigned j=0;j<512;j++) {
                union { float f; uint32_t u; } bits; bits.f=result[j];
                putc_uart(' '); hex32(bits.u);
            }
            putc_uart('\n');
        }
        if(pos>=count-1) {
            puts_uart("TOKEN "); dec(pos+1); putc_uart(' '); dec((unsigned)next); putc_uart('\n');
            if(next==2) break;
        }
    }
    puts_uart("DONE\n");
}
int main(void) {
    REG(GPIO)=1;
    puts_uart("ICFORGE TS260K RV32I CTX64\nREADY\n");
    for(;;) {
        unsigned c=getc_uart();
        if(c=='G' || c=='T') generate(c=='T');
        else if(c=='W') upload();
        else if(c=='I') { puts_uart("JEDEC "); hex32(flash_id()); REG(GPIO)=1; putc_uart('\n'); }
        else if(c=='V') { unsigned crc=crc_image(); model_valid=(crc==MODEL_CRC32); puts_uart("CRC "); hex32(crc); putc_uart('\n'); }
        else if(c=='X') { while(!(REG(STATUS)&2)) {} REG(0x4000000c)=1; }
        else puts_uart("ERROR command\n");
    }
}
