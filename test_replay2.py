import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
from core.config import config
from core.fleet.bus import FleetBus
from core.fleet.registry import FleetRegistry, chat_via_freellm, KIND_AGENT_SPAWNED, STATE_CHANGED, KIND_AGENT_UPDATED, DESTROYED, AGENT_STATES

bus = FleetBus(base_dir=config.resolve_path('data/fleet'))
reg = FleetRegistry(bus, chat_fn=chat_via_freellm)

# Manually replay to debug
snapshot, tail = bus.load()
print('Tail events:', len(tail))

for ev in tail:
    print(f'Processing seq={ev.seq} kind={ev.kind}')
    content = ev.content if isinstance(ev.content, dict) else {}
    kind = ev.kind
    
    if kind == KIND_AGENT_SPAWNED:
        data = content.get("agent")
        if not isinstance(data, dict):
            print(f'  -> False: not dict')
            continue
        from core.fleet.registry import LiveAgent
        agent = LiveAgent.from_dict(data)
        print(f'  -> Adding agent: {agent.name} ({agent.agent_id})')
        reg._agents[agent.agent_id] = agent
    elif kind == KIND_AGENT_UPDATED:
        aid = str(content.get("agent_id") or "")
        data = content.get("agent")
        if not aid:
            print(f'  -> False: no aid')
            continue
        if isinstance(data, dict):
            from core.fleet.registry import LiveAgent
            reg._agents[aid] = LiveAgent.from_dict(data)
            print(f'  -> Updated agent: {aid}')
        else:
            print(f'  -> False: data not dict')
    elif kind == STATE_CHANGED and content.get("scope") == "agent":
        aid = str(content.get("agent_id") or "")
        target = str(content.get("to") or "")
        agent = reg._agents.get(aid)
        if agent is None:
            print(f'  -> False: agent {aid} not found')
            continue
        if target == DESTROYED:
            print(f'  -> DESTROYED: {agent.name} ({aid})')
            reg._agents.pop(aid, None)
        elif target in AGENT_STATES:
            agent.state = target
            agent.last_activity = ev.ts
            print(f'  -> State change: {agent.name} ({aid}) -> {target}')
        else:
            print(f'  -> False: invalid target {target}')
    else:
        print(f'  -> Skipped')

print('\nFinal agents:')
for aid, agent in reg._agents.items():
    print(f'  {agent.name} ({aid}) state={agent.state}')