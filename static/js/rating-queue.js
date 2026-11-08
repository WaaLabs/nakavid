// Keyboard shortcuts for the rating queue: G = good, B = bad, S = skip.
// The buttons are a plain form POST, so this is only a shortcut layer —
// the page works without it.
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  const target = event.target;
  if (target instanceof HTMLElement && target.closest("input, textarea, select")) {
    return;
  }
  const button = document.querySelector(
    `.rating-queue__actions [data-key="${event.key.toLowerCase()}"]`
  );
  if (button) {
    event.preventDefault();
    button.click();
  }
});
