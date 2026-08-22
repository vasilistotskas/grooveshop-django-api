/**
 * Crash guard for django-unfold's ⌘K command palette.
 *
 * django-unfold 0.104.1 (latest at time of writing; unfixed on
 * upstream ``main``) ships an unguarded ``selectItem`` in the
 * ``searchCommand`` Alpine component:
 *
 *     const link = this.items[this.currentIndex - 1].querySelector("a");
 *
 * ``items`` is ``undefined`` until the first htmx response lands
 * (results are debounced 500ms server-side round trips), and
 * ``currentIndex`` is 0 until a result row is highlighted. Pressing
 * Enter in either window dereferences ``undefined`` → TypeError —
 * after which the palette's Alpine state is left inconsistent and the
 * search feels broken until the dialog is closed and reopened.
 *
 * This script loads BEFORE unfold's ``app.js`` (custom SCRIPTS render
 * above it in ``unfold/layouts/skeleton.html``), so it cannot wrap the
 * factory at load time. Instead it defers to ``alpine:init``, which
 * Alpine (loaded ``defer``, i.e. after all synchronous scripts) fires
 * before evaluating any ``x-data`` expression — by then ``app.js`` has
 * defined ``window.searchCommand``, and replacing it here means the
 * ``x-data="searchCommand()"`` expression picks up the wrapped
 * factory.
 *
 * The wrap changes exactly one behavior: ``selectItem`` becomes a
 * no-op when there is no highlighted result row to follow. Everything
 * else — history, favorites, navigation — is upstream's untouched
 * component. Re-check on every django-unfold upgrade; delete this file
 * once upstream guards ``selectItem`` itself.
 */
document.addEventListener("alpine:init", () => {
  const factory = window.searchCommand;

  if (typeof factory !== "function") {
    // Component renamed or dropped by an unfold upgrade: leave
    // upstream behavior alone rather than break palette init.
    return;
  }

  window.searchCommand = function patchedSearchCommand(...args) {
    const component = factory.apply(this, args);
    const selectItem = component.selectItem;

    component.selectItem = function guardedSelectItem(...selectArgs) {
      const item = this.items && this.items[this.currentIndex - 1];

      if (!item || !item.querySelector("a")) {
        return undefined;
      }

      return selectItem.apply(this, selectArgs);
    };

    return component;
  };
});
