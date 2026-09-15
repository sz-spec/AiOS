/* Gated native workload: report hardware CPUID identity, never task metadata. */
#include "unistd.h"
#include "stdio.h"
#include "stdint.h"
#define STEPS 200000U
#define ROUNDS 8U
static unsigned cpl(void){unsigned short cs;__asm__ volatile("mov %%cs,%0":"=r"(cs));return cs&3;}
static unsigned apic(void){unsigned a=1,b,c,d;__asm__ volatile("cpuid":"+a"(a),"=b"(b),"=c"(c),"=d"(d));return b>>24;}
static void fail(const char *why){printf("NATIVE_SMP FAIL reason=%s pid=%d\n",why,getpid());_exit(90);}
static int collect(pid_t child){for(unsigned i=0;i<20000;i++){int s=-1;pid_t r=waitpid(child,&s,1);if(r==child)return s;if(r!=0)fail("wait");if(usleep(1000))fail("sleep");}fail("timeout");return -1;}
int main(void){
 pid_t parent=getpid();if(cpl()!=3)fail("parent_cpl");
 printf("NATIVE_SMP role=parent pid=%d cpl=3 apic=%u\n",parent,apic());
 pid_t children[2];
 for(unsigned worker=0;worker<2;worker++){
  pid_t child=fork();if(child<0)fail("fork");
  if(child==0){
   uint32_t state=worker+1;
   for(unsigned round=1;round<=ROUNDS;round++){
    for(unsigned j=0;j<STEPS;j++)state=state*1664525U+1013904223U;
    if(cpl()!=3)fail("worker_cpl");
    printf("NATIVE_SMP worker=%u pid=%d checkpoint=%u steps=%u value=%u cpl=3 apic=%u\n",worker,getpid(),round,STEPS,state,apic());
    if(usleep(1000))fail("yield");
   }
   _exit(0);
  }
  children[worker]=child;
 }
 for(unsigned worker=0;worker<2;worker++){
  int status=collect(children[worker]);if(status!=0)fail("worker_status");
  printf("NATIVE_SMP worker=%u pid=%d wait_status=0 parent_pid=%d\n",worker,children[worker],parent);
 }
 printf("NATIVE_SMP complete=1 workers=2 checkpoints=16 parent_pid=%d\n",parent);
 if(usleep(10000)||getpid()!=parent||cpl()!=3)fail("progress");
 printf("NATIVE_SMP progress=1 parent_pid=%d cpl=3 apic=%u\n",parent,apic());
 for(;;)usleep(100000);
}
