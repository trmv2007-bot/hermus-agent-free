(() => {
  const q = (s, root = document) => root.querySelector(s);
  const qa = (s, root = document) => [...root.querySelectorAll(s)];

  const MOCKS = {
    rooms: {
      "main-hall": ["MAIN HALL", "Central HERMUS command space · live workspace routing"],
      "ide-lab": ["IDE LAB", "Code, inspect files and hand off to the deep Workshop"],
      "model-lab": ["MODEL LAB", "Compare runtime deployments and route roles to models"],
      "media-studio": ["MEDIA STUDIO", "Image and video creation surface · prompt to pipeline"],
      "control-room": ["CONTROL ROOM", "System health, missions, safety and operational state"],
      "personal-space": ["PERSONAL SPACE", "Private curiosity, memory and user-approved proposals"]
    },
    workbench: {
      chat: "Chat console",
      ide: "IDE lab",
      browser: "Browser",
      terminal: "Terminal",
      image: "Image generation",
      video: "Video studio",
      notes: "Notes & learning"
    }
  };

  function flash(message, kind = "info") {
    let host = q("#uiToastHost");
    if (!host) {
      host = document.createElement("div");
      host.id = "uiToastHost";
      host.className = "ui-toast-host";
      document.body.appendChild(host);
    }
    host.querySelectorAll(".ui-toast").forEach(existing => existing.remove());
    const toast = document.createElement("div");
    toast.className = "ui-toast " + (kind === "error" ? "error" : "");
    toast.innerHTML = "<b>HERMUS</b><span>" + String(message).replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c])) + "</span>";
    host.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("show"));
    setTimeout(() => {
      toast.classList.remove("show");
      setTimeout(() => toast.remove(), 220);
    }, 1900);
  }

  function pulse(node) {
    if (!node) return;
    node.classList.remove("ui-pulse");
    void node.offsetWidth;
    node.classList.add("ui-pulse");
  }

  function setToggle(button, on) {
    button.setAttribute("aria-pressed", String(on));
    button.classList.toggle("is-on", on);
    const indicator = button.querySelector(".toggle");
    indicator?.classList.toggle("on", on);
    const label = button.querySelector("span");
    const text = label?.textContent || "";
    button.title = text.trim() + (on ? " · ON" : " · OFF");
  }

  function switchWorkbench(view) {
    const workbench = q(".bottom-workbench");
    const views = qa("[data-workbench-view]");
    if (!workbench || !views.length) return;

    workbench.dataset.view = view;
    qa("[data-workbench-tab]").forEach(tab => {
      const active = tab.dataset.workbenchTab === view;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      if (active) {
        tab.parentElement?.scrollTo({ left: Math.max(0, tab.offsetLeft - 10), behavior: "smooth" });
      }
    });
    views.forEach(panel => panel.classList.toggle("active", panel.dataset.workbenchView === view));
    pulse(q("#workbenchViewStage"));
    flash("Workspace · " + (MOCKS.workbench[view] || view));
  }

  function setRoom(room) {
    const data = MOCKS.rooms[room];
    if (!data) return;
    qa("[data-room]").forEach(item => item.classList.toggle("active", item.dataset.room === room));
    const title = q("#roomPreviewTitle");
    const detail = q("#roomPreviewDetail");
    if (title) title.textContent = data[0];
    if (detail) detail.textContent = data[1];
    pulse(q("#roomPreview"));
    flash("3D Space · " + data[0]);
  }

  function hideWorkbench() {
    const workbench = q(".bottom-workbench");
    const restore = q("#workbenchRestore");
    if (!workbench) return;
    workbench.classList.add("is-hidden");
    restore?.classList.add("visible");
    flash("Workbench minimized");
  }

  function showWorkbench() {
    const workbench = q(".bottom-workbench");
    const restore = q("#workbenchRestore");
    if (!workbench) return;
    workbench.classList.remove("is-hidden");
    restore?.classList.remove("visible");
    flash("Workbench restored");
  }

  function togglePanel(button) {
    const targetName = button.dataset.panelToggle;
    const panel = targetName ? q("." + targetName) : null;
    if (!panel) return;
    const collapsed = panel.classList.toggle("is-collapsed");
    button.setAttribute("aria-expanded", String(!collapsed));
    button.textContent = collapsed ? "SHOW" : "HIDE";
    pulse(panel);
    flash((collapsed ? "Collapsed · " : "Expanded · ") + "3D Environment");
  }

  document.addEventListener("click", event => {
    const target = event.target.closest?.("button, [role='button'], .orb-action");
    if (target && target.dataset.command == null && !target.matches("[data-open]")) pulse(target);

    const toggle = event.target.closest?.("[data-toggle]");
    if (toggle) {
      event.preventDefault();
      event.stopPropagation();
      const on = toggle.getAttribute("aria-pressed") !== "true";
      setToggle(toggle, on);
      flash(toggle.querySelector("span")?.textContent?.trim() + (on ? " · ON" : " · OFF"));
      return;
    }

    const panelToggle = event.target.closest?.("[data-panel-toggle]");
    if (panelToggle) {
      event.preventDefault();
      event.stopPropagation();
      togglePanel(panelToggle);
      return;
    }

    const tab = event.target.closest?.("[data-workbench-tab]");
    if (tab) {
      event.preventDefault();
      event.stopPropagation();
      switchWorkbench(tab.dataset.workbenchTab);
      return;
    }

    if (event.target.closest?.("#workbenchClose")) {
      event.preventDefault();
      event.stopPropagation();
      hideWorkbench();
      return;
    }

    if (event.target.closest?.("#workbenchRestore")) {
      event.preventDefault();
      event.stopPropagation();
      showWorkbench();
      return;
    }

    const room = event.target.closest?.("[data-room]");
    if (room) setRoom(room.dataset.room);

    if (event.target.closest?.("#globalSearch")) {
      event.preventDefault();
      event.stopPropagation();
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "p", ctrlKey: true }));
    }

    if (event.target === q("#overlay")) {
      q("#overlay")?.classList.remove("open");
      q("#overlay")?.setAttribute("aria-hidden", "true");
    }
    if (event.target === q("#palette")) {
      q("#palette")?.classList.remove("open");
      q("#palette")?.setAttribute("aria-hidden", "true");
    }
  }, true);

  q("#workshopAsk")?.addEventListener("click", () => {
    const input = q("#workshopCommand");
    input?.focus({ preventScroll: true });
    setTimeout(() => input?.focus({ preventScroll: true }), 0);
  }, true);

  qa("[data-toggle]").forEach(button => setToggle(button, button.getAttribute("aria-pressed") === "true"));
  q(".bottom-workbench")?.setAttribute("data-view", "chat");
  q("#workbenchRestore")?.classList.remove("visible");

  const observer = new MutationObserver(() => {
    const overlay = q("#overlay");
    const modal = q("#overlay .modal");
    modal?.classList.toggle("ui-opening", !!overlay?.classList.contains("open"));
    const workshop = q("#workshop");
    workshop?.classList.toggle("ui-opening", !!workshop?.classList.contains("open"));
    const palette = q("#palette .palette-box");
    palette?.classList.toggle("ui-opening", !!q("#palette")?.classList.contains("open"));
  });
  observer.observe(document.body, { subtree:true, attributes:true, attributeFilter:["class"] });

  window.HermusUI = { switchWorkbench, setRoom, hideWorkbench, showWorkbench, setToggle, mocks:MOCKS };
})();

// UI QA trigger: browser pass validates every visible control and state.
