#include "Vtiny_sim.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <memory>
#include <string>
#include <vector>
static uint64_t simulation_time=0;
double sc_time_stamp() { return (double)simulation_time; }

static std::vector<uint8_t> load(const char *name) {
 std::ifstream f(name,std::ios::binary);
 if(!f) throw std::runtime_error(std::string("missing ")+name);
 return std::vector<uint8_t>(std::istreambuf_iterator<char>(f),{});
}
int main(int argc,char **argv) {
 if(argc!=3) return 2;
 Verilated::commandArgs(argc,argv);
 auto image=load(argv[1]), script=load(argv[2]);
 auto top=std::make_unique<Vtiny_sim>();
 const uint64_t bitcycles=50000000/115200, limit=3000000000ull;
 bool prevcs=true,prevsclk=false,prevtx=true,started=false,receiving=false;
 unsigned bits=0; uint32_t command=0; uint64_t reads=0;
 uint64_t sendstart=0,rxsample=0; size_t sendindex=0;
 unsigned rxbit=0; uint8_t byte=0;
 std::string line,uart;
 top->miso=0;
 for(uint64_t cycle=0;cycle<limit;cycle++) {
  simulation_time=cycle*20;
  bool input=true;
  if(started && sendindex<script.size() && cycle>=sendstart) {
   uint64_t index=(cycle-sendstart)/bitcycles;
   if(index==0) input=false;
   else if(index<=8) input=(script[sendindex]>>(index-1))&1;
   else if(index>=11) { sendindex++; sendstart=cycle+1; }
  }
  top->rx=input;
  top->clk=0; top->eval();
  top->clk=1; top->eval();
  bool cs=top->cs,sclk=top->sclk;
  if(prevcs && !cs) { bits=0; command=0; }
  if(!cs && !prevsclk && sclk) {
   if(bits<32) command=(command<<1)|(top->mosi&1);
   bits++;
  }
  if(!cs && prevsclk && !sclk) {
   unsigned opcode=bits>=8?((bits<32?command<<(32-bits):command)>>24):0;
   if(opcode==3 && bits>=32) {
    uint32_t address=(command&0xffffff)+(bits-32)/8;
    uint8_t value=address<image.size()?image[address]:255;
    top->miso=(value>>(7-(bits-32)%8))&1;
   } else if(opcode==0x9f && bits>=8) {
    const uint8_t id[]={0x20,0xba,0x18};
    unsigned n=(bits-8)/8;
    top->miso=n<3?(id[n]>>(7-(bits-8)%8))&1:1;
   }
  }
  if(!prevcs && cs && bits>=64 && (command>>24)==3) reads++;
  prevcs=cs; prevsclk=sclk;
  bool tx=top->tx;
  if(!receiving && prevtx && !tx) { receiving=true; rxbit=0; byte=0; rxsample=cycle+bitcycles+bitcycles/2; }
  if(receiving && cycle==rxsample) {
   if(rxbit<8) { byte|=(tx?1:0)<<rxbit; rxbit++; rxsample+=bitcycles; }
   else {
    if(!tx) { std::fprintf(stderr,"UART framing error\n"); return 3; }
    receiving=false; uart.push_back((char)byte); line.push_back((char)byte);
    if(byte=='\n') {
     std::printf("%s",line.c_str()); std::fflush(stdout);
     if(line=="READY\n" && !started) { started=true; sendstart=cycle+bitcycles*2; }
     line.clear();
    }
   }
  }
  prevtx=tx;
  if(top->trap || top->fault || top->overrun) {
   std::fprintf(stderr,"RTL fault at cycle %llu trap=%u bus=%u overrun=%u\n",
    (unsigned long long)cycle,top->trap,top->fault,top->overrun); return 4;
  }
  if(top->exitcode) {
   std::printf("SIM cycles=%llu spi_reads=%llu\n",(unsigned long long)cycle,(unsigned long long)reads);
   return uart.find("DONE\n")!=std::string::npos?0:5;
  }
  if(cycle && cycle%100000000==0) {
   std::fprintf(stderr,"cycle %llu, flash reads %llu\n",(unsigned long long)cycle,(unsigned long long)reads);
   std::fflush(stderr);
  }
 }
 std::fprintf(stderr,"RTL watchdog\n"); return 6;
}
