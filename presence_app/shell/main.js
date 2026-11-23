// HERMUS presence shell — a thin, always-on-top, click-through window that
// hosts the served renderer page (`/presence/overlay`).
//
// The window covers the whole virtual desktop so one canvas can draw the
// character on one monitor and its arrow pointing at a button on another.
// Nothing here paints: the art lives in the renderer, which the shell only
// hosts, positions and unblocks.
//
// Click-through policy: the window ignores every mouse event (`forward: true`
// still delivers mousemove so the renderer can feel the pointer) and the
// renderer asks for interactivity back only while the pointer is over the
// character itself. So the desktop underneath is fully usable — the overlay
// never eats a click.

const path = require("path");
const { app, BrowserWindow, ipcMain, powerMonitor, screen } = require("electron");

const PRESENCE_URL = process.env.HERMUS_PRESENCE_URL || "http://127.0.0.1:8000/presence/overlay";
const TOKEN = process.env.HERMUS_PRESENCE_TOKEN || "";
const INPUT_POLL_MS = 1000;
const INPUT_POST_MS = 2000;

let win = null;
let inputTimer = null;
let lastPostedIdle = -1;
let postedAt = 0;

function apiBase() {
  try {
    return new URL(PRESENCE_URL).origin;
  } catch (err) {
    return "http://127.0.0.1:8000";
  }
}

function withToken(url) {
  if (!TOKEN) return url;
  const target = new URL(url);
  target.searchParams.set("token", TOKEN);
  return target.toString();
}

async function postJson(path, body) {
  try {
    const res = await fetch(withToken(`${apiBase()}${path}`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    return res.ok;
  } catch (err) {
    return false;
  }
}

function displayLayout() {
  return screen.getAllDisplays().map((display, index) => ({
    index,
    scale: display.scaleFactor,
    bounds: display.bounds,
    physical: {
      x: Math.round(display.bounds.x * display.scaleFactor),
      y: Math.round(display.bounds.y * display.scaleFactor),
      w: Math.round(display.bounds.width * display.scaleFactor),
      h: Math.round(display.bounds.height * display.scaleFactor),
    },
  }));
}

function virtualBounds() {
  const displays = screen.getAllDisplays();
  const left = Math.min(...displays.map((d) => d.bounds.x));
  const top = Math.min(...displays.map((d) => d.bounds.y));
  const right = Math.max(...displays.map((d) => d.bounds.x + d.bounds.width));
  const bottom = Math.max(...displays.map((d) => d.bounds.y + d.bounds.height));
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function createWindow() {
  const area = virtualBounds();
  win = new BrowserWindow({
    x: area.x,
    y: area.y,
    width: area.width,
    height: area.height,
    transparent: true,
    frame: false,
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,
    show: false,
    type: "toolbar",
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });

  win.setAlwaysOnTop(true, "screen-saver");
  win.setIgnoreMouseEvents(true, { forward: true });
  win.loadURL(withToken(PRESENCE_URL));
  win.once("ready-to-show", () => win.showInactive());
  win.webContents.on("did-finish-load", () => {
    // The bridge owns the layout: the renderer and the API must agree on how a
    // physical pixel maps to a DIP one, and there is exactly one copy of that.
    postJson("/api/presence/displays", displayLayout());
  });
  win.on("closed", () => {
    win = null;
  });
}

// The person's keyboard quiet time, so a subtitle never appears mid-typing.
// powerMonitor measures system-wide input, which is exactly the signal the
// renderer needs and the page cannot observe on its own.
function startInputWatch() {
  inputTimer = setInterval(() => {
    if (!win || win.isDestroyed()) return;
    let idleSeconds = 0;
    try {
      idleSeconds = powerMonitor.getSystemIdleTime();
    } catch (err) {
      return;
    }
    win.webContents.send("presence:input-idle", idleSeconds);
    const now = Date.now();
    if (now - postedAt > INPUT_POST_MS && idleSeconds !== lastPostedIdle) {
      lastPostedIdle = idleSeconds;
      postedAt = now;
      postJson("/api/presence/input", { idle_ms: idleSeconds * 1000 });
    }
  }, INPUT_POLL_MS);
}

ipcMain.on("presence:set-interactive", (_event, interactive) => {
  if (!win || win.isDestroyed()) return;
  win.setIgnoreMouseEvents(!interactive, { forward: true });
});

ipcMain.on("presence:hide", () => {
  if (win && !win.isDestroyed()) win.hide();
});

ipcMain.on("presence:show", () => {
  if (!win || win.isDestroyed()) return;
  if (!win.isVisible()) win.showInactive();
});

ipcMain.handle("presence:ready", () => ({ url: PRESENCE_URL, platform: process.platform }));

const single = app.requestSingleInstanceLock();
if (!single) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win && !win.isDestroyed()) win.showInactive();
  });
  app.commandLine.appendSwitch("enable-transparent-visuals");
  app.whenReady().then(() => {
    createWindow();
    startInputWatch();
  });
  app.on("window-all-closed", () => {
    if (inputTimer) clearInterval(inputTimer);
    app.quit();
  });
}
