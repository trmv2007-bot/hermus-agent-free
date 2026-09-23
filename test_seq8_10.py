import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from core.fleet.bus import FleetBus

bus = FleetBus(base_dir=config.resolve_path('data/fleet'))
snapshot, tail = bus.load()

for ev in tail:
    if ev.seq in (8, 10):
        print(f'Event {ev.seq}:')
        print(f'  sender: {ev.sender}')
        print(f'  kind: {ev.kind}')
        print(f'  content: {ev.content}')
        print()