/**
 * TinyMCE save-on-submit safety net for the Webside admin.
 *
 * django-tinymce normally wires a form-submit handler so that
 * ``tinymce.triggerSave()`` copies the editor's iframe HTML back to
 * the underlying ``<textarea>`` before the form is POSTed. With
 * django-tinymce 5.x + django-unfold + TinyMCE 7.8 (current
 * production) that handoff doesn't reliably fire — and even when it
 * does, ``tinymce.triggerSave()`` itself misbehaves: it iterates
 * ``tinymce.editors`` internally, which is undefined in TinyMCE 7+
 * (the array moved off the public surface), so the call returns
 * void without ever calling ``editor.save()`` on anyone.
 *
 * Symptom: user edits the description in the iframe, clicks save,
 * gets a 302 success and a ``django-simple-history`` row with
 * timestamps bumped — but every column matches the previous row
 * exactly because the form POST body still contained the page-load
 * textarea value, not the edit.
 *
 * This script bypasses both deprecated paths and uses only the
 * documented stable API:
 *   - ``tinymce.get(id)`` for per-textarea editor lookup
 *   - ``editor.save()`` to copy iframe HTML to the bound textarea
 *   - ``tinymce.on('AddEditor', cb)`` (on the global, NOT on the
 *     deprecated EditorManager) to bind handlers to editors that
 *     init after this script runs.
 *
 * It wires three layers of redundancy so at least one always fires:
 *   1. Per-editor ``input change keyup blur ExecCommand SetContent``
 *      → ``editor.save()``. Continuous textarea sync, not just at
 *      submit time. Survives any unfold theme weirdness around the
 *      submit lifecycle.
 *   2. Form ``submit`` listener (capture phase): walks every
 *      textarea on the form, looks up its editor via
 *      ``tinymce.get(textarea.id)``, and calls ``editor.save()``
 *      directly. Does NOT rely on ``tinymce.triggerSave()`` /
 *      ``tinymce.editors`` (broken in v7+).
 *   3. ``beforeunload`` last-ditch sync for click handlers that
 *      navigate without a real submit (some unfold builds fire a
 *      programmatic navigation on "save and continue").
 */
(function () {
	"use strict";

	// Idempotent — admin includes can stack when SCRIPTS arrays grow.
	if (window.__webside_tinymce_save_sync_loaded) {
		return;
	}
	window.__webside_tinymce_save_sync_loaded = true;

	// True if the global TinyMCE namespace is loaded and usable.
	function tinymceReady() {
		return !!(window.tinymce && typeof window.tinymce.get === "function");
	}

	// Find every TinyMCE-backed textarea on the document by walking
	// real DOM, not the deprecated tinymce.editors array. Returns the
	// array of (textarea, editor) pairs where editor is the live
	// TinyMCE instance bound to that textarea (if any).
	function findEditors() {
		if (!tinymceReady()) return [];
		var pairs = [];
		var textareas = document.querySelectorAll("textarea");
		for (var i = 0; i < textareas.length; i++) {
			var ta = textareas[i];
			if (!ta.id) continue;
			var ed = window.tinymce.get(ta.id);
			if (ed) pairs.push({ ta: ta, ed: ed });
		}
		return pairs;
	}

	// Sync editor → textarea via the documented per-editor API.
	function safeSave(editor) {
		try {
			editor.save();
		} catch (e) {
			// Never block submit on a sync error.
			console.warn("[webside] editor.save() failed", e);
		}
	}

	// Wire continuous content sync on a single editor. Called on every
	// editor we discover (current + future via AddEditor).
	function bindEditorAutoSync(editor) {
		if (!editor || editor.__webside_save_bound) return;
		editor.__webside_save_bound = true;
		// Cover every event TinyMCE 7/8 fires when content meaningfully
		// changes:
		//   input/keyup → typed-character changes (real-time)
		//   change → committed change (focus leaves)
		//   blur → moving focus out of the iframe
		//   ExecCommand → toolbar buttons (bold, paste, etc.)
		//   SetContent → programmatic content set
		editor.on(
			"input change keyup blur ExecCommand SetContent",
			function () {
				safeSave(editor);
			}
		);
	}

	// Sync EVERY editor on the page. Used at script-load (in case
	// TinyMCE already initialised before we ran) and at form submit.
	function syncAll() {
		var pairs = findEditors();
		for (var i = 0; i < pairs.length; i++) {
			safeSave(pairs[i].ed);
		}
	}

	// Bind continuous-sync on every currently-present editor.
	function bindAllEditorsNow() {
		var pairs = findEditors();
		for (var i = 0; i < pairs.length; i++) {
			bindEditorAutoSync(pairs[i].ed);
		}
	}

	// Capture-phase form submit listener — runs before the form's own
	// listeners and before browser navigation. Forces every editor to
	// flush its iframe HTML to the bound textarea so the form's POST
	// body reflects the user's actual content.
	function attachFormHandlers() {
		var forms = document.querySelectorAll("form");
		for (var i = 0; i < forms.length; i++) {
			var form = forms[i];
			if (form.__webside_save_bound) continue;
			form.__webside_save_bound = true;
			form.addEventListener("submit", syncAll, true);
		}
	}

	// Hook AddEditor on the global tinymce object — this is the
	// documented v7/v8 API. The editorManager surface was removed and
	// any code that tries to bind there silently no-ops.
	function bindAddEditorListener() {
		if (!tinymceReady()) return;
		if (window.__webside_addeditor_bound) return;
		if (typeof window.tinymce.on !== "function") return;
		window.__webside_addeditor_bound = true;
		window.tinymce.on("AddEditor", function (e) {
			if (e && e.editor) bindEditorAutoSync(e.editor);
		});
	}

	function init() {
		bindAddEditorListener();
		bindAllEditorsNow();
		attachFormHandlers();
	}

	if (
		document.readyState === "complete"
		|| document.readyState === "interactive"
	) {
		// queueMicrotask gives TinyMCE its own init tick to populate
		// editor instances first.
		setTimeout(init, 0);
	} else {
		document.addEventListener("DOMContentLoaded", init);
	}

	// Editors can mount after our DOMContentLoaded — retry once a
	// second for the first 5 seconds to catch late-init editors that
	// missed the AddEditor hook (e.g. inside lazy-loaded fieldsets).
	var tries = 0;
	var poll = setInterval(function () {
		tries++;
		init();
		if (tries >= 5) {
			clearInterval(poll);
		}
	}, 1000);

	// Last-ditch sync at unload time
	window.addEventListener("beforeunload", syncAll);
})();
