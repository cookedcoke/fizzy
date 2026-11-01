// For the app's own small windows (Settings, Open Link).
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('fizzy', {
  get: (key) => ipcRenderer.invoke('form-get', key),
  submit: (form, value) => ipcRenderer.invoke('form-submit', form, value),
  close: () => ipcRenderer.send('form-close'),
});
