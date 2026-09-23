import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from core.fleet.bus import FleetBus

bus = FleetBus(base_dir=config.resolve_path('data/fleet'))
print('bus.last_seq:', bus.last_seq)
snapshot, tail = bus.load()
print('snapshot last_seq:', snapshot.get('last_seq'))
print('snapshot state keys:', list(snapshot.get('state', {}).keys()))
print('tail length:', len(tail))
for ev in tail:
    print(f'  seq={ev.seq} kind={ev.kind}')
print('replayed count:', len(tail))