// Writes to ~/Library/Logs/Fizzy/main.log (and the terminal, when run with npm start).
const { app } = require('electron');
const fs = require('fs');
const path = require('path');

let file = null;
const MAX_BYTES = 2 * 1024 * 1024;

function log(category, message) {
  const line = `${new Date().toISOString()} [${category}] ${message}`;
  console.log(line);
  try {
    if (!file) {
      const dir = app.getPath('logs');
      fs.mkdirSync(dir, { recursive: true });
      file = path.join(dir, 'main.log');
      // Start again once it gets big, keeping the last one as main.old.log.
      if (fs.existsSync(file) && fs.statSync(file).size > MAX_BYTES) {
        fs.renameSync(file, path.join(dir, 'main.old.log'));
      }
    }
    fs.appendFileSync(file, line + '\n');
  } catch {
    // Logging must never stop the app.
  }
}

module.exports = log;
