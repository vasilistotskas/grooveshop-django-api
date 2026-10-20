/*
 * The admin sign-in page (core/templates/admin/login.html): copy the
 * authenticator secret, copy all recovery codes, or save them as a text
 * file. The buttons carry the id of the element to read in their `value`;
 * the words shown after a click come from #staff-login-i18n so they stay
 * in the page's language.
 */
(() => {
  const i18n = document.getElementById("staff-login-i18n");
  const status = document.getElementById("staff-login-status");
  if (!i18n) {
    return;
  }

  const textOf = (id) => {
    const source = document.getElementById(id);
    if (!source) {
      return "";
    }
    const items = source.querySelectorAll("li");
    return items.length
      ? Array.from(items, (item) => item.textContent.trim()).join("\n")
      : source.textContent.trim();
  };

  const confirmed = (button, message) => {
    const label = button.querySelector(".js-staff-label");
    const original = label.textContent;
    label.textContent = message;
    status.textContent = message;
    window.setTimeout(() => {
      label.textContent = original;
      status.textContent = "";
    }, 2000);
  };

  const download = (text, filename) => {
    const url = URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  };

  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".js-staff-copy, .js-staff-download");
    if (!button) {
      return;
    }
    const text = textOf(button.value);
    if (button.classList.contains("js-staff-copy")) {
      await navigator.clipboard.writeText(text);
      confirmed(button, i18n.dataset.copied);
    } else {
      download(text, i18n.dataset.filename);
      confirmed(button, i18n.dataset.saved);
    }
  });
})();
