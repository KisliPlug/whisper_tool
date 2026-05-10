const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("annotatorApi", {
  load: () => ipcRenderer.invoke("annotator:load"),
  checkSignal: () => ipcRenderer.invoke("annotator:checkSignal"),
  save: (payload) => ipcRenderer.invoke("annotator:save", payload),
  cancel: () => ipcRenderer.invoke("annotator:cancel")
});
