const fs = require('node:fs');

const htmlFiles = ['index.html', 'latest.html', 'work.html', 'edit.html'];
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

for (const filename of ['config.json', 'stories.json', 'bible.json', 'library.json']) {
  JSON.parse(fs.readFileSync(filename, 'utf8'));
}

console.log('Static JavaScript and JSON checks passed.');
