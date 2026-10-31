// Apply the saved appearance before the first paint, including boarding links.
(() => {
  let saved;
  try {
    saved = localStorage.getItem('recovery-theme');
  } catch {}
  const theme =
    saved === 'light' || saved === 'dark'
      ? saved
      : matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').content =
    theme === 'dark' ? '#171717' : '#f8f8f8';
})();
