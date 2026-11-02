const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'public');
fs.rmSync(output, { recursive: true, force: true });
fs.cpSync(path.join(root, 'site'), output, { recursive: true });
console.log('Built the Koriyama dashboard in public/');
