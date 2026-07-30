const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const gameCatalog = JSON.parse(fs.readFileSync('games.json', 'utf8'));
const gameIds = new Set();
const gameHtmlFiles = gameCatalog.map((game) => {
  if (!game.id || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(game.id)) {
    throw new Error(`games.json: invalid game id "${game.id}"`);
  }
  if (gameIds.has(game.id)) {
    throw new Error(`games.json: duplicate game id "${game.id}"`);
  }
  gameIds.add(game.id);
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
  'play.html',
  'game-admin.html',
  ...gameHtmlFiles,
];
const scriptPattern = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
const moduleTempDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'game-module-check-'));

new Function(fs.readFileSync('render.js', 'utf8'));
const registerGameCheck = spawnSync(process.execPath, ['--check', 'tools/register-game.js'], {
  cwd: process.cwd(),
  encoding: 'utf8',
  windowsHide: true,
});
if (registerGameCheck.status !== 0) {
  throw new Error(
    `tools/register-game.js: invalid JavaScript\n${registerGameCheck.stderr || registerGameCheck.stdout}`,
  );
}

function getAttribute(attributes, name) {
  const pattern = new RegExp(
    `(?:^|\\s)${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s"'=<>\\x60]+))`,
    'i',
  );
  const match = attributes.match(pattern);
  return match ? (match[1] ?? match[2] ?? match[3] ?? '') : null;
}

function checkModuleSyntax(source, filename, scriptIndex) {
  const moduleFilename = path.join(moduleTempDirectory, `script-${scriptIndex}.mjs`);
  fs.writeFileSync(moduleFilename, source, 'utf8');
  const result = spawnSync(process.execPath, ['--check', moduleFilename], {
    encoding: 'utf8',
    windowsHide: true,
  });
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || 'invalid module syntax').trim();
    throw new Error(`${filename}: invalid module script\n${detail}`);
  }
}

try {
  let moduleScriptIndex = 0;
  checkModuleSyntax(
    'await Promise.resolve();',
    'module syntax self-check',
    'self-test',
  );
  for (const filename of htmlFiles) {
    const source = fs.readFileSync(filename, 'utf8');
    for (const match of source.matchAll(scriptPattern)) {
      const attributes = match[1];
      const scriptSource = match[2];
      if (!scriptSource.trim() || getAttribute(attributes, 'src') !== null) {
        continue;
      }

      const type = (getAttribute(attributes, 'type') || '').trim().toLowerCase();
      if (type === 'module') {
        checkModuleSyntax(scriptSource, filename, moduleScriptIndex);
        moduleScriptIndex += 1;
      } else if (
        !type
        || type === 'text/javascript'
        || type === 'application/javascript'
        || type === 'text/ecmascript'
        || type === 'application/ecmascript'
      ) {
        new Function(scriptSource);
      }
    }
  }
} finally {
  fs.rmSync(moduleTempDirectory, { recursive: true, force: true });
}

for (const filename of ['config.json', 'stories.json', 'bible.json', 'library.json', 'games.json']) {
  JSON.parse(fs.readFileSync(filename, 'utf8'));
}

const gameAdmin = fs.readFileSync('game-admin.html', 'utf8');
if (/\b(?:localStorage|sessionStorage)\s*\.\s*setItem\s*\(/.test(gameAdmin)) {
  throw new Error('game-admin.html must not persist the GitHub token in browser storage.');
}
if (!gameAdmin.includes('force: false')) {
  throw new Error('game-admin.html must use a non-forced Git reference update.');
}
if (!gameAdmin.includes("'game-upload/'") || !gameAdmin.includes('waitForValidation')) {
  throw new Error('game-admin.html must validate uploads on a temporary branch before publishing.');
}
const validationCallIndex = gameAdmin.indexOf('var validationRun = await waitForValidation');
const mainUpdateIndex = gameAdmin.indexOf(
  "await githubApi('/git/refs/heads/' + BRANCH",
  validationCallIndex,
);
if (validationCallIndex < 0 || mainUpdateIndex < validationCallIndex) {
  throw new Error('game-admin.html must complete validation before updating main.');
}
if (
  gameAdmin.includes("new Date().toISOString().slice(0, 10)")
  || !gameAdmin.includes("timeZone: 'Asia/Tokyo'")
) {
  throw new Error('game-admin.html must record publication dates in Japan time.');
}
if (!gameAdmin.includes('プレビューでは保存領域を利用できません')) {
  throw new Error('game-admin.html must warn when preview storage is unavailable.');
}
if (!gameAdmin.includes("SITE_ROOT + 'play.html?id='")) {
  throw new Error('game-admin.html must link published games through the player page.');
}

const gamesPage = fs.readFileSync('games.html', 'utf8');
if (
  !gamesPage.includes('ゲーム広場')
  || !gamesPage.includes("'play.html?id=' + encodeURIComponent(game.id)")
) {
  throw new Error('games.html must present the game plaza and open games through the player page.');
}

const playerPage = fs.readFileSync('play.html', 'utf8');
if (
  !playerPage.includes('ゲーム広場へ戻る')
  || !playerPage.includes("fetch('games.json'")
  || !playerPage.includes('gameFrame.src = game.path')
) {
  throw new Error('play.html must load catalog games with a persistent return link.');
}

const testWorkflow = fs.readFileSync('.github/workflows/test.yml', 'utf8');
if (!testWorkflow.includes("'game-upload/**'")) {
  throw new Error('The test workflow must run for temporary game upload branches.');
}

console.log('Static JavaScript and JSON checks passed.');
