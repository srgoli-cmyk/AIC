// The page's look. Sky is the default (set on <html> in app.html, so there is no flash). ?look=midnight
// or ?look=original shows another one, and then a small switcher in the top bar moves between them.
const LOOKS = [
  ["sky", "Sky"],
  ["midnight", "Midnight"],
  ["original", "Original"],
];

const params = new URLSearchParams(location.search);
export const look = LOOKS.some(([id]) => id === params.get("look")) ? params.get("look") : null;

if (look === "original") delete document.documentElement.dataset.look;
else if (look) document.documentElement.dataset.look = look;

if (look) {
  const bar = document.querySelector(".top-right");
  const nav = document.createElement("nav");
  nav.className = "looks";
  nav.setAttribute("aria-label", "Sample look");
  for (const [id, name] of LOOKS) {
    const a = document.createElement("a");
    const p = new URLSearchParams(location.search);
    p.set("look", id);
    a.href = `${location.pathname}?${p.toString().replace(/demo=(&|$)/, "demo$1")}`;
    a.textContent = name;
    if (id === look) a.setAttribute("aria-current", "true");
    nav.append(a);
  }
  bar?.prepend(nav);
}
