// Filter gallery tanpa framework. Semua keadaan ada di DOM (data-*), bukan di memori.
(function () {
  "use strict";

  const grid = document.getElementById("media-grid");
  if (!grid) return;

  const filters = Array.from(document.querySelectorAll(".filter"));
  const status = document.querySelector(".gallery-status");
  const state = { project: "", kind: "", mode: "" };
  const cards = Array.from(grid.querySelectorAll(".media-card"));

  function apply() {
    let visible = 0;
    cards.forEach((card) => {
      const ok =
        (!state.project || card.dataset.project === state.project) &&
        (!state.kind || card.dataset.kind === state.kind) &&
        (!state.mode || card.dataset.mode === state.mode);
      card.hidden = !ok;
      if (ok) visible += 1;
    });
    if (status) {
      status.textContent = `${visible} dari ${cards.length} media ditampilkan`;
    }
  }

  filters.forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.filter;
      if (!name) return;
      state[name] = button.dataset.value || "";
      document
        .querySelectorAll(`.filter[data-filter="${name}"]`)
        .forEach((peer) => peer.classList.toggle("is-active", peer === button));
      apply();
    });
  });

  // Dukungan tautan langsung: gallery.html?project=readyasset
  const params = new URLSearchParams(location.search);
  ["project", "kind", "mode"].forEach((name) => {
    const value = params.get(name);
    if (!value) return;
    const button = document.querySelector(
      `.filter[data-filter="${name}"][data-value="${CSS.escape(value)}"]`
    );
    if (button) {
      button.click();
    }
  });

  apply();
})();
