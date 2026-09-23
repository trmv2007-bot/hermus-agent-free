"""Part 1: Add fleetWs/screenWs state vars."""
import pathlib
p = pathlib.Path("gateway/static/control-room.js")
t = p.read_text(encoding="utf-8", errors="replace")
old = "let tmWs = null;"
new = "let tmWs = null;\nlet fleetWs = null;\nlet screenWs = null;\nlet fleetWsReconnectTimer = null;\nlet screenWsReconnectTimer = null;"
assert old in t
p.write_text(t.replace(old, new, 1), encoding="utf-8", errors="replace")
print("part1 OK")
