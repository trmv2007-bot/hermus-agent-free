import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from pathlib import Path

d = Path(config.resolve_path('data/fleet'))
for f in d.glob('bus-*.jsonl'):
    print('===', f.name, '===')
    content = open(f, encoding='utf-8').read()
    print(content[:3000])
    print('...')
    print('Total lines:', content.count('\n'))