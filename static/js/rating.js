function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

document.querySelectorAll("[data-rate-url]").forEach((container) => {
  const url = container.dataset.rateUrl;

  container.querySelectorAll(".rating__button").forEach((button) => {
    button.addEventListener("click", async () => {
      const alreadyActive = button.getAttribute("aria-pressed") === "true";
      const nextRating = alreadyActive ? "clear" : button.dataset.value;

      let response;
      try {
        response = await fetch(url, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCookie("csrftoken"),
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: `rating=${encodeURIComponent(nextRating)}`,
        });
      } catch {
        return;
      }
      if (!response.ok) {
        return;
      }
      const data = await response.json();

      container.querySelectorAll(".rating__button").forEach((b) => {
        b.setAttribute("aria-pressed", "false");
      });
      if (data.rating === true) {
        container.querySelector('[data-value="up"]').setAttribute("aria-pressed", "true");
      } else if (data.rating === false) {
        container.querySelector('[data-value="down"]').setAttribute("aria-pressed", "true");
      }
    });
  });
});
