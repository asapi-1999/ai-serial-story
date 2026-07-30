'use strict';

const assert = require('node:assert/strict');
const {
  decodeHtmlText,
  defaultGameId,
  inspectHtml,
  isValidGameId,
  parseArguments,
  slugFromFilename,
  titleFromHtml,
  todayInJapan,
} = require('../tools/register-game.js');

assert.deepEqual(parseArguments(['game.html']), {
  source: 'game.html',
  skipPreview: false,
  prepareOnly: false,
});
assert.deepEqual(parseArguments(['--skip-preview', '--prepare-only', 'game.html']), {
  source: 'game.html',
  skipPreview: true,
  prepareOnly: true,
});
assert.throws(() => parseArguments(['--unknown']), /不明なオプション/);
assert.throws(() => parseArguments(['one.html', 'two.html']), /1つだけ/);

assert.equal(slugFromFilename('Forest_Game 20.html'), 'forest-game-20');
assert.equal(
  defaultGameId('games/ikimono-biotop-adventure-20sec/index.html'),
  'ikimono-biotop-adventure-20sec',
);
assert.equal(isValidGameId('forest-game-20'), true);
assert.equal(isValidGameId('Forest_Game'), false);
assert.equal(decodeHtmlText('森 &amp; 池 &#x1F41F;'), '森 & 池 🐟');
assert.equal(decodeHtmlText('範囲外: &#99999999;'), '範囲外: &#99999999;');
assert.equal(
  titleFromHtml('<!doctype html><html><head><title>森 &amp; 池</title></head></html>'),
  '森 & 池',
);
assert.equal(
  todayInJapan(new Date('2026-07-29T15:30:00Z')),
  '2026-07-30',
);

const inspection = inspectHtml(`<!doctype html>
<html>
<head>
  <script>const score = 1;</script>
  <script type="module">await Promise.resolve();</script>
  <script src="https://cdn.example.com/game.js"></script>
</head>
<body><img src="data:image/png;base64,AA=="><iframe src="help.html"></iframe></body>
</html>`);
assert.deepEqual(
  inspection.externalReferences,
  ['https://cdn.example.com/game.js', 'help.html'],
);
assert.deepEqual(inspection.suspectedSecrets, []);

assert.throws(
  () => inspectHtml('<html><script>const = 1;</script></html>'),
  /構文にエラー/,
);
assert.throws(
  () => inspectHtml('<html><script type="module">await ;</script></html>'),
  /module構文にエラー/,
);
assert.throws(() => inspectHtml('<div>fragment</div>'), /HTML文書として認識/);

const secretInspection = inspectHtml(
  '<html><script>const token = "github_pat_abcdefghijklmnopqrstuvwxyz";</script></html>',
);
assert.deepEqual(secretInspection.suspectedSecrets, ['GitHub token']);

console.log('Game registration helper tests passed.');
