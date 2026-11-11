// Sample looks for the page: ?look=sky or ?look=midnight (styles in looks.css). Without ?look the
// page is unchanged. With it, a small switcher in the top bar moves between the samples.
const LOOKS = [
  ["original", "Original"],
  ["sky", "Sky"],
  ["midnight", "Midnight"],
];

const params = new URLSearchParams(location.search);
export const look = LOOKS.some(([id]) => id === params.get("look")) ? params.get("look") : null;

if (look && look !== "original") document.documentElement.dataset.look = look;

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
