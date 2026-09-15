/* Creator-task lifetime tests. Registry retirement is not a PMM-free oracle. */
static void shm_wait_retired(long id) {
 for(unsigned i=0;i<5000;i++) {
  long size=syscall1(414,id);
  if(size==0)return;
  if(size!=PAGE)fail("shm_exit_size");
  if(usleep(1000))fail("shm_exit_sleep");
 }
 fail("shm_exit_retirement_timeout");
}

static void native_shm_exit_tests(pid_t parent) {
 static const char *names[]={"unmapped","mapped","explicit"};
 if(cpl()!=3)fail("shm_exit_parent_cpl");
 for(unsigned mode=0;mode<4;mode++) {
  int command[2],reply[2];
  if(pipe(command)||pipe(reply))fail("shm_exit_pipes");
  /* The previous iteration has detached its mapping before this fork. */
  pid_t child=fork();if(child<0)fail("shm_exit_fork");
  if(child==0) {
   close(command[1]);close(reply[0]);
   if(cpl()!=3)fail("shm_exit_child_cpl");
   long id=shm_create_test("native-creator-exit",(mode==1||mode==2)?64:0);
   transfer(reply[1],&id,sizeof(id),1);
   unsigned char ready;
   transfer(command[0],&ready,1,0);
   if(ready!=0x61)fail("shm_exit_child_challenge");
   if(mode==2&&syscall1(411,id)!=0)fail("shm_exit_explicit_close");
   if(mode==3) {
    printf("NATIVE_MEMORY shm_exit=fault pid=%d address=0x7400000000 operation=read cpl=3 attempt=1\n",getpid());
    volatile unsigned char value=*(volatile unsigned char*)0x7400000000ULL;
    (void)value;fail("shm_exit_fault_returned");
   }
   _exit(0);
  }
  close(command[0]);close(reply[1]);
  long id=0;transfer(reply[0],&id,sizeof(id),0);
  if(id<=0||id>0x7fffffffL)fail("shm_exit_handle");
  volatile unsigned char *shared=NULL;
  if(mode==1||mode==2) {
   long mapped=syscall2(412,id,0);
   if(mapped<=0)fail("shm_exit_parent_map");
   shared=(volatile unsigned char*)(uintptr_t)mapped;
   fill(shared);check(shared);
  }
  unsigned char ready=0x61;transfer(command[1],&ready,1,1);
  /* Retirement before wait proves creator cleanup does not need waitpid. */
  if(mode==0||mode==3)shm_wait_retired(id);
  if(collect(child)!=(mode==3?35584:0))fail("shm_exit_wait");
  close(command[1]);close(reply[0]);
  if(mode==1||mode==2) {
   /* This read/write occurs only after the creator has exited. */
   if(syscall1(414,id)!=PAGE)fail("shm_exit_live_region");
   check(shared);
   for(unsigned i=0;i<PAGE;i++)shared[i]^=0xff;
   for(unsigned i=0;i<PAGE;i++)
    if(shared[i]!=(unsigned char)(pattern(i)^0xff))fail("shm_exit_survivor_write");
   if(syscall2(413,id,(long)shared)!=0)fail("shm_exit_last_unmap");
  }
  /* waitpid observes task termination, not necessarily deferred cleanup. */
  shm_wait_retired(id);
  if(mode==3)
   printf("NATIVE_MEMORY shm_exit=fault pid=%d parent_pid=%d cpl=3 wait_status=35584 retired=1\n",child,parent);
  else if(!mode)
   printf("NATIVE_MEMORY shm_exit=unmapped pid=%d parent_pid=%d cpl=3 wait_status=0 retired=1\n",child,parent);
  else
   printf("NATIVE_MEMORY shm_exit=%s pid=%d parent_pid=%d cpl=3 wait_status=0 survivor=1 retired=1\n",names[mode],child,parent);
 }
}
