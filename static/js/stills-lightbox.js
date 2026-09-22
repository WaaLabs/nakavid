const lightbox = document.getElementById("still-lightbox");

if (lightbox) {
  const image = lightbox.querySelector(".lightbox__image");
  const caption = lightbox.querySelector(".lightbox__caption");

  document.querySelectorAll("[data-lightbox-src]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      image.src = trigger.dataset.lightboxSrc;
      image.alt = trigger.dataset.lightboxCaption || "";
      caption.textContent = trigger.dataset.lightboxCaption || "";
      lightbox.showModal();
    });
  });

  lightbox.querySelector(".lightbox__close").addEventListener("click", () => {
    lightbox.close();
  });

  // A click lands on the <dialog> element itself only when it hits the
  // backdrop — a click on any real content inside stops there instead.
  lightbox.addEventListener("click", (event) => {
    if (event.target === lightbox) {
      lightbox.close();
    }
  });
}
