# Swapping the domain — moved

The retargeting procedure now lives in **[`SKILL.md`](../../../../SKILL.md)** at
the repository root, as the `retarget-aegis` skill.

Nothing here. Deliberately.

This file used to hold a full retarget checklist, and `adapter/README.md` held
another, and the eight module docstrings held a third numbering. Three copies of
one procedure is how one of them becomes wrong — this directory simultaneously
claimed "piece 2 of 5", "3 of 5", "4 of 5", "6 of 5" and "**6 of 6**" while
holding ten pieces, and two of those pieces appeared in no checklist at all.

So there is now exactly one procedure, at the root, where an agent handed this
repository will find it. `README.md` beside this file remains as the local map of
the ten pieces; it is not a second procedure.

`backend/tests/adapter/test_piece_manifest.py` enforces both halves: that the
authoritative procedure names all ten pieces, and that this file stays a pointer
rather than growing a checklist back.
