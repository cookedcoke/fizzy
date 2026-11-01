// The customer display: a window filling the second screen, showing /display. It can't be
// clicked, focused or typed into, so nothing aimed at the staff window (Esc, Cmd+W, Cmd+Q)
// can reach it, and it stays out of the Dock's window list and Mission Control.
const { BrowserWindow, screen, session, powerMonitor } = require('electron');
const path = require('path');
const store = require('./store');
const log = require('./log');

const CREAM = '#FFF7EE';  // the /display page's background, so there's no white flash
const RETRY_SECONDS = 30;
const PLACEHOLDER = path.join(__dirname, 'placeholder.html');

let win = null;
let staffWindow = null;  // kept off the display's screen
let showingPlaceholder = false;
let retryTimer = null;
let placeTimer = null;

function create(staff) {
  staffWindow = staff;
  win = new BrowserWindow({
    frame: false,
    show: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    closable: false,
    fullscreenable: false,
    enableLargerThanScreen: true,
    backgroundColor: CREAM,
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      backgroundThrottling: false,  // keep its timers running while someone's in another app
      spellcheck: false,
    },
  });
  win.setIgnoreMouseEvents(true);
  win.setAlwaysOnTop(true, 'screen-saver');  // above the menu bar and Dock on its screen
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
  win.excludedFromShownWindowsMenu = true;
  if (win.setHiddenInMissionControl) win.setHiddenInMissionControl(true);
  win.on('close', (event) => {
    if (!require('./quitting').now) event.preventDefault();
  });

  const contents = win.webContents;
  // Somewhere other than /display (e.g. sent to the sign-in page): customers shouldn't see it.
  const stayOnDisplay = (event, url, isInPlace, isMainFrame) => {
    if (isMainFrame === false || url.startsWith('file:') || isDisplayPage(url)) return;
    event.preventDefault();
    showPlaceholder(`sent to ${url}`);
  };
  contents.on('will-navigate', stayOnDisplay);
  contents.on('will-redirect', stayOnDisplay);
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  contents.on('did-navigate', (event, url, code) => {
    if (url.startsWith('file:')) return;
    if (code === 404) showPlaceholder("/display isn't there yet (404)");
    else if (!isDisplayPage(url)) showPlaceholder(`ended up at ${url}`);
  });
  contents.on('did-finish-load', () => {
    if (isDisplayPage(contents.getURL())) {
      showingPlaceholder = false;
      clearTimeout(retryTimer);
      log('display', 'Display page loaded');
    }
  });
  contents.on('did-fail-load', (event, code, description, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return;
    showPlaceholder(`didn't load: ${description}`);
  });
  contents.on('render-process-gone', (event, details) => {
    log('display', `Display stopped (${details.reason}); reloading`);
    showingPlaceholder = false;
    load();
  });

  // Screens plugged in or out, rearranged, or woken up: put the display back where it goes.
  for (const event of ['display-added', 'display-removed', 'display-metrics-changed']) {
    screen.on(event, placeSoon);
  }
  powerMonitor.on('resume', placeSoon);
  powerMonitor.on('unlock-screen', placeSoon);
  return win;
}

function isDisplayPage(url) {
  try {
    const u = new URL(url);
    return u.hostname === new URL(store.baseURL()).hostname
      && (u.pathname === '/display' || u.pathname.startsWith('/display/'));
  } catch {
    return false;
  }
}

// MARK: loading

// Checks /display first (with the shared cookies), so a missing page or a trip to the
// sign-in page never shows on the display, not even for a moment.
async function load() {
  const url = store.displayURL();
  log('display', `Loading ${url}`);
  let response;
  try {
    response = await session.defaultSession.fetch(url, { cache: 'no-store' });
  } catch (err) {
    showPlaceholder(`didn't load: ${err.message}`);
    return;
  }
  if (response.status === 404) {
    showPlaceholder("/display isn't there yet (404)");
  } else if (!isDisplayPage(response.url || url)) {
    showPlaceholder(`sent to ${response.url}`);
  } else {
    win.loadURL(url);
  }
}

// Tries /display again if the placeholder is up, e.g. after someone signs in on the staff screen.
function retryIfNeeded() {
  if (showingPlaceholder) load();
}

function showPlaceholder(reason) {
  log('display', `Showing the placeholder: ${reason}`);
  if (!showingPlaceholder) {
    showingPlaceholder = true;
    win.loadFile(PLACEHOLDER);
  }
  clearTimeout(retryTimer);
  retryTimer = setTimeout(retryIfNeeded, RETRY_SECONDS * 1000);
}

// Passes a message (already JSON) to the display page.
function send(json) {
  log('bridge', `Forwarding to display: ${json}`);
  win.webContents
    .executeJavaScript(`void (window.fizzyDisplay && window.fizzyDisplay(${json}))`)
    .catch((err) => log('bridge', `Display didn't take the message: ${err.message}`));
}

// MARK: screens

function placeSoon() {
  // These come in bursts while a screen connects, so wait for things to settle.
  clearTimeout(placeTimer);
  placeTimer = setTimeout(place, 1000);
}

// The screen picked with "Move display to other screen" if it's plugged in, otherwise the
// first screen that isn't the main one. None with only one screen.
function chooseScreen() {
  const screens = screen.getAllDisplays();
  if (screens.length < 2) return null;
  const saved = store.get('displayScreen');
  if (saved) {
    const match = screens.find((d) => d.id === saved.id) || screens.find((d) => saved.label && d.label === saved.label);
    if (match) return match;
  }
  const primary = screen.getPrimaryDisplay();
  return screens.find((d) => d.id !== primary.id);
}

// Covers the right screen, or hides the display until a second screen is plugged in.
function place() {
  const target = chooseScreen();
  if (!target) {
    if (win.isVisible()) log('display', 'Only one screen; display hidden until another is plugged in');
    win.hide();
    return;
  }
  log('display', `Display on ${target.label || target.id}`);
  win.setBounds(target.bounds);
  win.showInactive();
  win.setAlwaysOnTop(true, 'screen-saver');
  moveStaffWindow(target);
}

// Display > Move display to other screen. Swaps with the staff window if they'd overlap.
function moveToNextScreen() {
  const screens = screen.getAllDisplays();
  if (screens.length < 2) {
    require('electron').shell.beep();
    log('display', "Can't move the display: there's only one screen");
    return;
  }
  const current = screen.getDisplayMatching(win.getBounds());
  const index = screens.findIndex((d) => d.id === current.id);
  const next = screens[(index + 1) % screens.length];
  store.set('displayScreen', { id: next.id, label: next.label });
  place();
}

// If the staff window is on the display's screen it would be hidden behind it, so move it to
// another screen, keeping its size where it fits.
function moveStaffWindow(displayScreen) {
  if (!staffWindow || staffWindow.isDestroyed()) return;
  if (screen.getDisplayMatching(staffWindow.getBounds()).id !== displayScreen.id) return;
  const other = screen.getAllDisplays().find((d) => d.id !== displayScreen.id);
  if (!other) return;
  const area = other.workArea;
  const b = staffWindow.getBounds();
  const width = Math.min(b.width, area.width);
  const height = Math.min(b.height, area.height);
  staffWindow.setBounds({
    x: Math.round(area.x + (area.width - width) / 2),
    y: Math.round(area.y + (area.height - height) / 2),
    width, height,
  });
}

function openDevTools() {
  win.webContents.openDevTools({ mode: 'detach' });
}

// When quitting: the window can't be closed the normal way.
function destroy() {
  if (win && !win.isDestroyed()) win.destroy();
}

module.exports = { create, load, retryIfNeeded, send, place, moveToNextScreen, openDevTools, destroy };
