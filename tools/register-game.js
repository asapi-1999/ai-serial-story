#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline/promises');
const { spawn, spawnSync } = require('node:child_process');
const { stdin, stdout } = require('node:process');

const REPO_ROOT = path.resolve(__dirname, '..');
const CATALOG_PATH = path.join(REPO_ROOT, 'games.json');
const MAX_FILE_SIZE = 5 * 1024 * 1024;
const SITE_ROOT = 'https://asapi-1999.github.io/ai-serial-story/';

class UserError extends Error {}

function printHelp() {
  console.log(`HTMLゲーム登録

使い方:
  node tools/register-game.js <ゲームHTML> [--skip-preview] [--prepare-only]

オプション:
  --skip-preview  ブラウザでのローカルプレビューを省略
  --prepare-only  ファイル更新とテストまで行い、コミット・pushは行わない
  --help          このヘルプを表示
`);
}

function parseArguments(argv) {
  const options = {
    source: '',
    skipPreview: false,
    prepareOnly: false,
  };

  for (const argument of argv) {
    if (argument === '--help' || argument === '-h') {
      options.help = true;
    } else if (argument === '--skip-preview') {
      options.skipPreview = true;
    } else if (argument === '--prepare-only') {
      options.prepareOnly = true;
    } else if (argument.startsWith('-')) {
      throw new UserError(`不明なオプションです: ${argument}`);
    } else if (!options.source) {
      options.source = argument;
    } else {
      throw new UserError('HTMLファイルは1つだけ指定してください。');
    }
  }
  return options;
}

function decodeHtmlText(value) {
  return value
    .replace(/<[^>]*>/g, '')
    .replace(/&#x([0-9a-f]+);/gi, (original, code) => {
      const valueNumber = Number.parseInt(code, 16);
      return valueNumber <= 0x10FFFF ? String.fromCodePoint(valueNumber) : original;
    })
    .replace(/&#([0-9]+);/g, (original, code) => {
      const valueNumber = Number.parseInt(code, 10);
      return valueNumber <= 0x10FFFF ? String.fromCodePoint(valueNumber) : original;
    })
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&amp;/gi, '&')
    .trim();
}

function titleFromHtml(html) {
  const match = html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i);
  return match ? decodeHtmlText(match[1]) : '';
}

function slugFromFilename(filename) {
  const base = path.basename(filename).replace(/\.html?$/i, '').toLowerCase();
  const slug = base
    .replace(/[_\s]+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
  return slug || `game-${Date.now().toString(36)}`;
}

function defaultGameId(filename) {
  const gamesDirectory = path.join(REPO_ROOT, 'games');
  const relative = path.relative(gamesDirectory, path.resolve(filename));
  const segments = relative.split(path.sep);
  if (
    segments.length === 2
    && /^index\.html?$/i.test(segments[1])
    && isValidGameId(segments[0])
  ) {
    return segments[0];
  }
  return slugFromFilename(filename);
}

function isValidGameId(value) {
  return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value);
}

function getAttribute(attributes, name) {
  const pattern = new RegExp(
    `(?:^|\\s)${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s"'=<>\\x60]+))`,
    'i',
  );
  const match = attributes.match(pattern);
  return match ? (match[1] ?? match[2] ?? match[3] ?? '') : null;
}

function checkModuleSyntax(source, label) {
  const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'register-game-module-'));
  const modulePath = path.join(temporaryDirectory, 'script.mjs');
  try {
    fs.writeFileSync(modulePath, source, 'utf8');
    const result = spawnSync(process.execPath, ['--check', modulePath], {
      encoding: 'utf8',
      windowsHide: true,
    });
    if (result.status !== 0) {
      const detail = (result.stderr || result.stdout || 'invalid module syntax').trim();
      throw new UserError(`${label} のmodule構文にエラーがあります。\n${detail}`);
    }
  } finally {
    fs.rmSync(temporaryDirectory, { recursive: true, force: true });
  }
}

function inspectHtml(html) {
  if (!/<html[\s>]/i.test(html)) {
    throw new UserError('HTML文書として認識できませんでした。<html>要素を確認してください。');
  }

  const scriptPattern = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  let scriptIndex = 0;
  for (const match of html.matchAll(scriptPattern)) {
    const attributes = match[1];
    const source = match[2];
    if (!source.trim() || getAttribute(attributes, 'src') !== null) {
      continue;
    }

    scriptIndex += 1;
    const type = (getAttribute(attributes, 'type') || '').trim().toLowerCase();
    if (type === 'module') {
      checkModuleSyntax(source, `インラインスクリプト${scriptIndex}`);
    } else if (
      !type
      || type === 'text/javascript'
      || type === 'application/javascript'
      || type === 'text/ecmascript'
      || type === 'application/ecmascript'
    ) {
      try {
        new Function(source);
      } catch (error) {
        throw new UserError(
          `インラインスクリプト${scriptIndex}の構文にエラーがあります: ${error.message}`,
        );
      }
    }
  }

  const externalReferences = [];
  const externalPattern =
    /<(?:script|link|img|audio|video|source|iframe)\b[^>]*?\s(?:src|href)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/gi;
  for (const match of html.matchAll(externalPattern)) {
    const value = match[1] ?? match[2] ?? match[3] ?? '';
    if (value && !/^(?:data:|blob:|#)/i.test(value)) {
      externalReferences.push(value);
    }
  }

  const secretPatterns = [
    { label: 'GitHub token', pattern: /\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]{20,})\b/ },
    { label: 'OpenAI API key', pattern: /\bsk-[A-Za-z0-9_-]{20,}\b/ },
    { label: 'Google API key', pattern: /\bAIza[0-9A-Za-z_-]{30,}\b/ },
    { label: 'private key', pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  ];
  const suspectedSecrets = secretPatterns
    .filter((item) => item.pattern.test(html))
    .map((item) => item.label);

  return {
    externalReferences: [...new Set(externalReferences)],
    suspectedSecrets,
  };
}

function todayInJapan(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  const values = Object.fromEntries(
    parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    windowsHide: true,
    stdio: options.inherit ? 'inherit' : 'pipe',
  });
  if (result.error) {
    throw new UserError(`${command}を実行できませんでした: ${result.error.message}`);
  }
  if (result.status !== 0 && !options.allowFailure) {
    const detail = (result.stderr || result.stdout || '').trim();
    throw new UserError(
      `${command} ${args.join(' ')} が失敗しました。${detail ? `\n${detail}` : ''}`,
    );
  }
  return result;
}

function git(args, options) {
  return run('git', args, options);
}

function relativeToRepo(filename) {
  return path.relative(REPO_ROOT, filename).split(path.sep).join('/');
}

function assertRepositoryState(targetPath) {
  const branch = git(['branch', '--show-current']).stdout.trim();
  if (branch !== 'main') {
    throw new UserError(`mainブランチで実行してください。現在のブランチ: ${branch || '(detached)'}`);
  }

  const staged = git(['diff', '--cached', '--quiet'], { allowFailure: true });
  if (staged.status === 1) {
    throw new UserError('ステージ済みの変更があります。コミットまたは解除してから実行してください。');
  }
  if (staged.status > 1) {
    throw new UserError('Gitのステージ状態を確認できませんでした。');
  }

  const targetRelative = relativeToRepo(targetPath);
  const status = git([
    'status',
    '--porcelain=v1',
    '--',
    'games.json',
    targetRelative,
  ]).stdout.trim();
  if (status) {
    throw new UserError(
      `登録対象に未コミット変更があります。先に整理してください。\n${status}`,
    );
  }
}

function assertRemoteIsCurrent() {
  console.log('\nリモートのmainブランチを確認しています…');
  git(['fetch', 'origin', 'main'], { inherit: true });
  const localSha = git(['rev-parse', 'HEAD']).stdout.trim();
  const remoteSha = git(['rev-parse', 'FETCH_HEAD']).stdout.trim();
  if (localSha !== remoteSha) {
    throw new UserError(
      'ローカルmainとorigin/mainが一致しません。git pull --ff-onlyなどで同期してから再実行してください。',
    );
  }
}

function findPythonCommand() {
  const candidates = process.platform === 'win32'
    ? [['py', ['-3']], ['python', []], ['python3', []]]
    : [['python3', []], ['python', []]];
  for (const [command, prefix] of candidates) {
    const result = spawnSync(command, [...prefix, '--version'], {
      encoding: 'utf8',
      windowsHide: true,
    });
    if (!result.error && result.status === 0) {
      return { command, prefix };
    }
  }
  return null;
}

function runTests() {
  console.log('\n静的検査を実行しています…');
  run(process.execPath, ['tests/check_static.js'], { inherit: true });
  run(process.execPath, ['tests/test_register_game.js'], { inherit: true });

  const python = findPythonCommand();
  if (python) {
    console.log('\nPythonテストを実行しています…');
    run(
      python.command,
      [...python.prefix, '-m', 'unittest', 'discover', '-s', 'tests'],
      { inherit: true },
    );
  } else {
    console.warn('\n注意: Pythonが見つからないため、Pythonテストは省略しました。');
  }
}

async function ask(terminal, label, defaultValue = '', options = {}) {
  while (true) {
    const suffix = defaultValue ? ` [${defaultValue}]` : '';
    const answer = (await terminal.question(`${label}${suffix}: `)).trim();
    const value = answer || defaultValue;
    if (!value && options.required) {
      console.log('この項目は必須です。');
      continue;
    }
    if (options.maxLength && [...value].length > options.maxLength) {
      console.log(`${options.maxLength}文字以内で入力してください。`);
      continue;
    }
    if (options.validate && !options.validate(value)) {
      console.log(options.validationMessage || '入力内容を確認してください。');
      continue;
    }
    return value;
  }
}

async function askYesNo(terminal, label, defaultYes = true) {
  const hint = defaultYes ? 'Y/n' : 'y/N';
  while (true) {
    const answer = (await terminal.question(`${label} [${hint}]: `)).trim().toLowerCase();
    if (!answer) return defaultYes;
    if (answer === 'y' || answer === 'yes') return true;
    if (answer === 'n' || answer === 'no') return false;
    console.log('y または n を入力してください。');
  }
}

function openBrowser(url) {
  let command;
  let args;
  if (process.platform === 'win32') {
    command = 'explorer.exe';
    args = [url];
  } else if (process.platform === 'darwin') {
    command = 'open';
    args = [url];
  } else {
    command = 'xdg-open';
    args = [url];
  }

  try {
    const child = spawn(command, args, {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.on('error', () => {});
    child.unref();
    return true;
  } catch {
    return false;
  }
}

async function previewHtml(terminal, html) {
  const server = http.createServer((request, response) => {
    const requestPath = new URL(request.url, 'http://127.0.0.1').pathname;
    if (requestPath === '/' || requestPath === '/index.html') {
      response.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
      });
      response.end(html);
      return;
    }
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('not found');
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });

  try {
    const address = server.address();
    const url = `http://127.0.0.1:${address.port}/`;
    console.log(`\nローカルプレビュー: ${url}`);
    if (!openBrowser(url)) {
      console.log('ブラウザを自動で開けませんでした。上のURLを開いてください。');
    }
    await terminal.question('確認できたらEnterを押してください。');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function rollbackFiles(state) {
  const conflicts = [];
  const currentCatalog = fs.readFileSync(CATALOG_PATH);
  if (currentCatalog.equals(state.writtenCatalog)) {
    fs.writeFileSync(CATALOG_PATH, state.originalCatalog);
  } else {
    conflicts.push('games.json');
  }

  const currentTarget = fs.existsSync(state.targetPath)
    ? fs.readFileSync(state.targetPath)
    : null;
  if (currentTarget?.equals(state.writtenTarget)) {
    if (state.targetExisted) {
      fs.writeFileSync(state.targetPath, state.originalTarget);
    } else {
      fs.unlinkSync(state.targetPath);
    }
  } else if (currentTarget || state.targetExisted) {
    conflicts.push(relativeToRepo(state.targetPath));
  }

  if (!state.targetExisted && !fs.existsSync(state.targetPath)) {
    if (!state.targetDirectoryExisted) {
      try {
        fs.rmdirSync(path.dirname(state.targetPath));
      } catch {
        // Keep a directory if another process added content to it.
      }
    }
  }

  if (conflicts.length) {
    throw new UserError(
      `処理中に別の変更を検出したため、次のファイルは自動で元に戻していません: ${conflicts.join(', ')}`,
    );
  }
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }
  if (!options.source) {
    printHelp();
    throw new UserError('登録するHTMLファイルを指定してください。');
  }

  const sourcePath = path.resolve(options.source);
  if (!/\.html?$/i.test(sourcePath)) {
    throw new UserError('拡張子が.htmlまたは.htmのファイルを指定してください。');
  }
  const sourceStat = fs.statSync(sourcePath, { throwIfNoEntry: false });
  if (!sourceStat || !sourceStat.isFile()) {
    throw new UserError(`HTMLファイルが見つかりません: ${sourcePath}`);
  }
  if (sourceStat.size > MAX_FILE_SIZE) {
    throw new UserError('HTMLファイルが5MBを超えています。');
  }

  const html = fs.readFileSync(sourcePath, 'utf8');
  const inspection = inspectHtml(html);
  const originalCatalog = fs.readFileSync(CATALOG_PATH);
  const catalog = JSON.parse(originalCatalog.toString('utf8'));
  if (!Array.isArray(catalog)) {
    throw new UserError('games.jsonが配列ではありません。');
  }

  console.log(`\nHTML: ${sourcePath}`);
  console.log(`サイズ: ${Math.round(sourceStat.size / 1024)} KB`);
  console.log('JavaScript構文: OK');
  if (inspection.externalReferences.length) {
    console.warn(`外部ファイル参照: ${inspection.externalReferences.length}件`);
    inspection.externalReferences.slice(0, 5).forEach((value) => console.warn(`  - ${value}`));
  } else {
    console.log('外部ファイル参照: なし');
  }

  const terminal = readline.createInterface({ input: stdin, output: stdout });
  try {
    if (inspection.suspectedSecrets.length) {
      console.warn(
        `\n秘密情報らしき文字列を検出しました: ${inspection.suspectedSecrets.join(', ')}`,
      );
      const continueWithSecret = await askYesNo(
        terminal,
        '内容を確認済みとして処理を続けますか？',
        false,
      );
      if (!continueWithSecret) {
        console.log('登録を中止しました。');
        return;
      }
    }

    const initialTitle = titleFromHtml(html);
    const title = await ask(terminal, 'タイトル', initialTitle, {
      required: true,
      maxLength: 80,
    });
    const id = await ask(terminal, 'ゲームID', defaultGameId(sourcePath), {
      required: true,
      maxLength: 80,
      validate: isValidGameId,
      validationMessage: '半角小文字・数字・ハイフンを使用してください。',
    });

    const targetDirectory = path.join(REPO_ROOT, 'games', id);
    const targetPath = path.join(targetDirectory, 'index.html');
    const catalogIndex = catalog.findIndex((game) => game.id === id);
    const previous = catalogIndex >= 0 ? catalog[catalogIndex] : null;
    const targetExists = fs.existsSync(targetPath);
    if (previous || targetExists) {
      const update = await askYesNo(
        terminal,
        `ゲームID「${id}」は既に存在します。更新しますか？`,
        false,
      );
      if (!update) {
        console.log('登録を中止しました。');
        return;
      }
    }

    const description = await ask(terminal, '紹介文', previous?.description || '', {
      required: true,
      maxLength: 180,
    });
    const genre = await ask(terminal, 'ジャンル', previous?.genre || '', { maxLength: 30 });
    const devicesText = await ask(
      terminal,
      '対応端末（カンマ区切り）',
      (previous?.devices || ['PC', 'スマホ']).join(','),
    );
    const devices = [...new Set(
      devicesText.split(/[,、]/).map((value) => value.trim()).filter(Boolean),
    )];
    const duration = await ask(
      terminal,
      'プレイ時間',
      previous?.duration || '約20秒',
      { maxLength: 24 },
    );
    const icon = await ask(terminal, 'アイコン', previous?.icon || '🎮', { maxLength: 8 });

    if (!options.skipPreview) {
      const shouldPreview = await askYesNo(terminal, 'ローカルプレビューを開きますか？', true);
      if (shouldPreview) {
        await previewHtml(terminal, html);
      }
    }

    console.log('\n登録内容');
    console.log(`  タイトル: ${title}`);
    console.log(`  ゲームID: ${id}`);
    console.log(`  紹介文: ${description}`);
    console.log(`  ジャンル: ${genre || '(なし)'}`);
    console.log(`  対応端末: ${devices.join(', ') || '(なし)'}`);
    console.log(`  プレイ時間: ${duration || '(なし)'}`);
    console.log(`  公開URL: ${SITE_ROOT}play.html?id=${encodeURIComponent(id)}`);

    if (!await askYesNo(terminal, 'この内容で登録準備を進めますか？', true)) {
      console.log('登録を中止しました。');
      return;
    }

    assertRepositoryState(targetPath);
    if (!options.prepareOnly) {
      assertRemoteIsCurrent();
    }

    const game = {
      ...(previous || {}),
      id,
      title,
      description,
      genre,
      devices,
      duration,
      icon: icon || '🎮',
      path: `games/${id}/`,
    };
    const today = todayInJapan();
    if (previous) game.updated = today;
    else game.published = today;

    if (catalogIndex >= 0) catalog[catalogIndex] = game;
    else catalog.push(game);

    const state = {
      originalCatalog,
      targetPath,
      targetExisted: targetExists,
      originalTarget: targetExists ? fs.readFileSync(targetPath) : null,
      targetDirectoryExisted: fs.existsSync(targetDirectory),
      writtenCatalog: Buffer.from(`${JSON.stringify(catalog, null, 2)}\n`, 'utf8'),
      writtenTarget: Buffer.from(html, 'utf8'),
    };

    if (!fs.readFileSync(CATALOG_PATH).equals(originalCatalog)) {
      throw new UserError('入力中にgames.jsonが変更されました。最初からやり直してください。');
    }
    if (
      targetExists
      && !fs.readFileSync(targetPath).equals(state.originalTarget)
    ) {
      throw new UserError('入力中に対象ゲームが変更されました。最初からやり直してください。');
    }
    if (!targetExists && fs.existsSync(targetPath)) {
      throw new UserError('入力中に同じゲームIDのファイルが作成されました。最初からやり直してください。');
    }

    fs.mkdirSync(targetDirectory, { recursive: true });
    fs.writeFileSync(targetPath, state.writtenTarget);
    fs.writeFileSync(CATALOG_PATH, state.writtenCatalog);

    try {
      runTests();
    } catch (error) {
      rollbackFiles(state);
      throw new UserError(`テストに失敗したため変更を元に戻しました。\n${error.message}`);
    }

    console.log('\n作成される変更:');
    git([
      'status',
      '--short',
      '--',
      'games.json',
      relativeToRepo(targetPath),
    ], { inherit: true });

    if (options.prepareOnly) {
      console.log('\nファイル更新とテストが完了しました。コミットとpushは行っていません。');
      return;
    }

    if (!await askYesNo(terminal, 'コミットしてorigin/mainへpushしますか？', true)) {
      rollbackFiles(state);
      console.log('変更を元に戻して登録を中止しました。');
      return;
    }

    const targetRelative = relativeToRepo(targetPath);
    try {
      git(['add', '--', 'games.json', targetRelative]);
      git([
        'commit',
        '-m',
        `${previous || targetExists ? 'ゲームを更新' : 'ゲームを追加'}: ${title}`,
        '--',
        'games.json',
        targetRelative,
      ], { inherit: true });
    } catch (error) {
      git(['restore', '--staged', '--', 'games.json', targetRelative], { allowFailure: true });
      rollbackFiles(state);
      throw error;
    }

    const push = git(['push', 'origin', 'HEAD:main'], {
      inherit: true,
      allowFailure: true,
    });
    if (push.status !== 0) {
      throw new UserError(
        'pushに失敗しました。登録コミットはローカルに残っています。'
        + '\nリモートを確認してから git push origin HEAD:main を再実行してください。',
      );
    }

    console.log('\n公開登録が完了しました。');
    console.log(`${SITE_ROOT}play.html?id=${encodeURIComponent(id)}`);
  } finally {
    terminal.close();
  }
}

if (require.main === module) {
  main().catch((error) => {
    const message = error instanceof UserError ? error.message : (error.stack || String(error));
    console.error(`\nエラー: ${message}`);
    process.exitCode = 1;
  });
}

module.exports = {
  decodeHtmlText,
  defaultGameId,
  inspectHtml,
  isValidGameId,
  parseArguments,
  slugFromFilename,
  titleFromHtml,
  todayInJapan,
};
