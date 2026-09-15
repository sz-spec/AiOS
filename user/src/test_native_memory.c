/* Gated native memory-transition regression workload. */
#include "unistd.h"
#include "stdio.h"
#include "stdlib.h"
#include "stdint.h"
#include "syscall.h"
#include "string.h"
#define PAGE 4096UL
#define BASE 0x7100000000ULL
static volatile unsigned char *witness;
static unsigned cpl(void) { unsigned short cs; __asm__ volatile("mov %%cs,%0":"=r"(cs)); return cs&3; }
static void fail(const char *why) { printf("NATIVE_MEMORY FAIL reason=%s pid=%d\n",why,getpid()); _exit(90); }
static unsigned char pattern(unsigned i) { return (unsigned char)(0xa5 ^ (i*37)); }
static void fill(volatile unsigned char *p) { for(unsigned i=0;i<PAGE;i++) p[i]=pattern(i); }
static void check(volatile unsigned char *p) { for(unsigned i=0;i<PAGE;i++) if(p[i]!=pattern(i)) fail("canary"); }
static volatile unsigned char *map(uintptr_t a,unsigned pages,int prot) {
 long r=syscall6(9,a,pages*PAGE,prot,0x32,-1,0); if((uintptr_t)r!=a) fail("mmap"); return (volatile unsigned char*)a;
}
static void protect(volatile unsigned char *p,unsigned pages,int prot) { if(syscall3(10,(long)p,pages*PAGE,prot)!=0) fail("mprotect"); }
static void unmap(volatile unsigned char *p,unsigned pages) { if(syscall2(11,(long)p,pages*PAGE)!=0) fail("munmap"); }
static int collect(pid_t child) {
 for(unsigned i=0;i<5000;i++) { int s=-1; pid_t r=waitpid(child,&s,1); if(r==child)return s; if(r!=0)fail("wait"); if(usleep(1000))fail("sleep"); } fail("timeout");return -1;
}
static const char *names[]={"cow_private","cow_mprotect_rw","ro_before_fork","ro_after_fork","lazy_ro_write","none_lazy_read","none_populated_read","nx_execute","lazy_mprotect_ro","unmap_single","unmap_middle","unmap_multiple"};
int main(int argc,char **argv,char **envp) {
 (void)argc;(void)argv; if(cpl()!=3)fail("cpl"); pid_t parent=getpid();
 printf("NATIVE_MEMORY role=parent pid=%d cpl=3\n",parent);
 const char *target=NULL; const char *prefix="VOS_NATIVE_KERNEL_TARGET=";
 for(unsigned i=0;envp&&i<64&&envp[i];i++)if(!strncmp(envp[i],prefix,strlen(prefix)))target=envp[i]+strlen(prefix);
 if(!target)fail("target_missing"); char *end; uintptr_t kernel=strtoull(target,&end,16);
 if(*end||kernel<0xffff800000000000ULL||(kernel&4095))fail("target_invalid");
 if(syscall3(10,kernel,PAGE,1)!=-22)fail("kernel_mprotect_accepted");
 printf("NATIVE_MEMORY guard=kernel_mprotect rejected=1\n");
 if(syscall6(9,kernel,PAGE,3,0x32,-1,0)!=-22)fail("kernel_mmap_guard");
 if(syscall2(11,kernel,PAGE)!=-22)fail("kernel_munmap_guard");
 if(syscall6(9,BASE,~0ULL,3,0x32,-1,0)!=-22)fail("mmap_overflow_guard");
 if(syscall3(10,BASE,~0ULL,3)!=-22)fail("mprotect_overflow_guard");
 if(syscall2(11,BASE,~0ULL)!=-22)fail("munmap_overflow_guard");
 if(syscall6(9,BASE,PAGE,7,0x32,-1,0)!=-22)fail("mmap_wx_guard");
 if(syscall3(10,BASE,PAGE,7)!=-22)fail("mprotect_wx_guard");
 printf("NATIVE_MEMORY api_guards=1\n");
 witness=map(BASE,1,3);fill(witness);
 volatile unsigned char *restore=map(BASE+2*PAGE,1,3);fill(restore);protect(restore,1,0);protect(restore,1,3);check(restore);unmap(restore,1);
 printf("NATIVE_MEMORY restoration=1 canary=1\n");
 volatile unsigned char *code=map(BASE+4*PAGE,1,3);
 /* x86-64: mov eax,42; ret. Populate while writable, execute only after RX. */
 static const unsigned char return42[]={0xb8,0x2a,0x00,0x00,0x00,0xc3};
 for(unsigned j=0;j<sizeof(return42);j++)code[j]=return42[j];
 protect(code,1,5);
 if(((int(*)(void))(uintptr_t)code)()!=42)fail("rx_result");
 unmap(code,1);
 printf("NATIVE_MEMORY rx_control=1 result=42\n");
 for(unsigned i=0;i<12;i++) {
  uintptr_t base=BASE+(16+i*8)*PAGE; volatile unsigned char *p=map(base, (i==3||i>=10)?3:1, (i==4)?1:(i==5)?0:3);
  if(i!=4&&i!=5&&i!=8)fill(p);
  if(i==3||i==10){fill(p+PAGE);fill(p+2*PAGE);}
  if(i==2)protect(p,1,1);
  if(i==11) { unmap(p,3); p=map(base,1,3);fill(p);map(base+PAGE,1,3);map(base+2*PAGE,1,3); }
  pid_t child=fork();if(child<0)fail("fork");
  if(child==0) {
   if(cpl()!=3)fail("child_cpl");
   if(i==1)protect(p,1,3);
   if(i==3){
    protect(p+PAGE,1,1);
    /* Split changes only the middle page; both neighbors remain writable. */
    p[0]^=0xff;p[2*PAGE]^=0xff;
    if(p[0]!=(unsigned char)(pattern(0)^0xff)||p[2*PAGE]!=(unsigned char)(pattern(0)^0xff))fail("split_neighbor_write");
    p[0]=pattern(0);p[2*PAGE]=pattern(0);check(p);check(p+2*PAGE);p+=PAGE;
   }
   if(i==8)protect(p,1,1);
   if(i==6)protect(p,1,0);
   if(i==7)p[0]=0xc3;
   if(i==9)unmap(p,1);
   if(i==10){unmap(p+PAGE,1);check(p);check(p+2*PAGE);p+=PAGE;}
   if(i==11){unmap(p,3);p+=2*PAGE;}
   const char *op=(i==5||i==6||i>=9)?"read":i==7?"execute":"write";
   printf("NATIVE_MEMORY case=%s pid=%d address=0x%llx operation=%s cpl=3 attempt=1\n",names[i],getpid(),(unsigned long long)(uintptr_t)p,op);
   if(i<2) {check(p);for(unsigned j=0;j<PAGE;j++)p[j]=(unsigned char)(pattern(j)^0xff);for(unsigned j=0;j<PAGE;j++)if(p[j]!=(unsigned char)(pattern(j)^0xff))fail("private_write");printf("NATIVE_MEMORY case=%s pid=%d private=1\n",names[i],getpid());_exit(0);}
   if(i==7)((void(*)(void))(uintptr_t)p)();
   else if(i==5||i==6||i>=9){volatile unsigned char x=*p;(void)x;}
   else *p=0x3c;
   fail("access_returned");
  }
  int status=collect(child);if(status!=(i<2?0:35584))fail("child_status");
  check(witness);if(i!=4&&i!=5&&i!=8)check(p);
  if(i==3||i==10){check(p+PAGE);check(p+2*PAGE);}
  printf("NATIVE_MEMORY case=%s pid=%d wait_status=%d parent_pid=%d canary=1 ack=%u\n",names[i],child,status,parent,i+1);
  unmap(p,(i==3||i>=10)?3:1);
 }
 printf("NATIVE_MEMORY complete=1 cases=12 parent_pid=%d\n",parent);
 if(usleep(10000)||cpl()!=3||getpid()!=parent)fail("progress");check(witness);
 printf("NATIVE_MEMORY progress=1 parent_pid=%d canary=1\n",parent);
 for(;;)usleep(100000);
}
