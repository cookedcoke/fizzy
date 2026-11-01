// The app's few settings and remembered things, in settings.json in the app's data folder
// (~/Library/Application Support/Fizzy).
const { app } = require('electron');
const fs = require('fs');
const path = require('path');

const DEFAULT_BASE_URL = 'https://fizzy.cookedcoke.co';
const file = () => path.join(app.getPath('userData'), 'settings.json');
let data = null;

function load() {
  if (data) return data;
  try {
    data = JSON.parse(fs.readFileSync(file(), 'utf8'));
  } catch {
    data = {};
  }
  return data;
}

function get(key) {
  return load()[key];
}

function set(key, value) {
  load();
  if (value === undefined) delete data[key];
  else data[key] = value;
  try {
    fs.mkdirSync(path.dirname(file()), { recursive: true });
    fs.writeFileSync(file(), JSON.stringify(data, null, 2));
  } catch (err) {
    console.error('Could not save settings', err);
  }
}

// "fizzy.example.com", "http://localhost:8000" or "https://test.cookedcoke.co/" -> a base
// URL with no path, or null if it isn't a web address.
function parseBaseURL(text) {
  let s = String(text || '').trim();
  if (!s) return null;
  if (!s.includes('://')) s = 'https://' + s;
  try {
    const url = new URL(s);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    if (!url.hostname) return null;
    return url.origin;
  } catch {
    return null;
  }
}

// Where Fizzy lives. Change it in Fizzy > Settings to point the app at a test server.
function baseURL() {
  return parseBaseURL(get('baseURL')) || DEFAULT_BASE_URL;
}

function setBaseURL(url) {
  set('baseURL', url === DEFAULT_BASE_URL ? undefined : url);
}

const staffURL = () => baseURL() + '/';
const displayURL = () => baseURL() + '/display';

// Hosts that open inside the app. Everything else opens in the default browser.
function appHosts() {
  return new Set(['fizzy.cookedcoke.co', 'my.cookedcoke.co', new URL(baseURL()).hostname]);
}

function opensInApp(url) {
  try {
    const u = new URL(url);
    if (['about:', 'blob:', 'data:', 'devtools:'].includes(u.protocol)) return true;
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
    return appHosts().has(u.hostname.toLowerCase());
  } catch {
    return false;
  }
}

module.exports = {
  DEFAULT_BASE_URL, get, set, parseBaseURL, baseURL, setBaseURL, staffURL, displayURL, appHosts, opensInApp,
};
