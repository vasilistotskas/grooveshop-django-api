/**
 * TinyMCE save-on-submit safety net for the Webside admin.
 *
 * django-tinymce normally registers a form-submit handler that calls
 * tinymce.triggerSave() to copy editor content back to the underlying
 * <textarea> before the form is posted. django-unfold's admin theme
 * replaces parts of the admin shell and that handler can end up not
 * being wired, with a silent failure mode: the editor shows the
 * user's edit in the iframe, but the textarea retains its page-load
 * value, so the POST body has no content change. The save returns
 * 302 success, the row's updated_at bumps, and django-simple-history
 * writes a row, but no field actually changes — the user reloads and
 * sees their edit "lost".
 *
 * This script wires the missing hook three ways so at least one fires:
 *   1. tinymce.triggerSave() on every form submit (capture phase, so
 *      we run before any other handler that might short-circuit).
 *   2. editor.on('change input keyup blur ExecCommand SetContent') →
 *      editor.save() — continuous sync to the textarea regardless of
 *      submit handler state. Using the editor's own 'save' method
 *      writes the current iframe HTML to the bound textarea.
 *   3. window.addEventListener('beforeunload', triggerSave) — last-
 *      ditch sync if a click handler navigates away without a real
 *      submit (e.g. unfold's "save and continue" button under some
 *      builds).
 */
(function () {
	"use strict";

	// Idempotent: don't double-bind if this script is loaded twice
	// (admin includes can stack when extra_js arrays grow).
	if (window.__webside_tinymce_save_sync_loaded) {
		return;
	}
	window.__webside_tinymce_save_sync_loaded = true;

	function triggerSave() {
		if (window.tinymce && typeof window.tinymce.triggerSave === "function") {
			try {
				window.tinymce.triggerSave();
			} catch (e) {
				// Never block submit on a sync error — the textarea may
				// already hold the right value, and the original Django
				// admin error path is the right surface for any failure.
				console.warn("tinymce.triggerSave failed", e);
			}
		}
	}

	function bindEditorAutoSync(editor) {
		if (!editor || editor.__webside_save_bound) return;
		editor.__webside_save_bound = true;
		// 'save' on the editor copies its current content to the
		// textarea it's bound to. We trigger it on every meaningful
		// content event so the textarea is always fresh, not just at
		// submit time.
		editor.on("change input keyup blur ExecCommand SetContent", function () {
			try {
				editor.save();
			} catch (e) {
				/* swallow — see triggerSave note */
			}
		});
	}

	function bindAllEditors() {
		if (!window.tinymce) return;
		var eds = window.tinymce.editors || [];
		for (var i = 0; i < eds.length; i++) {
			bindEditorAutoSync(eds[i]);
		}
		// And catch editors that get added later in the lifecycle
		// (admin sometimes lazy-inits editors inside collapsed
		// fieldsets when they're expanded).
		if (window.tinymce.editorManager && !window.__webside_addeditor_bound) {
			window.__webside_addeditor_bound = true;
			window.tinymce.editorManager.on("AddEditor", function (e) {
				bindEditorAutoSync(e.editor);
			});
		}
	}

	function attachFormHandlers() {
		var forms = document.querySelectorAll("form");
		for (var i = 0; i < forms.length; i++) {
			var form = forms[i];
			if (form.__webside_save_bound) continue;
			form.__webside_save_bound = true;
			// Capture phase so we run before Django admin's own
			// handler (which serialises the form right after).
			form.addEventListener("submit", triggerSave, true);
		}
	}

	function init() {
		bindAllEditors();
		attachFormHandlers();
	}

	if (document.readyState === "complete" || document.readyState === "interactive") {
		// queueMicrotask gives TinyMCE's own DOMContentLoaded handler
		// time to populate window.tinymce.editors first.
		setTimeout(init, 0);
	} else {
		document.addEventListener("DOMContentLoaded", init);
	}

	// django-tinymce can mount editors after our DOMContentLoaded —
	// retry once a second for the first 5s to catch late-init editors.
	var tries = 0;
	var poll = setInterval(function () {
		tries++;
		init();
		if (tries >= 5) {
			clearInterval(poll);
		}
	}, 1000);

	// Last-ditch sync at unload time
	window.addEventListener("beforeunload", triggerSave);
})();
