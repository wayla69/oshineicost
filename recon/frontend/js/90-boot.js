/* recon frontend — 90-boot.js: boot: draw the nav, then render the current route.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
_initNav();
route();
