(() => {
  const root = document.querySelector(".theory-progress");
  if (!root) return;
  const bar = document.getElementById("theoryBar");
  const pctLabel = document.getElementById("theoryPct");
  const cta = document.getElementById("theoryCta");
  const article = document.querySelector(".theory-article");
  const url = root.dataset.progressUrl;
  let lastSent = Number(root.dataset.initial || 0);
  let done = root.dataset.done === "1";

  const measure = () => {
    if (!article) return 100;
    const top = article.getBoundingClientRect().top + window.scrollY;
    const height = article.offsetHeight - window.innerHeight * 0.72;
    if (height <= 0) return 100;
    const y = window.scrollY - top + 40;
    return Math.max(0, Math.min(100, Math.round((y / height) * 100)));
  };

  const send = async (pct, completed) => {
    if (!url) return;
    try {
      await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.csrfToken || "",
        },
        body: JSON.stringify({ pct, completed }),
      });
    } catch (_) {
      /* сеть не должна ломать чтение */
    }
  };

  const tick = () => {
    const pct = measure();
    if (bar) bar.style.width = `${pct}%`;
    if (pctLabel) pctLabel.textContent = `${pct}%`;
    const completed = pct >= 98;
    if (completed && cta) cta.hidden = false;
    if (completed && !done) {
      done = true;
      send(100, true);
      lastSent = 100;
      return;
    }
    if (pct - lastSent >= 8) {
      lastSent = pct;
      send(pct, false);
    }
  };

  window.addEventListener("scroll", tick, { passive: true });
  window.addEventListener("resize", tick);
  document.addEventListener("DOMContentLoaded", () => {
    tick();
    if (measure() >= 98 && !done) send(100, true);
  });
})();
