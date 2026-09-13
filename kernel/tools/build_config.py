"""Record effective build inputs without touching an unchanged stamp."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
inputs = {key: value for key, value in os.environ.items()
          if key.startswith(sys.argv[2] if len(sys.argv) > 2 else 'VOS_CONFIG_')}
if len(sys.argv) > 3:
    inputs['files'] = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                       for name in sys.argv[3:]}
content = json.dumps(inputs, sort_keys=True, indent=2) + '\n'
if not path.exists() or path.read_text() != content:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    # Older make versions compare mtimes at whole-second precision. Ensure a
    # changed stamp is newer even when options are toggled within one second.
    if path.exists():
        now = time.time()
        time.sleep(int(now) + 1 - now + 0.01)
    temporary.write_text(content)
    temporary.replace(path)
