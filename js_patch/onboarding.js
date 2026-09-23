// ---------- ONBOARDING WIZARD (skeleton) ----------
const ONBOARDING_STEPS = [
  {
    title: "Welcome to HERMUS Fleet",
    render: () => '<div class="wizard-step-text">'
      + '<p class="wizard-intro">Set up your first agent fleet in a few steps. You can skip any step — nothing here writes secrets.</p>'
      + '<div class="wizard-features">'
      + '<div class="wizard-feature"><span class="wizard-feature-icon">&#9889;</span><div><strong>Agent Pool</strong><br>Spawn persistent agents with provider/model presets.</div></div>'
      + '<div class="wizard-feature"><span class="wizard-feature-icon">&#128222;</span><div><strong>API Keys</strong><br>Add keys via the dashboard or CLI — never stored in the browser.</div></div>'
      + '<div class="wizard-feature"><span class="wizard-feature-icon">&#128279;</span><div><strong>Live Feed</strong><br>Watch fleet state changes in real time via WebSocket.</div></div>'
      + '</div>'
      + '<p class="wizard-note">This wizard does not write any secrets. API keys are added separately via POST /api/fleet/keys or the CLI.</p>'
      + '</div>'
  },
  {
    title: "Choose a Provider & Model",
    render: () => '<div class="wizard-field"><label>Provider</label><select id="onbProvider"><option value="">Loading...</option></select></div>'
      + '<div class="wizard-field" id="onbModelGroup" style="display:none"><label>Model</label><select id="onbModel"><option>Loading...</option></select></div>'
      + '<div class="wizard-note">Add API keys separately — this step does not touch your keys.</div>',
    afterRender: async () => {
      try {
        const { j } = await getJSON("/api/fleet/providers");
        const sel = document.getElementById("onbProvider");
        if (sel) sel.innerHTML = '<option value="">Select provider...</option>' + j.providers.map(p => '<option value="' + esc(p) + '">' + esc(p) + '</option>').join("");
      } catch(e) { const sel = document.getElementById("onbProvider"); if (sel) sel.innerHTML = '<option value="">— unavailable —</option>'; }
      document.getElementById("onbProvider").onchange = onbProviderChange;
    }
  },
  {
    title: "API Key (optional)",
    render: () => '<div class="wizard-field">'
      + '<p class="wizard-note">Add an API key for your provider. Optional — add keys later via POST /api/fleet/keys or the CLI.</p>'
      + '<div class="wizard-key-form">'
      + '<input type="text" id="onbKeyName" placeholder="Key name (e.g. groq-prod)" />'
      + '<input type="text" id="onbKeyProvider" placeholder="Provider (e.g. groq)" />'
      + '<input type="password" id="onbKeyValue" placeholder="API key — sent to server, never stored in browser" />'
      + '</div>'
      + '<p class="wizard-note">The key is sent to the gateway vault and is not persisted in the browser.</p>'
      + '</div>'
  },
  {
    title: "Ready to go",
    render: () => '<div class="wizard-step-text">'
      + '<p>Your fleet is ready. You can now:</p>'
      + '<ul class="wizard-ready-list">'
      + '<li>Spawn an agent from the <strong>Agents</strong> tab or <strong>+ Create Agent</strong>.</li>'
      + '<li>Watch live state changes in the <strong>Live Bus Feed</strong> panel.</li>'
      + '<li>Approve or deny pending actions from the <strong>Approvals</strong> panel.</li>'
      + '<li>Add more API keys anytime from <strong>Fleet &rarr; Keys</strong>.</li>'
      + '</ul>'
      + '<p class="wizard-note">Tip: run <strong>Spawn Demo Team</strong> to create a pre-configured team.</p>'
      + '</div>'
  }
];

let onbStep = 0;

function startOnboarding() {
  onbStep = 0;
  const wizard = $("#onboardingWizard");
  if (!wizard) return;
  wizard.hidden = false;
  renderOnboardingStep();
}

function closeOnboarding() {
  const wizard = $("#onboardingWizard");
  if (wizard) wizard.hidden = true;
  onbStep = 0;
}

function renderOnboardingStep() {
  const wizard = $("#onboardingWizard");
  if (!wizard || onbStep < 0 || onbStep >= ONBOARDING_STEPS.length) { closeOnboarding(); return; }
  const step = ONBOARDING_STEPS[onbStep];
  $("#wizardStep").textContent = "Step " + (onbStep + 1) + " of " + ONBOARDING_STEPS.length;
  $("#wizardContent").innerHTML = step.render();
  $("#wizardPrev").disabled = onbStep === 0;
  $("#wizardNext").textContent = onbStep === ONBOARDING_STEPS.length - 1 ? "Done" : "Next";
  if (step.afterRender) step.afterRender();
}

function onbProviderChange() {
  const sel = document.getElementById("onbProvider");
  const prov = sel && sel.value || "";
  const modelGroup = document.getElementById("onbModelGroup");
  const modelSel = document.getElementById("onbModel");
  if (!modelGroup || !modelSel) return;
  if (!prov) { modelGroup.style.display = "none"; return; }
  modelGroup.style.display = "";
  modelSel.innerHTML = '<option value="">Loading...</option>';
  fetch("/api/fleet/models?provider=" + encodeURIComponent(prov))
    .then(r => r.json())
    .then(d => { modelSel.innerHTML = '<option value="">Select model...</option>' + (d.models || []).map(m => '<option value="' + esc(m) + '">' + esc(m) + '</option>').join(""); })
    .catch(() => { modelSel.innerHTML = '<option value="">— unavailable —</option>'; });
}

function onbNext() {
  if (onbStep < ONBOARDING_STEPS.length - 1) { onbStep++; renderOnboardingStep(); }
  else { closeOnboarding(); toast("Onboarding complete — your fleet is ready", "success"); }
}

function onbPrev() { if (onbStep > 0) { onbStep--; renderOnboardingStep(); } }

function onbAddKey() {
  const name = (document.getElementById("onbKeyName") || {}).value.trim() || "";
  const provider = (document.getElementById("onbKeyProvider") || {}).value.trim() || "";
  const key = (document.getElementById("onbKeyValue") || {}).value || "";
  if (!name || !provider || !key) { toast("Key name, provider, and key are required", "error"); return; }
  postJSON("/api/fleet/keys", { name, provider, key })
    .then(d => { if (d.ok) { toast("Key '" + esc(name) + "' added to vault", "success"); closeOnboarding(); }
      else toast("Failed to add key: " + esc(d.message || d.error || ""), "error"); })
    .catch(e => toast("Error adding key: " + e.message, "error"));
}

function wireOnboardingButtons() {
  const prev = $("#wizardPrev");
  const next = $("#wizardNext");
  if (prev) prev.onclick = onbPrev;
  if (next) next.onclick = onbNext;
  document.addEventListener("click", (e) => {
    if (e.target && e.target.id === "onbAddKey") onbAddKey();
  });
}
