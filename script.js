const filterButtons = document.querySelectorAll(".filter-button");
const taskCards = document.querySelectorAll(".task-card");

filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const filter = button.dataset.filter;

    filterButtons.forEach((item) => item.classList.toggle("active", item === button));

    taskCards.forEach((card) => {
      const shouldShow = filter === "all" || card.dataset.kind === filter;
      card.classList.toggle("is-hidden", !shouldShow);
    });
  });
});
