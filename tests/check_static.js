const fs = require('node:fs');

const gameCatalog = JSON.parse(fs.readFileSync('games.json', 'utf8'));
const gameHtmlFiles = gameCatalog.map((game) => {
  if (!game.id || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(game.id)) {
    throw new Error(`games.json: invalid game id "${game.id}"`);
  }
  if (!game.title || !game.description || !game.path) {
    throw new Error(`games.json: missing required fields for "${game.id}"`);
  }
  const expectedPath = `games/${game.id}/`;
  if (game.path !== expectedPath) {
    throw new Error(`games.json: path for "${game.id}" must be "${expectedPath}"`);
  }
  const filename = `${game.path}index.html`;
  if (!fs.existsSync(filename)) {
    throw new Error(`games.json: game file not found: ${filename}`);
  }
  return filename;
});

const htmlFiles = [
  'index.html',
  'latest.html',
  'work.html',
  'edit.html',
  'games.html',
  'game-admin.html',
  ...gameHtmlFiles,
];
const scriptPattern = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi;

new Function(fs.readFileSync('render.js', 'utf8'));

for (const filename of htmlFiles) {
  const source = fs.readFileSync(filename, 'utf8');
  for (const match of source.matchAll(scriptPattern)) {
    if (match[1].trim()) {
      new Function(match[1]);
    }
  }
}

for (const filename of ['config.json', 'stories.json', 'bible.json', 'library.json', 'games.json']) {
  JSON.parse(fs.readFileSync(filename, 'utf8'));
}

const gameAdmin = fs.readFileSync('game-admin.html', 'utf8');
if (/\b(?:localStorage|sessionStorage)\b/.test(gameAdmin)) {
  throw new Error('game-admin.html must not persist the GitHub token in browser storage.');
}
if (!gameAdmin.includes('force: false')) {
  throw new Error('game-admin.html must use a non-forced Git reference update.');
}

console.log('Static JavaScript and JSON checks passed.');
