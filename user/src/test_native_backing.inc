/* Included by the gated memory workload after its syscall/test helpers. */
static unsigned char backing_byte(unsigned page,unsigned offset) {
 return (unsigned char)(0x39U+page*23U+offset*17U);
}
static void backing_verify(volatile unsigned char *base) {
 static const unsigned pages[]={0,2,4,5,6};
 for(unsigned j=0;j<5;j++)for(unsigned i=0;i<PAGE;i++)
  if(base[pages[j]*PAGE+i]!=backing_byte(pages[j],i))fail("backing_contents");
}
static void native_backing_tests(pid_t parent) {
 int fd=open("/tmp/native-backing-owned",O_CREAT|O_TRUNC|O_RDWR,0600);
 if(fd<0)fail("backing_open");
 unsigned char bytes[PAGE];
 for(unsigned page=0;page<7;page++){
  for(unsigned i=0;i<PAGE;i++)bytes[i]=backing_byte(page,i);
  if(write(fd,bytes,PAGE)!=(int)PAGE)fail("backing_write");
 }
 uintptr_t addr=BASE+0x300000;
 if((uintptr_t)syscall6(9,addr,7*PAGE,3,0x12,fd,0)!=addr)fail("backing_map");
 if(close(fd)!=0)fail("backing_close");
 int unrelated=open("/tmp/native-backing-sentinel",O_CREAT|O_TRUNC|O_RDWR,0600);
 if(unrelated<0)fail("backing_sentinel_open");
 if(unrelated!=fd){if(dup2(unrelated,fd)!=fd)fail("backing_reuse");close(unrelated);unrelated=fd;}
 unsigned char sentinel=0xe7;
 if(write(unrelated,&sentinel,1)!=1)fail("backing_sentinel_write");
 volatile unsigned char *base=(volatile unsigned char*)addr;
 /* No backing page has faulted yet: split both through mprotect and munmap. */
 protect(base+3*PAGE,1,1);
 unmap(base+PAGE,1);
 unmap(base+3*PAGE,1);
 pid_t child=fork();if(child<0)fail("backing_fork");
 if(child==0){if(cpl()!=3)fail("backing_cpl");backing_verify(base);_exit(0);}
 if(collect(child)!=0)fail("backing_child");
 backing_verify(base);
 unmap(base,1);unmap(base+2*PAGE,1);unmap(base+4*PAGE,3);
 sentinel=0;
 if(lseek(unrelated,0,0)!=0||read(unrelated,&sentinel,1)!=1||sentinel!=0xe7)
  fail("backing_unrelated_fd");
 if(close(unrelated)!=0)fail("backing_sentinel_close");
 if(unlink("/tmp/native-backing-owned")||unlink("/tmp/native-backing-sentinel"))fail("backing_unlink");
 printf("NATIVE_MEMORY backing=owned pid=%d closed_fd=1 reused_fd=1 split=1 clone=1 contents=1\n",parent);
}
