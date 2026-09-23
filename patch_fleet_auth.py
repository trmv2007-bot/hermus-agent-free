import pathlib

p = pathlib.Path("gateway/routes_fleet.py")
txt = p.read_text(encoding="utf-8")

old = '    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")'
new = '    expected = getattr(config, "gateway_api_token", None) or os.getenv("HERMUS_GATEWAY_TOKEN")'
assert old in txt, "old fleet auth line not found"
txt = txt.replace(old, new, 1)
p.write_text(txt, encoding="utf-8")
print("Patched _fleet_ws_auth to use getattr")
