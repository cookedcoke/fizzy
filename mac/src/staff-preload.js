// Runs in the staff pages before they load. Gives them the same
// window.webkit.messageHandlers.fizzyDisplay.postMessage(msg) as a Mac web view, so the
// Fizzy pages don't need to know they're in Electron.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('webkit', {
  messageHandlers: {
    fizzyDisplay: {
      postMessage: (msg) => {
        // Only plain JSON gets through, the same as the Mac web view.
        let json;
        try {
          json = JSON.stringify(msg);
        } catch {
          json = undefined;
        }
        ipcRenderer.send('fizzy-display', json);
      },
    },
  },
});
