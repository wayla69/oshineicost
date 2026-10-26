// Tailwind CSS 3.4.17 — the SAME engine and the SAME (default) config the page used to run in the browser
// (vendor/tailwind.js, the Play CDN build, no inline tailwind.config), compiled ahead of time to
// vendor/tailwind.build.css, which index.html links at the end of <head> — where the runtime injected its <style>.
//
// AFTER ADDING NEW TAILWIND CLASSES (index.html, tabs/*.html, js/*.js): run `npm run build:css` in tests/e2e
// and commit vendor/tailwind.build.css. CI (the e2e job in .github/workflows/test.yml) rebuilds it and fails
// when the committed file is out of date.
//
// A class name built at run time from pieces ('text-' + c) is invisible to the scan: write the whole class
// name somewhere in the source, or add a pattern to the safelist below.
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: {
    relative: true,                 // the globs are relative to this file, wherever the CLI is run from
    files: ['./index.html', './tabs/**/*.html', './js/**/*.js'],
  },
  // the Play CDN's defaults: darkMode 'media' (no dark: class is used; the app's dark theme is body.dark in its own CSS),
  // the default theme, every core plugin including preflight, no plugins
  theme: { extend: {} },
  plugins: [],
  safelist: [],
};
