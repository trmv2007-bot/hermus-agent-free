import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from pathlib import Path

d = Path(config.resolve_path('data/fleet'))
for f in d.glob('bus-*.jsonl'):
    print('===', f.name, '===')
    content = open(f, encoding='utf-8').read()
    lines = content.strip().split('\n')
    print('Total lines:', len(lines))
    for line in lines:
        import json
        try:
            event = json.loads(line)
            print(f'  seq={event.get("seq")} kind={event.get("kind")} sender={event.get("sender")}')
        except:
            pass