/* Architecture hooks only: protocol under test is a separate production TU. */
#include "vos/tlb_shootdown.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *scenario;
static uint32_t cpu, count=1;
static unsigned flushed[8],sent[8],pauses;
static int nested_result=999,duplicate_delivered;
static void check(int ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s: %s\n",scenario,why);exit(1);}}
uint32_t vos3_tlb_arch_cpu(void){return cpu;}
uint32_t vos3_tlb_arch_count(void){return count;}
int vos3_tlb_arch_target(uint32_t target,uint32_t *apic){
 if(target>=count||target==2)return 0;
 *apic=(!strcmp(scenario,"duplicate_apic")?10:10+target*7);return 1;
}
uint64_t vos3_tlb_arch_irq_save(void){return 0x200;}
void vos3_tlb_arch_irq_restore(uint64_t flags){check(flags==0x200,"IRQ state restored");}
void vos3_tlb_arch_flush_all(void){check(cpu<8,"CPU bound");flushed[cpu]++;}
static void deliver(uint32_t target){uint32_t previous=cpu;cpu=target;vos3_tlb_shootdown_handle();cpu=previous;}
int vos3_tlb_arch_send(uint32_t apic){
 check(apic>=10&&(apic-10)%7==0,"APIC translation");uint32_t target=(apic-10)/7;check(target<8&&target!=0&&target!=2,"only remote started targets");sent[target]++;
 if(!strcmp(scenario,"send_failure")||(!strcmp(scenario,"partial_send_failure")&&target==3))return -1;
 if(!strcmp(scenario,"nested")){nested_result=vos3_tlb_shootdown_checked(0x4000,4096);deliver(target);}
 else if(strcmp(scenario,"timeout")&&strcmp(scenario,"duplicate"))deliver(target);
 return 0;
}
void vos3_tlb_arch_pause(void){
 pauses++;
 if(!strcmp(scenario,"duplicate")){
  if(pauses==1){deliver(1);deliver(1);duplicate_delivered=1;}
  if(pauses==4)deliver(3);
 }
}
int main(int argc,char **argv){
 check(argc==2,"scenario argument");scenario=argv[1];int r;
 if(!strcmp(scenario,"pre_smp")){count=0;r=vos3_tlb_shootdown_checked(0x1000,4096);check(r==0&&flushed[0]==1,"pre-SMP local flush");}
 else if(!strcmp(scenario,"duplicate_apic")){count=2;r=vos3_tlb_shootdown_checked(0x1000,4096);check(r==-22&&flushed[0]==0&&sent[1]==0,"duplicate APIC rejected before effects");}
 else if(!strcmp(scenario,"old_ack")){count=2;r=vos3_tlb_shootdown_checked(0x1000,4096);check(r==0,"first request completes");scenario="timeout";r=vos3_tlb_shootdown_checked(0x2000,4096);check(r==-110,"prior acknowledgement cannot complete next request");scenario="old_ack";}
 else if(!strcmp(scenario,"empty")){r=vos3_tlb_shootdown_checked(0x1000,0);check(r==0,"empty succeeds");}
 else if(!strcmp(scenario,"unaligned")){r=vos3_tlb_shootdown_checked(0x1fff,2);check(r==0&&flushed[0]==1,"cross-page full flush");}
 else if(!strcmp(scenario,"top")){r=vos3_tlb_shootdown_checked(UINTPTR_MAX,1);check(r==0&&flushed[0]==1,"last canonical byte");}
 else if(!strcmp(scenario,"overflow")){r=vos3_tlb_shootdown_checked(UINTPTR_MAX,2);check(r==-22&&flushed[0]==0,"overflow rejected before effects");}
 else if(!strcmp(scenario,"hole")){r=vos3_tlb_shootdown_checked(0x00007fffffffffffULL,2);check(r==-22&&flushed[0]==0,"canonical hole rejected");}
 else if(!strcmp(scenario,"sparse")){count=4;r=vos3_tlb_shootdown_checked(0x2000,8192);check(r==0&&sent[1]==1&&sent[3]==1&&flushed[1]==1&&flushed[3]==1,"started sparse targets complete");}
 else if(!strcmp(scenario,"duplicate")){count=4;r=vos3_tlb_shootdown_checked(0x2000,4096);check(r==0&&duplicate_delivered&&pauses>=4&&flushed[3]==1,"duplicate cannot replace missing CPU");}
 else if(!strcmp(scenario,"timeout")){count=2;r=vos3_tlb_shootdown_checked(0x2000,4096);check(r==-110,"missing target times out");deliver(1);r=vos3_tlb_shootdown_checked(0x3000,4096);check(r==-5,"late handler cannot unpoison request");}
 else if(!strcmp(scenario,"send_failure")||!strcmp(scenario,"partial_send_failure")){
 count=4;r=vos3_tlb_shootdown_checked(0x2000,4096);check(r==-110,"delivery failure returns timeout");
 check(pauses==0,"delivery failure does not wait");
 if(!strcmp(scenario,"partial_send_failure"))check(flushed[1]==1&&sent[3]==1,"earlier CPU completed before later send failed");
 unsigned previous=sent[1]+sent[3];deliver(1);deliver(3);
 r=vos3_tlb_shootdown_checked(0x3000,4096);check(r==-5&&sent[1]+sent[3]==previous,"delivery failure permanently poisons retries");
 }
 else if(!strcmp(scenario,"nested")){count=2;r=vos3_tlb_shootdown_checked(0x2000,4096);check(nested_result==-16&&r==0,"nested busy cannot overwrite outer request");}
 else check(0,"unknown scenario");
 printf("PASS %s\n",scenario);return 0;
}
