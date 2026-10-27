#!/usr/bin/env node
// Code guard (2026-10-27): the js/ slices ship minified, with local names mangled and every comment gone, so
// the browser gets working code and not the source with its notes. Runs IN PLACE on a copy of js/ — the Docker
// build (node stage) and the e2e CI job, so the smoke test runs the code that ships. Never on your checkout.
// Top-level names stay: the slices share one global scope and the markup calls them (onclick="…").
//   node scripts/minify-js.js <dir>
const fs = require('fs');
const path = require('path');
const { minify } = require('terser');

(async () => {
  const dir = process.argv[2];
  if (!dir) { console.error('usage: minify-js.js <dir>'); process.exit(2); }
  let before = 0, after = 0;
  for (const name of fs.readdirSync(dir).filter((n) => n.endsWith('.js')).sort()) {
    const file = path.join(dir, name);
    const src = fs.readFileSync(file, 'utf8');
    const out = await minify(src, {
      ecma: 2020,
      compress: { passes: 1 },
      mangle: { toplevel: false },
      format: { comments: false },
    });
    if (!out.code) throw new Error('minify failed: ' + name);
    fs.writeFileSync(file, out.code);
    before += src.length; after += out.code.length;
  }
  console.log(`minified ${dir}: ${(before / 1e6).toFixed(2)} MB -> ${(after / 1e6).toFixed(2)} MB`);
})().catch((e) => { console.error(e); process.exit(1); });
