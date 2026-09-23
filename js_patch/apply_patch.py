"""Apply all JS patches to control-room.js."""
import pathlib

base = pathlib.Path("js_patch")
cr = pathlib.Path("gateway/static/control-room.js")
txt = cr.read_text(encoding="utf-8", errors="replace")

fleet_ws = base.joinpath("fleet_ws.js").read_text(encoding="utf-8", errors="replace")
screen_panel = base.joinpath("screen_panel.js").read_text(encoding="utf-8", errors="replace")
approval_panel = base.joinpath("approval_panel.js").read_text(encoding="utf-8", errors="replace")
onboarding = base.joinpath("onboarding.js").read_text(encoding="utf-8", errors="replace")

# Build the insertion block
insert_block = fleet_ws + "\n\n" + screen_panel + "\n\n" + approval_panel + "\n\n" + onboarding + "\n\n"

# Insert before boot()
boot_marker = "async function boot()"
assert boot_marker in txt, "boot() not found"
txt = txt.replace(boot_marker, insert_block + boot_marker, 1)
print("Step 1: inserted JS fragments before boot()")

# Add postJSON helper if not present
if "function postJSON" not in txt:
    post_helper = "\nfunction postJSON(url, body) {\n  return requestJSON(url, { method: \"POST\", headers: { \"Content-Type\": \"application/json\" }, body: JSON.stringify(body) });\n}\n"
    marker = "  }));\n}\n\n/* getJSON"
    assert marker in txt, "requestJSON end marker not found"
    txt = txt.replace(marker, "  }));\n}\n" + post_helper + "/* getJSON", 1)
    print("Step 2: added postJSON helper")

# Modify boot() to call new init functions
old_boot = """async function boot(){
  await refreshOverview();
  await refreshPresence();
  await refreshJobs();
  try { await refreshMissions(); } catch(e){}
  await refreshAgents();
  refreshTelemetry(true);
  openTmStream();
}"""

new_boot = """async function boot(){
  await refreshOverview();
  await refreshPresence();
  await refreshJobs();
  try { await refreshMissions(); } catch(e){}
  await refreshAgents();
  refreshTelemetry(true);
  openTmStream();
  initFleetWs();
  initScreenPanel();
  refreshApprovalsPanel();
  wireOnboardingButtons();
  try {
    const { j } = await getJSON("/api/fleet/agents");
    if (j && (!j.agents || !j.agents.length)) startOnboarding();
  } catch(e) {}
}"""

if old_boot in txt:
    txt = txt.replace(old_boot, new_boot, 1)
    print("Step 3: modified boot()")
else:
    print("WARNING: old boot() not found — boot() may already be patched or have different formatting")

# Modify polling interval to also refresh approvals
old_interval = "setInterval(() => { refreshOverview(); refreshPresence(); refreshJobs(); try { refreshMissions(); } catch(e){} try { refreshAgents(); } catch(e){} try { refreshTelemetry(false); } catch(e){} }, 8000);"
new_interval = "setInterval(() => { refreshOverview(); refreshPresence(); refreshJobs(); try { refreshMissions(); } catch(e){} try { refreshAgents(); } catch(e){} try { refreshTelemetry(false); } catch(e){} refreshApprovalsPanel(); }, 8000);"
if old_interval in txt:
    txt = txt.replace(old_interval, new_interval, 1)
    print("Step 4: modified polling interval")
else:
    print("WARNING: polling interval not found")

cr.write_text(txt, encoding="utf-8", errors="replace")
print("All patches applied to control-room.js")
