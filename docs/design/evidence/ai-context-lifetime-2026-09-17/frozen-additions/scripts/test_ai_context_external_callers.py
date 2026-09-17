"""Compile the actual telemetry snapshot with controlled retained references."""
from pathlib import Path
import subprocess
import hashlib
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT = Path(__file__).resolve().parents[1]
class ExternalContextCallers(unittest.TestCase):
    def test_appload_denies_without_launch_side_effects(self):
        actual = function((ROOT/'kernel/src/drivers/vbus_app_cmds.c').read_text(), 'void cmd_appload(')
        # Deliberately provide no allocator, task factory, context or scheduler
        # implementation: a side-effect dependency makes this actual-C link fail.
        code = """#include <assert.h>
#include <stddef.h>
#include <string.h>
static int errors;
static void send_err(int code,const char* reason){assert(code==95);assert(strstr(reason,"unsupported"));errors++;}
""" + actual + """
int main(void){cmd_appload("7","/bin/init");cmd_appload(NULL,NULL);cmd_appload("263","/bin/hostile");assert(errors==3);return 0;}
"""
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'test.c').write_text(code)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr)

    def test_preserved_management_commands(self):
        # Frozen function-body fixtures from baseline f8c0b82, with only the
        # reviewed APPLIST acquire/put conversion applied. No runtime Git or
        # mutable HEAD dependency: this also works in archived source trees.
        current = (ROOT/'kernel/src/drivers/vbus_app_cmds.c').read_text()
        expected = {'cmd_appstat': '58669b483d856dc6abba673a33a462d4c9073e59f1ac037a97e65abafb57cfa0', 'cmd_appkill': 'd8211ee48695713c4d067639f4f64c63e0515b9b431da9b0f84c84241029239b', 'cmd_applogs': '678ded3324cdc7a1c74208a2d1ce3a244c1da2c2c7fe849ccfc46cf612970bbb', 'cmd_agent_kill_all': 'a9ae751df1bfcf8b165b0be628304ee75e2eb687dddaf3c2b66710bad53fe41e', 'cmd_applist': '988469287f6a0565e6a27d3ef9aa6b1b48c6c9aaadffb2a51a34b3c4e2d5f5c8'}
        for name, digest in expected.items():
            actual = function(current, 'void '+name+'(')
            self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(), digest, name)

    def test_syscall_ids_rejected_before_narrowing(self):
        source = (ROOT/'kernel/src/arch/x86_64/syscall.c').read_text()
        actual = '\n'.join(function(source, 'int64_t vos3_sys_app_ctx_'+name+'(') for name in ('create','destroy','switch'))
        code = r'''#include <assert.h>
#include <stdint.h>
#define VOS3_MAX_APP_CONTEXTS 8
#define VOS3_AI_GUARD_ERR_INVALID -1
#define VOS3_DEBUG(...) ((void)0)
typedef struct {uint64_t rdi;} vos3_syscall_frame_t;
static unsigned calls;
static int dispatch(uint8_t id){assert(id==7);calls++;return 123;}
#define vos3_ai_guard_create_app_ctx dispatch
#define vos3_ai_guard_destroy_app_ctx dispatch
#define vos3_ai_guard_switch_ctx dispatch
''' + actual + r'''
int main(void){
 int64_t (*handlers[])(vos3_syscall_frame_t*)={vos3_sys_app_ctx_create,vos3_sys_app_ctx_destroy,vos3_sys_app_ctx_switch};
 uint64_t invalid[]={8,263,UINT64_MAX};
 for(unsigned h=0;h<3;h++){
  for(unsigned i=0;i<3;i++){vos3_syscall_frame_t f={invalid[i]};unsigned before=calls;assert(handlers[h](&f)==VOS3_AI_GUARD_ERR_INVALID);assert(calls==before);}
  vos3_syscall_frame_t f={7};assert(handlers[h](&f)==123);
 }
 assert(calls==3);return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'test.c').write_text(code)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr)

    def test_snapshot_releases_pin_on_every_return(self):
        actual = function((ROOT/'kernel/src/mm/ai_telemetry.c').read_text(), 'int64_t vos3_ai_telemetry_snapshot(void* buffer, size_t buffer_size)\n')
        code = '''#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define VOS3_AI_TELEMETRY_MAGIC 0x1234
#define VOS3_AI_TELEMETRY_VERSION 1
typedef struct region {struct region *next;uint64_t id,size,base,type,state,numa_node,flags;struct {uint64_t read_count,write_count,fault_count,last_access_time;} stats;struct {unsigned ref_count;} *shared;} vos3_ai_guard_region_t;
typedef struct {vos3_ai_guard_region_t *regions;} vos3_ai_guard_ctx_t;
typedef struct {uint64_t magic,version,timestamp,region_count,alert_count,total_bytes;} vos3_ai_telemetry_header_t;
typedef struct {uint64_t region_id,pid,type,state,numa_node,flags,base,size,read_count,write_count,fault_count,last_access_time,ref_count,_padding;} vos3_ai_telemetry_region_t;
static struct {unsigned count;} g_alert_queue;
static vos3_ai_guard_ctx_t context;
static int available, acquired, released;
vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_global_ctx(void){if(!available)return NULL;acquired++;return &context;}
void vos3_ai_guard_ctx_put(vos3_ai_guard_ctx_t* p){assert(p==&context);released++;}
uint64_t vos3_timer_get_ticks(void){return 100;}
''' + actual + '''
int main(void){
 unsigned char out[8192];
 assert(vos3_ai_telemetry_snapshot(NULL,sizeof out)==-1);
 assert(vos3_ai_telemetry_snapshot(out,0)==-1);assert(acquired==0);
 available=1;assert(vos3_ai_telemetry_snapshot(out,1)==-3);assert(acquired==1&&released==1);
 memset(&context,0,sizeof context);
 assert(vos3_ai_telemetry_snapshot(out,sizeof out)==sizeof(vos3_ai_telemetry_header_t));
 assert(acquired==2&&released==2);
 vos3_ai_guard_region_t region;memset(&region,0,sizeof region);region.size=4096;region.id=12;context.regions=&region;
 assert(vos3_ai_telemetry_snapshot(out,sizeof out)==sizeof(vos3_ai_telemetry_header_t)+sizeof(vos3_ai_telemetry_region_t));
 assert(acquired==3&&released==3);
 assert(((vos3_ai_telemetry_header_t*)out)->region_count==1);
 assert(((vos3_ai_telemetry_header_t*)out)->total_bytes==4096);
 available=0;assert(vos3_ai_telemetry_snapshot(out,sizeof out)==sizeof(vos3_ai_telemetry_header_t));
 assert(acquired==3&&released==3);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'test.c').write_text(code)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(ROOT/'kernel/include'),str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr)
if __name__=='__main__':unittest.main()
