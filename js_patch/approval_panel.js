// ---------- APPROVAL ATTENTION PANEL ----------
function refreshApprovalsPanel() {
  const list = $("#approvalList");
  const badge = $("#approvalBadge");
  const panel = $("#approvalAttention");
  if (!list) return;
  try {
    const { j } = getJSON("/permissions/pending");
    const pending = (j && j.pending) ? j.pending : [];
    const active = pending.filter(a => a.status === "pending" || a.status === "asked");
    if (badge) { badge.textContent = String(active.length); badge.className = "badge " + (active.length ? "warning" : ""); }
    if (panel) panel.hidden = active.length === 0;
    if (!active.length) { list.innerHTML = '<div class="approval-empty">No pending approvals — all clear.</div>'; return; }
    list.innerHTML = active.map(a => {
      const created = a.created_at ? new Date(a.created_at).toLocaleString() : "";
      return '<div class="approval-item glass">'
        + '<div class="approval-item-header">'
        + '<span class="approval-item-title">' + esc(a.title || "Approval required") + '</span>'
        + '<span class="approval-item-id">' + esc(a.id) + '</span>'
        + '<span class="approval-item-age">' + esc(created) + '</span>'
        + '</div>'
        + '<div class="approval-item-body">'
        + '<div class="approval-item-tool">Tool: ' + esc(a.tool || "") + '</div>'
        + (a.suggested_purpose ? '<div class="approval-item-purpose">Purpose: ' + esc(a.suggested_purpose) + '</div>' : "")
        + (a.suggested_resources && a.suggested_resources.length ? '<div class="approval-item-resources">Resources: ' + esc(a.suggested_resources.join(", ")) + '</div>' : "")
        + '</div>'
        + '<div class="approval-item-actions">'
        + '<button class="btn primary" data-approve-approval="' + esc(a.id) + '">Approve</button>'
        + '<button class="btn ghost" data-deny-approval="' + esc(a.id) + '">Deny</button>'
        + '</div>'
        + '</div>';
    }).join("");
    list.querySelectorAll("[data-approve-approval]").forEach(b => b.addEventListener("click", () => resolveApproval(b.getAttribute("data-approve-approval"), "approve")));
    list.querySelectorAll("[data-deny-approval]").forEach(b => b.addEventListener("click", () => resolveApproval(b.getAttribute("data-deny-approval"), "deny")));
  } catch(e) { list.innerHTML = '<div class="approval-empty">Unable to load pending approvals.</div>'; }
}

function resolveApproval(id, decision) {
  const list = $("#approvalList");
  if (list) list.innerHTML = '<div class="approval-empty">Resolving...</div>';
  try {
    const { j } = postJSON("/permissions/pending/resolve", { id, decision, ttl_minutes: 30 });
    if (j && j.success) {
      toast("Approval " + decision + ": " + esc(id), "success");
      refreshApprovalsPanel();
      try { refreshMissions(); } catch(e) {}
    } else {
      toast("Failed to " + decision + " approval: " + esc(j.message || j.error || ""), "error");
      refreshApprovalsPanel();
    }
  } catch(e) { toast("Error resolving approval: " + e.message, "error"); refreshApprovalsPanel(); }
}
