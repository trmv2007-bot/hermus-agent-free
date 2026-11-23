// The only bridge between the renderer page and the shell.
//
// Deliberately tiny: the page can ask whether the pointer is over the
// character (so the shell can stop ignoring the mouse there), ask to be shown
// or hidden, and subscribe to system input idleness. It cannot reach Node, the
// filesystem, or any other process.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("presenceShell", {
  ready: () => ipcRenderer.invoke("presence:ready"),
  setInteractive: (interactive) => ipcRenderer.send("presence:set-interactive", Boolean(interactive)),
  hide: () => ipcRenderer.send("presence:hide"),
  show: () => ipcRenderer.send("presence:show"),
  onInputIdle: (handler) => {
    ipcRenderer.on("presence:input-idle", (_event, idleSeconds) => handler(idleSeconds));
  },
});
