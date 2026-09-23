import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from pathlib import Path
import json

p = Path(config.resolve_path('data/fleet/snapshot.json'))
print('exists:', p.exists())
if p.exists():
    print(json.dumps(json.load(open(p)), indent=2)[:3000])