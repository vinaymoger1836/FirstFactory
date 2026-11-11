// Runs before first paint. "system" (default) = no attribute; CSS follows prefers-color-scheme.
(function () {
  try {
    var t = localStorage.getItem("fl-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    /* storage unavailable: follow the system theme */
  }
})();
