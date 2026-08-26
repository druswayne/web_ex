(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  window.csrfToken = token || "";

  const renderMath = () => {
    if (!window.renderMathInElement) return;
    document.querySelectorAll(".md-body, .math-ready, .ai-feedback").forEach((el) => {
      window.renderMathInElement(el, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
        ],
        throwOnError: false,
      });
    });
  };

  document.addEventListener("DOMContentLoaded", renderMath);
})();
