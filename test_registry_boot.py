import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from core.fleet.bus import FleetBus
from core.fleet.registry import FleetRegistry, chat_via_freellm

bus = FleetBus(base_dir=config.resolve_path('data/fleet'))
reg = FleetRegistry(bus, chat_fn=chat_via_freellm)
reg.boot()
agents = reg.list()
print('Agents after boot:', [(a.name, a.agent_id, a.state) for a in agents])