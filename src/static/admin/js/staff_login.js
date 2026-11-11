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

  const CONFIRMATION_MS = 2000;
  const timers = new WeakMap();

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

  // Shows `message` on the button for a moment. The original label is read
  // once, so a second click while the message shows cannot make the message
  // the label for good.
  const confirmed = (button, message) => {
    const label = button.querySelector(".js-staff-label");
    label.dataset.original ??= label.textContent;
    window.clearTimeout(timers.get(button));
    label.textContent = message;
    status.textContent = message;
    timers.set(
      button,
      window.setTimeout(() => {
        label.textContent = label.dataset.original;
        status.textContent = "";
      }, CONFIRMATION_MS),
    );
  };

  // The codes are shown once: when the clipboard is refused (no secure
  // context, or the browser said no), select them so they can be copied by
  // hand, and say so.
  const selectForManualCopy = (id) => {
    const source = document.getElementById(id);
    if (!source) {
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(source);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
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
    if (button.classList.contains("js-staff-download")) {
      download(text, i18n.dataset.filename);
      confirmed(button, i18n.dataset.saved);
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      confirmed(button, i18n.dataset.copied);
    } catch {
      selectForManualCopy(button.value);
      status.textContent = i18n.dataset.copyFailed;
    }
  });
})();
