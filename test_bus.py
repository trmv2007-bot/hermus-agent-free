import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from core.fleet.bus import FleetBus

bus = FleetBus(base_dir=config.resolve_path("data/fleet"))
print('last_seq:', bus.last_seq)
print('log_paths:', bus.log_paths())
snapshot, tail = bus.load()
print('snapshot keys:', snapshot.keys() if snapshot else None)
print('state keys:', snapshot.get('state', {}).keys() if snapshot and 'state' in snapshot else None)
agents_state = snapshot.get('state', {}).get('agents', {}) if snapshot and 'state' in snapshot else {}
print('agents in snapshot:', len(agents_state))
for aid, data in agents_state.items():
    print('  -', aid, data.get('name'))
print('tail events:', len(tail))
for ev in tail:
    print('  -', ev.kind, ev.seq, ev.content.get('agent', {}).get('name') if isinstance(ev.content, dict) else '')