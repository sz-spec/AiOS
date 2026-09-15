/* Real ring-3 IPC authorization controls; include after transfer/collect. */
static long shm_create_test(const char *name, unsigned flags) {
 long id=syscall3(410,(long)name,PAGE,flags);
 if(id<=0||id>0x7fffffffL)fail("shm_create");return id;
}
static void shm_denied_close(long id) {
 if(syscall1(411,id)!=-13||syscall3(31,id,0,0)!=-13)fail("shm_foreign_close");
}
static void shm_shared_denial(void *argument) {
 if(cpl()!=3)fail("shm_clone_cpl");
 shm_denied_close(*(long*)argument);_exit(0);
}
static void native_shm_auth_tests(pid_t parent) {
 if(cpl()!=3)fail("shm_parent_cpl");
 if(syscall3(410,(long)"native-auth-overflow",-1L,0)!=-12||
    syscall3(410,(long)"native-auth-device",PAGE,32)!=-12)fail("shm_invalid_create");
 /* No SHM mappings exist at either fork, so children inherit no backing. */
 long id=shm_create_test("native-auth-private",0);
 pid_t child=fork();if(child<0)fail("shm_private_fork");
 if(!child){
  if(cpl()!=3)fail("shm_private_cpl");shm_denied_close(id);
  if(syscall2(412,id,0)!=0)fail("shm_private_map");_exit(0);
 }
 if(collect(child)!=0)fail("shm_private_wait");
 volatile unsigned char *p=(volatile unsigned char*)(uintptr_t)syscall2(412,id,0);
 if(!p)fail("shm_private_owner_map");fill(p);check(p);
 volatile unsigned char *clone_stack=map(BASE+0x400000,4,3);
 pid_t shared=native_share_clone((void*)(clone_stack+4*PAGE-16),shm_shared_denial,&id);
 if(shared<=0||collect(shared)!=0)fail("shm_clone_denial");
 check(p);unmap(clone_stack,4);
 if(syscall2(413,id,(long)p)||syscall1(411,id))fail("shm_private_cleanup");
 printf("NATIVE_MEMORY shm_auth=private pid=%d parent_pid=%d cpl=3 destroy_denied=1 alias_denied=1 map_denied=1 clone_denied=1 wait_status=0\n",child,parent);

 id=shm_create_test("native-auth-public",64);
 int command[2],reply[2];if(pipe(command)||pipe(reply))fail("shm_public_pipes");
 child=fork();if(child<0)fail("shm_public_fork");
 if(!child){
  close(command[1]);close(reply[0]);if(cpl()!=3)fail("shm_public_cpl");
  shm_denied_close(id);
  volatile unsigned char *shared=(volatile unsigned char*)(uintptr_t)syscall2(412,id,0);
  if(!shared)fail("shm_public_map");fill(shared);
  unsigned char ack=0x51;transfer(reply[1],&ack,1,1);
  transfer(command[0],&ack,1,0);if(ack!=0x52)fail("shm_public_challenge");
  check(shared);shared[0]^=0xff;
  if(shared[0]!=(unsigned char)(pattern(0)^0xff))fail("shm_public_survivor");
  if(syscall2(413,id,(long)shared))fail("shm_public_unmap");
  ack=0x53;transfer(reply[1],&ack,1,1);_exit(0);
 }
 close(command[0]);close(reply[1]);unsigned char ack;
 transfer(reply[0],&ack,1,0);if(ack!=0x51)fail("shm_public_ready");
 p=(volatile unsigned char*)(uintptr_t)syscall2(412,id,0);
 if(!p)fail("shm_public_owner_map");check(p);
 if(syscall2(413,id,(long)p)||syscall1(411,id))fail("shm_public_owner_close");
 ack=0x52;transfer(command[1],&ack,1,1);transfer(reply[0],&ack,1,0);
 if(ack!=0x53||collect(child)!=0)fail("shm_public_wait");
 close(command[1]);close(reply[0]);
 if(syscall1(414,id)!=0)fail("shm_public_final_ref");
 printf("NATIVE_MEMORY shm_auth=public pid=%d parent_pid=%d cpl=3 destroy_denied=1 alias_denied=1 owner_closed=1 survivor=1 wait_status=0\n",child,parent);

 long old=shm_create_test("native-auth-old",0);
 if(syscall1(411,old))fail("shm_old_close");
 long fresh=shm_create_test("native-auth-new",0);
 if(old==fresh||(old&63)!=(fresh&63))fail("shm_slot_generation");
 p=(volatile unsigned char*)(uintptr_t)syscall2(412,fresh,0);
 if(!p)fail("shm_fresh_map");fill(p);
 if(syscall1(411,old)!=-3||syscall3(31,old,0,0)!=-3)fail("shm_stale_close");
 if(syscall2(412,old,0)!=0||syscall1(414,old)!=0)fail("shm_stale_lookup");
 if(syscall2(413,old,(long)p)!=-3)fail("shm_stale_unmap");
 if(syscall3(31,fresh,2,0)!=-22||syscall3(31,fresh,99,0)!=-22)fail("shm_alias_command");
 long wide=fresh+0x100000000L;
 if(syscall1(411,wide)!=-22||syscall3(31,wide,0,0)!=-22)fail("shm_wide_id");
 if(syscall2(412,wide,0)!=-22||syscall2(413,wide,(long)p)!=-22||syscall1(414,wide)!=-22)
  fail("shm_wide_mapping_id");
 check(p);if(syscall1(414,fresh)!=PAGE)fail("shm_fresh_size");
 if(syscall2(413,fresh,(long)p)||syscall1(411,fresh))fail("shm_fresh_cleanup");
 printf("NATIVE_MEMORY shm_auth=stale pid=%d cpl=3 old=%ld new=%ld slot=%ld canonical=1 alias=1 unsupported=1 wide=1 canary=1\n",parent,old,fresh,fresh&63);
}
