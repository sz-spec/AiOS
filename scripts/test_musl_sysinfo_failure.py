"""Compile actual musl memory-query cases and legacy callers with syscall mock."""
from pathlib import Path
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT = Path(__file__).resolve().parents[1]

class MuslSysinfoFailure(unittest.TestCase):
    def test_real_linux_wrapper_errno(self):
        actual = function((ROOT/'user/musl/src/linux/sysinfo.c').read_text(), 'int __lsysinfo(')
        code = r'''#include <assert.h>
#include <errno.h>
#define SYS_sysinfo 99
struct sysinfo {unsigned char bytes[368];};
static long mock_syscall(int number, struct sysinfo *out){
 assert(number==99);(void)out;errno=ENOSYS;return -1;
}
#define syscall mock_syscall
''' + actual + r'''
int main(void){struct sysinfo out;for(unsigned i=0;i<sizeof out;i++)out.bytes[i]=0xa5;
errno=0;assert(__lsysinfo(&out)==-1&&errno==ENOSYS);
for(unsigned i=0;i<sizeof out;i++)assert(out.bytes[i]==0xa5);return 0;}
'''
        with tempfile.TemporaryDirectory(prefix='vos-musl-wrapper-') as directory:
            p=Path(directory);(p/'test.c').write_text(code)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)

    def test_failure_errno_and_success_units(self):
        source = (ROOT/'user/musl/src/conf/sysconf.c').read_text()
        begin = source.index('\tcase JT_PHYS_PAGES & 255:')
        end = source.index('\tcase JT_MINSIGSTKSZ & 255:', begin)
        cases = source[begin:end]
        legacy = (ROOT/'user/musl/src/conf/legacy.c').read_text()
        wrappers = '\n'.join(function(legacy, 'long '+name+'(') for name in ('get_phys_pages', 'get_avphys_pages'))
        loadavg = function((ROOT/'user/musl/src/legacy/getloadavg.c').read_text(), 'int getloadavg(')
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <limits.h>
#include <errno.h>
#define JT_PHYS_PAGES 8
#define JT_AVPHYS_PAGES 9
#define _SC_PHYS_PAGES 8
#define _SC_AVPHYS_PAGES 9
#define PAGE_SIZE 4096
#define SI_LOAD_SHIFT 16
struct sysinfo {unsigned long loads[3];unsigned long totalram,freeram,bufferram;unsigned mem_unit;};
static struct sysinfo fixture;
static int failure,calls;
static int __lsysinfo(struct sysinfo *out){
 calls++;
 if(failure){errno=failure;return -1;} /* Deliberately leave output untouched. */
 *out=fixture;return 0;
}
static long tested_sysconf(int name){switch(name){
''' + cases + r'''
default:return -1;}}
#define sysconf tested_sysconf
''' + wrappers + '\n#define sysinfo(p) __lsysinfo(p)\n' + loadavg + r'''
int main(void){
 long (*queries[])(void)={get_phys_pages,get_avphys_pages};
 for(unsigned i=0;i<2;i++){
  failure=ENOSYS;errno=0;assert(queries[i]()==-1&&errno==ENOSYS);
  failure=EFAULT;errno=0;assert(queries[i]()==-1&&errno==EFAULT);
 }
 assert(calls==4);
 double output[4]={10,20,30,40};
 failure=ENOSYS;errno=0;assert(getloadavg(output,3)==-1&&errno==ENOSYS);
 assert(output[0]==10&&output[1]==20&&output[2]==30&&output[3]==40);
 int before=calls;assert(getloadavg(output,0)==0&&getloadavg(output,-1)==-1&&calls==before);
 failure=0;fixture.loads[0]=65536;fixture.loads[1]=131072;fixture.loads[2]=32768;
 assert(getloadavg(output,4)==3&&output[0]==1.0&&output[1]==2.0&&output[2]==0.5&&output[3]==40);

 failure=0;fixture=(struct sysinfo){.totalram=16384,.freeram=8192,.bufferram=4096,.mem_unit=1};
 errno=EDOM;assert(get_phys_pages()==4&&errno==EDOM);assert(get_avphys_pages()==3);
 fixture.mem_unit=0;assert(get_phys_pages()==4&&get_avphys_pages()==3);
 fixture=(struct sysinfo){.totalram=9,.freeram=5,.bufferram=2,.mem_unit=4096};
 assert(get_phys_pages()==9&&get_avphys_pages()==7);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-musl-sysinfo-') as directory:
            p=Path(directory);(p/'test.c').write_text(code)
            result=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
