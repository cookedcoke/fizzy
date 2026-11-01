// The normal window on the main screen where staff use Fizzy.
const { BrowserWindow, WebContentsView, dialog, screen, shell, session } = require('electron');
const fs = require('fs');
const path = require('path');
const store = require('./store');
const log = require('./log');

let win = null;
let view = null;  // the Fizzy page, below the title bar strip
// The title bar is hidden and the window's own background (Fizzy's dark header colour) shows
// in a strip above the page, so it reads as one dark bar. The page sits below the strip, so the
// window buttons never cover anything on it.
const TITLE_BAR_HEIGHT = 28;
let lastFailedURL = null;  // so View > Reload Page tries the page again, not the error page
let saveTimer = null;
const onPageLoaded = [];

function create() {
  const saved = savedBounds();
  win = new BrowserWindow({
    title: 'Fizzy',
    backgroundColor: '#15171B',  // Fizzy's header colour, so there's no white flash while it loads
    width: saved ? saved.width : 1200,
    height: saved ? saved.height : 800,
    x: saved ? saved.x : undefined,
    y: saved ? saved.y : undefined,
    minWidth: 480,
    minHeight: 360,
    show: false,
    tabbingIdentifier: 'staff',
    titleBarStyle: 'hidden',
    trafficLightPosition: { x: 10, y: 7 },
  });
  view = new WebContentsView({
    webPreferences: {
      preload: path.join(__dirname, 'staff-preload.js'),
      contextIsolation: true,
      sandbox: true,
      spellcheck: true,
    },
  });
  view.setBackgroundColor('#15171B');
  win.contentView.addChildView(view);
  const layout = () => {
    const [width, height] = win.getContentSize();
    const top = win.isFullScreen() ? 0 : TITLE_BAR_HEIGHT;
    view.setBounds({ x: 0, y: top, width, height: height - top });
  };
  layout();
  win.on('resize', layout);
  win.on('enter-full-screen', layout);
  win.on('leave-full-screen', layout);
  win.on('focus', () => view.webContents.focus());
  if (saved && saved.maximised) win.maximize();
  view.webContents.once('did-finish-load', () => win.show());
  setTimeout(() => { if (!win.isDestroyed()) win.show(); }, 3000);  // even if the page is slow

  // Remembers the size and position between launches.
  for (const event of ['resize', 'move', 'maximize', 'unmaximize']) {
    win.on(event, () => {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(saveBounds, 500);
    });
  }

  // Closing the window (Cmd+W) only hides it. The Dock icon or Window > Fizzy brings it back.
  win.on('close', (event) => {
    saveBounds();
    if (!require('./quitting').now) {
      event.preventDefault();
      win.hide();
    }
  });

  const contents = view.webContents;
  contents.on('did-finish-load', () => contents.setZoomFactor(store.get('staffZoom') || 1));

  // Other sites (and mailto: and the like) open in the default browser or app.
  const keepInApp = (event, url) => {
    if (store.opensInApp(url)) return;
    event.preventDefault();
    openOutside(url);
  };
  contents.on('will-navigate', keepInApp);
  contents.on('will-redirect', (event, url, isInPlace, isMainFrame) => {
    if (isMainFrame) keepInApp(event, url);
  });
  // Links that want a new window (target="_blank", window.open): Fizzy pages open here.
  contents.setWindowOpenHandler(({ url }) => {
    if (store.opensInApp(url)) contents.loadURL(url);
    else openOutside(url);
    return { action: 'deny' };
  });

  contents.on('did-finish-load', () => {
    if (contents.getURL().startsWith('http')) lastFailedURL = null;
    for (const fn of onPageLoaded) fn();
  });
  contents.on('did-fail-load', (event, code, description, url, isMainFrame) => {
    // -3 is a load that was stopped on purpose (a new page started, or a link sent to the browser).
    if (!isMainFrame || code === -3) return;
    log('staff', `Page didn't load: ${description} (${url})`);
    lastFailedURL = url || lastFailedURL || store.staffURL();
    contents.loadURL(errorPage(description));
  });
  contents.on('render-process-gone', (event, details) => {
    log('staff', `Staff page stopped (${details.reason}); reloading`);
    reload();
  });

  session.defaultSession.on('will-download', handleDownload);
  return win;
}

function openOutside(url) {
  log('staff', `Opening outside the app: ${url}`);
  try {
    const protocol = new URL(url).protocol;
    if (['http:', 'https:', 'mailto:', 'tel:'].includes(protocol)) shell.openExternal(url);
  } catch {
    // Not a URL; ignore it.
  }
}

function errorPage(description) {
  const escape = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const html = `<!doctype html><meta charset="utf-8"><title>Fizzy</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px -apple-system, sans-serif; margin: 0; display: flex; align-items: center;
         justify-content: center; height: 100vh; text-align: center; }
  h1 { font-size: 22px; font-weight: 600; }
  p { color: #888; max-width: 30em; }
</style>
<div><h1>Fizzy didn't load</h1><p>${escape(description)}</p>
<p>Check the internet connection, then choose View &gt; Reload Page.</p></div>`;
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(html);
}

// MARK: size and position

function savedBounds() {
  const b = store.get('staffBounds');
  if (!b) return null;
  // Only if it's still on a screen that's plugged in.
  const onScreen = screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    return b.x < a.x + a.width && b.x + b.width > a.x && b.y < a.y + a.height && b.y + b.height > a.y;
  });
  return onScreen ? b : null;
}

function saveBounds() {
  if (!win || win.isDestroyed() || win.isMinimized() || win.isFullScreen()) return;
  const maximised = win.isMaximized();
  const b = maximised ? (store.get('staffBounds') || win.getNormalBounds()) : win.getBounds();
  store.set('staffBounds', { ...b, maximised });
}

// MARK: menu actions

function show() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function loadHome() {
  lastFailedURL = null;
  view.webContents.loadURL(store.staffURL());
}

function load(url) {
  view.webContents.loadURL(url);
}

function reload() {
  const url = view.webContents.getURL();
  if (lastFailedURL) view.webContents.loadURL(lastFailedURL);
  else if (!url || !url.startsWith('http')) loadHome();
  else view.webContents.reload();
}

function setZoom(factor) {
  const zoom = Math.min(Math.max(factor, 0.5), 3);
  view.webContents.setZoomFactor(zoom);
  store.set('staffZoom', zoom === 1 ? undefined : zoom);
}

function zoomBy(step) {
  setZoom(view.webContents.getZoomFactor() * step);
}

// MARK: downloads

// Downloads (CSV exports, backups) go to ~/Downloads, never over an existing file.
function handleDownload(event, item) {
  const folder = require('electron').app.getPath('downloads');
  const target = unusedFile(folder, item.getFilename());
  item.setSavePath(target);
  log('staff', `Downloading to ${target}`);
  item.once('done', async (e, state) => {
    if (state !== 'completed') {
      if (state === 'interrupted') {
        dialog.showMessageBox(win, { type: 'warning', message: "The download didn't finish", detail: path.basename(target) });
      }
      return;
    }
    if (process.platform === 'darwin') require('electron').app.dock.downloadFinished(target);
    const { response } = await dialog.showMessageBox(win, {
      message: 'Saved to Downloads',
      detail: path.basename(target),
      buttons: ['OK', 'Show in Finder'],
      defaultId: 0,
      cancelId: 0,
    });
    if (response === 1) shell.showItemInFolder(target);
  });
}

// "takings.csv", then "takings 2.csv", "takings 3.csv" ...
function unusedFile(folder, name) {
  const safe = (name || 'download').replace(/[/:]/g, '-');
  const ext = path.extname(safe);
  const stem = safe.slice(0, safe.length - ext.length);
  let target = path.join(folder, safe);
  for (let n = 2; fs.existsSync(target); n++) target = path.join(folder, `${stem} ${n}${ext}`);
  return target;
}

module.exports = {
  create, show, loadHome, load, reload, setZoom, zoomBy, onPageLoaded,
  window: () => win,
  contents: () => view && view.webContents,
};
