"""Manual Docker integration check; no host ports or external network access."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

IMAGE = 'ghcr.io/felixmosh/bull-board:9.10.1'
prefix = 'vos5-board-check-' + uuid.uuid4().hex[:10]
redis_name, board_name = prefix + '-redis', prefix + '-ui'
created = []

def docker(*args, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise RuntimeError(result.stderr[-1500:])
    return result

try:
    docker('run', '-d', '--name', redis_name, '--network', 'none', '--read-only', '--tmpfs', '/data',
           'redis:8.10.1-alpine', 'redis-server', '--save', '', '--appendonly', 'no')
    created.append(redis_name)
    for _ in range(40):
        if docker('exec', redis_name, 'redis-cli', 'ping', check=False).stdout.strip() == 'PONG':
            break
        time.sleep(.25)
    else:
        raise RuntimeError('isolated Redis did not start')
    docker('run', '-d', '--name', board_name, '--network', 'container:' + redis_name,
           '--read-only', '--tmpfs', '/tmp', '-e', 'BULL_BOARD_REDIS_URL=redis://127.0.0.1:6379',
           '-e', 'BULL_BOARD_USER=smoke-user', '-e', 'BULL_BOARD_PASSWORD=smoke-only-password',
           '-e', 'BULL_BOARD_SCAN_INTERVAL=1', IMAGE)
    created.append(board_name)
    # Seed an actual Bull job, exercising compatibility with the existing queue family.
    docker('exec', board_name, 'node', '-e', """
const Queue=require('/opt/bull-board/node_modules/bull');
(async()=>{const q=new Queue('code-reviews','redis://127.0.0.1:6379');
await q.add({probe:'isolated-migration-check'});await q.close();})().catch(e=>{console.error(e);process.exit(1)});
""")
    result = docker('exec', board_name, 'node', '-e', """
const assert=require('node:assert/strict');
const base='http://127.0.0.1:3000';
const auth='Basic '+Buffer.from('smoke-user:smoke-only-password').toString('base64');
(async()=>{
 let ready=false;
 for(let i=0;i<80;i++){try{await fetch(base);ready=true;break}catch{await new Promise(r=>setTimeout(r,250))}}
 assert.ok(ready,'dashboard failed to start');
 assert.equal((await fetch(base)).status,401);
 assert.equal((await fetch(base,{headers:{authorization:'Basic '+Buffer.from('smoke-user:wrong').toString('base64')}})).status,401);
 assert.equal((await fetch(base,{headers:{authorization:auth}})).status,200);
 let found=false;
 for(let i=0;i<40;i++){
 const r=await fetch(base+'/api/queues',{headers:{authorization:auth}});
 if(r.ok&&(await r.text()).includes('code-reviews')){found=true;break;}
 await new Promise(r=>setTimeout(r,250));
 }
 assert.ok(found,'Bull code-reviews queue missing from dashboard API');
 assert.equal((await fetch(base+'/api/queues')).status,401);
 console.log(JSON.stringify({unauthenticated:401,wrong_password:401,authenticated:200,bull_queue_visible:true,unauthenticated_queue_api:401}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
    print(result.stdout.strip())
    # Compose refuses missing credentials before creating any container.
    env = dict(os.environ)
    env.update(GITHUB_WEBHOOK_SECRET='smoke-only',GITHUB_TOKEN='',ANTHROPIC_API_KEY='')
    env.pop('BULL_BOARD_USER',None);env.pop('BULL_BOARD_PASSWORD',None)
    config = subprocess.run(['docker','compose','-f',str(Path(__file__).resolve().parents[1]/'docker-compose.webhook.yml'),'config','--quiet'], env=env,capture_output=True,text=True)
    assert config.returncode != 0 and 'BULL_BOARD_USER' in config.stderr
    print('Compose missing-dashboard-credentials rejection passed')
finally:
    for name in reversed(created):
        docker('rm','-f',name,check=False)
