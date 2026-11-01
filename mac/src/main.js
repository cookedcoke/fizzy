// Fizzy at the counter: the staff window on the main screen and the customer display on the
// second screen. Both use the default session, so signing in once in the staff window also
// signs in the display.
const { app, BrowserWindow, Menu, dialog, ipcMain, powerMonitor, powerSaveBlocker, session } = require('electron');
const path = require('path');
const store = require('./store');
const log = require('./log');
const staff = require('./staff');
const display = require('./display');
const quitting = require('./quitting');

app.setName('Fizzy');

// One copy at a time: opening it again just brings the staff window forward.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => staff.show());
  app.whenReady().then(start);
}

function start() {
  // Lets the Fizzy pages tell they're running in the counter app.
  const ua = session.defaultSession.getUserAgent();
  session.defaultSession.setUserAgent(`${ua} FizzyMac/${app.getVersion()}`);

  const staffWindow = staff.create();
  display.create(staffWindow);
  staff.onPageLoaded.push(display.retryIfNeeded);
  Menu.setApplicationMenu(buildMenu());

  staff.loadHome();
  display.load();
  display.place();

  // No dimming, screen saver or sleep while Fizzy is open, so the customer display stays on.
  powerSaveBlocker.start('prevent-display-sleep');
  log('app', `Started, Fizzy address ${store.baseURL()}`);
}

// Clicking the Dock icon brings back the staff window if it was closed.
app.on('activate', () => staff.show());
app.on('window-all-closed', () => {});  // keep running; the staff window only hides

// MARK: quitting

// Logging out, restarting or shutting down: don't hold that up with a question.
powerMonitorReady(() => powerMonitor.on('shutdown', () => {
  quitting.now = true;
  display.destroy();
}));

app.on('before-quit', (event) => {
  if (quitting.now) {
    display.destroy();
    return;
  }
  event.preventDefault();
  const win = staff.window();
  if (win) staff.show();
  const options = {
    type: 'warning',
    message: 'Quit Fizzy?',
    detail: 'The customer display will turn off.',
    // Return and Esc both mean Cancel, so a stray key press can't quit.
    buttons: ['Cancel', 'Quit'],
    defaultId: 0,
    cancelId: 0,
  };
  const choice = win ? dialog.showMessageBoxSync(win, options) : dialog.showMessageBoxSync(options);
  if (choice === 1) {
    quitting.now = true;
    app.quit();
  }
});

function powerMonitorReady(fn) {
  if (app.isReady()) fn();
  else app.once('ready', fn);
}

// MARK: messages from the staff pages to the display

// The staff pages call window.webkit.messageHandlers.fizzyDisplay.postMessage(msg); the display
// page gets the same msg through window.fizzyDisplay(msg). The app doesn't look inside the
// messages, so new kinds of message only need changes to the Fizzy pages.
ipcMain.on('fizzy-display', (event, json) => {
  const frame = event.senderFrame;
  let host = '';
  try { host = new URL(frame.url).hostname.toLowerCase(); } catch { /* no URL */ }
  // Only Fizzy's own pages get to drive the display.
  if (event.sender !== staff.contents() || frame !== event.sender.mainFrame || !store.appHosts().has(host)) {
    log('bridge', `Ignored a message from ${host || 'an unknown page'}`);
    return;
  }
  // Read it and write it out again, so only real JSON ever reaches the display page.
  let clean;
  try {
    clean = JSON.stringify(JSON.parse(json));
  } catch {
    clean = undefined;
  }
  if (typeof clean !== 'string') {
    log('bridge', "Ignored a message that isn't plain JSON");
    return;
  }
  display.send(clean);
});

// MARK: Settings and Open Link windows

let formWindow = null;

function openForm(file, title, height) {
  if (formWindow && !formWindow.isDestroyed()) formWindow.close();
  const parent = staff.window();
  formWindow = new BrowserWindow({
    title,
    width: 460,
    height,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    show: false,
    parent: file === 'openlink.html' ? parent : undefined,
    modal: file === 'openlink.html',
    webPreferences: { preload: path.join(__dirname, 'form-preload.js'), contextIsolation: true, sandbox: true },
  });
  formWindow.setMenuBarVisibility(false);
  formWindow.webContents.on('will-navigate', (e) => e.preventDefault());
  formWindow.once('ready-to-show', () => formWindow.show());
  formWindow.loadFile(path.join(__dirname, file));
}

ipcMain.handle('form-get', (event, key) => {
  if (key === 'baseURL') return store.baseURL();
  if (key === 'staffURL') return store.staffURL();
  return null;
});

ipcMain.handle('form-submit', (event, form, value) => {
  if (form === 'settings') {
    const url = value === '' ? store.DEFAULT_BASE_URL : store.parseBaseURL(value);
    if (!url) return "That isn't a web address. Try something like https://fizzy.cookedcoke.co";
    const changed = url !== store.baseURL();
    store.setBaseURL(url);
    formWindow.close();
    if (changed) {
      log('app', `Fizzy address changed to ${url}`);
      staff.loadHome();
      display.load();
    }
    return null;
  }
  if (form === 'openlink') {
    let text = String(value || '').trim();
    if (!text) return 'Paste a link first.';
    if (!text.includes('://')) text = 'https://' + text;
    let url;
    try { url = new URL(text).href; } catch { return "That isn't a link."; }
    formWindow.close();
    if (store.opensInApp(url)) staff.load(url);
    else require('electron').shell.openExternal(url);
    return null;
  }
  return null;
});

ipcMain.on('form-close', () => {
  if (formWindow && !formWindow.isDestroyed()) formWindow.close();
});

// MARK: menu bar

function buildMenu() {
  const staffOnly = (fn) => () => { if (staff.window()) fn(); };
  return Menu.buildFromTemplate([
    {
      label: 'Fizzy',
      submenu: [
        { role: 'about', label: 'About Fizzy' },
        { type: 'separator' },
        { label: 'Settings…', accelerator: 'Cmd+,', click: () => openForm('settings.html', 'Settings', 190) },
        { type: 'separator' },
        // Hides only the staff window, so the customer display stays up.
        { label: 'Hide Fizzy', accelerator: 'Cmd+H', click: staffOnly(() => staff.window().hide()) },
        { role: 'hideOthers', label: 'Hide Others' },
        { role: 'unhide', label: 'Show All' },
        { type: 'separator' },
        { role: 'quit', label: 'Quit Fizzy' },
      ],
    },
    {
      label: 'File',
      submenu: [
        { label: 'Open Link…', accelerator: 'Cmd+L', click: () => { staff.show(); openForm('openlink.html', 'Open Link', 170); } },
        { label: 'Print…', accelerator: 'Cmd+P', click: staffOnly(() => staff.contents().print()) },
        { type: 'separator' },
        { role: 'close', label: 'Close Window' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { label: 'Back', accelerator: 'Cmd+[', click: staffOnly(() => staff.contents().navigationHistory.goBack()) },
        { label: 'Forward', accelerator: 'Cmd+]', click: staffOnly(() => staff.contents().navigationHistory.goForward()) },
        { label: 'Home', click: staffOnly(() => staff.loadHome()) },
        { label: 'Reload Page', accelerator: 'Cmd+R', click: staffOnly(() => staff.reload()) },
        { type: 'separator' },
        { label: 'Actual Size', accelerator: 'Cmd+0', click: staffOnly(() => staff.setZoom(1)) },
        { label: 'Zoom In', accelerator: 'Cmd+=', click: staffOnly(() => staff.zoomBy(1.1)) },
        { label: 'Zoom Out', accelerator: 'Cmd+-', click: staffOnly(() => staff.zoomBy(1 / 1.1)) },
        { type: 'separator' },
        { role: 'togglefullscreen', label: 'Enter Full Screen' },
        { type: 'separator' },
        { label: 'Developer Tools', accelerator: 'Alt+Cmd+I', click: staffOnly(() => staff.contents().toggleDevTools()) },
      ],
    },
    {
      label: 'Display',
      submenu: [
        { label: 'Hide customer display', accelerator: 'Cmd+Shift+H', click: () => display.send('{"type":"hide"}') },
        { label: 'Show customer display', click: () => display.send('{"type":"idle"}') },
        { type: 'separator' },
        { label: 'Reload display', click: () => display.load() },
        { label: 'Move display to other screen', click: () => display.moveToNextScreen() },
        { type: 'separator' },
        { label: 'Display Developer Tools', click: () => display.openDevTools() },
      ],
    },
    {
      role: 'window',
      submenu: [
        { role: 'minimize', label: 'Minimise' },
        { role: 'zoom' },
        { type: 'separator' },
        { label: 'Fizzy', accelerator: 'Cmd+1', click: () => staff.show() },
      ],
    },
  ]);
}
