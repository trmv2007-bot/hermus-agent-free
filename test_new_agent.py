import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from core.fleet.bus import FleetBus

bus = FleetBus(base_dir=config.resolve_path('data/fleet'))
print('last_seq:', bus.last_seq)
snapshot, tail = bus.load()
print('tail length:', len(tail))
for ev in tail[-5:]:
    print(f'  seq={ev.seq} kind={ev.kind}')