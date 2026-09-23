"""Fix poll to use _iter_events via a wrapper that materialises the list in-thread."""
import pathlib

p = pathlib.Path("gateway/routes_fleet.py")
txt = p.read_text(encoding="utf-8")

old = "new_events = await asyncio.to_thread(bus.read_events_after, last_seq + 1)"
new = """new_events = await asyncio.to_thread(
                lambda: list(bus._iter_events(last_seq + 1))
            )"""
assert old in txt, "old poll line not found"
txt = txt.replace(old, new, 1)
print("Step 5 OK")
p.write_text(txt, encoding="utf-8")
