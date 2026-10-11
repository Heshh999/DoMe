/* DoMe: apply the saved light/dark choice before the first paint (no flash of the wrong theme).
   Loaded as a classic, render-blocking script from <head>: the CSP allows no inline script.
   Mirrors applyTheme() in src/lib/theme.ts (test/theme.test.ts keeps them in step). */
(function () {
  var choice = null;
  try {
    choice = window.localStorage.getItem("dome.theme");
  } catch {
    choice = null; // storage blocked: follow the device
  }
  if (choice !== "light" && choice !== "dark") return;
  var root = document.documentElement;
  root.setAttribute("data-theme", choice);
  var color = choice === "dark" ? "#001441" : "#f6f8fc";
  var metas = document.querySelectorAll('meta[name="theme-color"]');
  for (var i = 0; i < metas.length; i++) metas[i].setAttribute("content", color);
})();
