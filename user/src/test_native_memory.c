/* Gated native memory-transition regression workload. */
#include "unistd.h"
#include "stdio.h"
#include "stdlib.h"
#include "stdint.h"
#include "syscall.h"
#include "string.h"
#include "poll.h"
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

#include "test_native_backing.inc"

/* Separate assembly return path: never resume a C frame on the new stack. */
extern long native_share_clone(void *top, void (*entry)(void *), void *argument);
__asm__(".text\n.type native_share_clone,@function\nnative_share_clone:\n"
        "and $-16,%rdi\nsub $16,%rdi\nmov %rsi,0(%rdi)\nmov %rdx,8(%rdi)\n"
        "mov %rdi,%rsi\nmov $273,%edi\nxor %edx,%edx\nxor %r10d,%r10d\nxor %r8d,%r8d\n"
        "mov $56,%eax\nsyscall\ntest %rax,%rax\njnz 1f\n"
        "pop %rax\npop %rdi\ncall *%rax\nmov $90,%edi\nmov $60,%eax\nsyscall\nud2\n1: ret\n");
static void transfer(int fd,void *data,unsigned size,int sending){
 if(!sending){struct pollfd p={fd,POLLIN,0};if(syscall3(SYS_POLL,(long)&p,1,5000)!=1||!(p.revents&POLLIN))fail("lifetime_pipe_timeout");}
 if((sending?write(fd,data,size):read(fd,data,size))!=(int)size)fail("lifetime_pipe");
}
struct lifetime_args {volatile unsigned char *page;int command,reply,detach;};
static void failed_exec_child(void *argument){
 volatile unsigned char *page=argument;if(cpl()!=3)fail("shared_cpl");
 char *args[]={"missing",NULL};char *env[]={NULL};
 if(execve("/tmp/vos-invalid-lifetime.elf",args,env)>=0)fail("failed_exec_return");
 check(page);page[0]^=0xff;
 printf("NATIVE_MEMORY lifetime=failed_exec pid=%d cpl=3 shared=1 rollback=1\n",getpid());_exit(0);
}
static void survivor_child(void *argument){
 struct lifetime_args *a=argument;pid_t self=getpid();if(cpl()!=3)fail("survivor_cpl");
 transfer(a->reply,&self,sizeof(self),1);unsigned char challenge;
 transfer(a->command,&challenge,1,0);if(challenge!=0x71)fail("survivor_challenge");
 check(a->page);for(unsigned i=0;i<PAGE;i++)a->page[i]^=0xff;
 for(unsigned i=0;i<PAGE;i++)if(a->page[i]!=(unsigned char)(pattern(i)^0xff))fail("survivor_write");
 printf("NATIVE_MEMORY lifetime=%s pid=%d cpl=3 canary=1 survivor=1\n",a->detach?"exec_detach":"parent_exit",self);
 challenge=0x72;transfer(a->reply,&challenge,1,1);_exit(0);
}
static void lifetime_tests(pid_t parent){
 int bad=open("/tmp/vos-invalid-lifetime.elf",O_CREAT|O_TRUNC|O_WRONLY,0600);
 if(bad<0)fail("invalid_elf_create");
 static const char malformed[64]="not-an-ELF-lifetime-control";
 if(write(bad,malformed,sizeof(malformed))!=(int)sizeof(malformed)||close(bad))fail("invalid_elf_write");
 volatile unsigned char *page=map(BASE+0x200000,1,3);fill(page);
 volatile unsigned char *stack=map(BASE+0x210000,4,3);
 pid_t child=native_share_clone((void*)(stack+4*PAGE-16),failed_exec_child,(void*)page);
 if(child<=0)fail("shared_clone");if(collect(child)!=0)fail("shared_wait");
 if(page[0]!=(unsigned char)(pattern(0)^0xff))fail("shared_visibility");page[0]=pattern(0);check(page);
 printf("NATIVE_MEMORY lifetime=failed_exec pid=%d parent_pid=%d wait_status=0 verified=1\n",child,parent);
 unmap(stack,4);unmap(page,1);
 for(int detach=0;detach<2;detach++){
 int command[2],reply[2];if(pipe(command)||pipe(reply))fail("lifetime_pipes");
 pid_t owner=fork();if(owner<0)fail("owner_fork");
 if(owner==0){
  struct lifetime_args a={map(BASE+0x220000,1,3),command[0],reply[1],detach};fill(a.page);
  volatile unsigned char *survivor_stack=map(BASE+0x230000,4,3);
  /* Arguments must survive owner-stack reclamation. Store in shared VMA. */
  struct lifetime_args *stable=(struct lifetime_args*)map(BASE+0x240000,1,3);*stable=a;
  pid_t survivor=native_share_clone((void*)(survivor_stack+4*PAGE-16),survivor_child,stable);
  if(survivor<=0)fail("survivor_clone");
  if(detach){char *args[]={"test_native_memory","--lifetime-detached",NULL};char *env[]={NULL};execve("/bin/test_native_memory",args,env);fail("self_exec_returned");}
  _exit(0);
 }
 pid_t survivor;transfer(reply[0],&survivor,sizeof(survivor),0);
 if(survivor<=0||survivor==owner||collect(owner)!=0)fail("owner_wait");
 printf("NATIVE_MEMORY lifetime=%s pid=%d survivor_pid=%d parent_pid=%d wait_status=0\n",detach?"exec_owner_waited":"owner_waited",owner,survivor,parent);
 unsigned char challenge=0x71;transfer(command[1],&challenge,1,1);transfer(reply[0],&challenge,1,0);
 if(challenge!=0x72||collect(survivor)!=0)fail("survivor_wait");
 printf("NATIVE_MEMORY lifetime=%s pid=%d parent_pid=%d wait_status=0 verified=1\n",detach?"exec_detach":"parent_exit",survivor,parent);
 close(command[0]);close(command[1]);close(reply[0]);close(reply[1]);
 }
}
static const char *names[]={"cow_private","cow_mprotect_rw","ro_before_fork","ro_after_fork","lazy_ro_write","none_lazy_read","none_populated_read","nx_execute","lazy_mprotect_ro","unmap_single","unmap_middle","unmap_multiple"};
#include "test_native_shm_auth.inc"
#include "test_native_shm_exit.inc"

int main(int argc,char **argv,char **envp) {
 if(argc==2&&!strcmp(argv[1],"--lifetime-detached")){
  if(cpl()!=3)fail("exec_detached_cpl");
  printf("NATIVE_MEMORY lifetime=exec_loaded pid=%d cpl=3 loaded=1\n",getpid());_exit(0);
 }
 if(cpl()!=3)fail("cpl"); pid_t parent=getpid();
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
 lifetime_tests(parent);
 native_backing_tests(parent);
 native_shm_auth_tests(parent);
 native_shm_exit_tests(parent);
 printf("NATIVE_MEMORY complete=1 cases=12 parent_pid=%d\n",parent);
 if(usleep(10000)||cpl()!=3||getpid()!=parent)fail("progress");check(witness);
 printf("NATIVE_MEMORY progress=1 parent_pid=%d canary=1\n",parent);
 for(;;)usleep(100000);
}
