"""List remaining untranslated msgids — both simple and multi-line."""

import sys
from pathlib import Path

# Force UTF-8 on stdout so the Greek/arrow chars don't trip cp1252.
sys.stdout.reconfigure(encoding="utf-8")

PO = Path("locale/el/LC_MESSAGES/django.po")
text = PO.read_text(encoding="utf-8")

# Walk line-by-line, collect each msgid block + its msgstr, emit
# only those where msgstr is empty (including the multi-line form
# where msgstr is on multiple lines that are all empty).
lines = text.splitlines()
i = 0
blocks: list[tuple[str, int]] = []
while i < len(lines):
    line = lines[i]
    if line.startswith('msgid "'):
        # collect msgid lines
        msgid_parts = [line[len("msgid ") :]]
        j = i + 1
        while j < len(lines) and lines[j].startswith('"'):
            msgid_parts.append(lines[j])
            j += 1
        # next should be msgstr
        if j < len(lines) and lines[j].startswith('msgstr "'):
            msgstr_parts = [lines[j][len("msgstr ") :]]
            k = j + 1
            while k < len(lines) and lines[k].startswith('"'):
                msgstr_parts.append(lines[k])
                k += 1
            # decode python-like quoted strings
            msgid = "".join(eval(p) for p in msgid_parts)
            msgstr = "".join(eval(p) for p in msgstr_parts)
            if msgid and not msgstr:
                blocks.append((msgid, i + 1))
            i = k
            continue
    i += 1

print(f"Untranslated: {len(blocks)}")
# Filter for terms 80 chars or fewer (admin-grade copy)
shortish = [(m, ln) for m, ln in blocks if len(m) <= 80]
print(f"Untranslated short (<=80 chars): {len(shortish)}")
print()
# Print first 150 short ones sorted by length
shortish.sort(key=lambda x: (len(x[0]), x[0]))
limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
for m, ln in shortish[:limit]:
    print(f"  ({ln:>5}) [{len(m):>3}]  {m!r}")
