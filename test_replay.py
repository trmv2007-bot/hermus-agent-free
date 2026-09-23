import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from core.fleet.bus import FleetBus

bus = FleetBus(base_dir=config.resolve_path('data/fleet'))
snapshot, tail = bus.load()

for ev in tail:
    if ev.seq == 9:
        print('Event 9:', ev.content)
        break