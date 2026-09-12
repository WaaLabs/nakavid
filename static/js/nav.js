// Native <details> menus in the nav don't close on their own when you click
// elsewhere on the page — that's the trade-off for keyboard and
// screen-reader behaviour coming for free instead of a scripted dropdown
// (see the .nv-menu comment in nakavid.css). This is the one bit of
// behaviour <details> doesn't give you, so it's the one bit worth wiring up.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.nv-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) {
      menu.open = false;
    }
  });
});
