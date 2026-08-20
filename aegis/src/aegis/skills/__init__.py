"""Skills as data — authored in ``SKILL.md``, stored in Postgres, resolved by scope.

The public surface of §10.1-10.3. Three things live behind it:

* :mod:`aegis.skills.document` — the ``SKILL.md`` open standard (agentskills.io) as the
  authoring format, parsed in and rendered back out.
* :mod:`aegis.skills.models` — one tenant-scoped table, registered with its RLS policy
  in the same change.
* :mod:`aegis.skills.store` — resolution ``platform u tenant u user`` **through the
  Phase 3 settings resolver**, and an authoring path that screens a body with the input
  rail before any row exists.

Requires the ``aegis[data]`` extra for the table; the document half imports nothing.
"""

from __future__ import annotations

from aegis.skills.document import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    SKILL_NAME_PATTERN,
    SkillDocument,
    SkillFormatError,
    parse_skill_md,
    render_skill_md,
)
from aegis.skills.models import SKILLS_TABLE, AgentSkill, SkillScope
from aegis.skills.store import (
    SKILLS_ENABLED_KEY,
    InputRail,
    ResolvedSkill,
    SkillBodyRefusedError,
    SkillNotFoundError,
    SkillScopeError,
    delete_skill,
    list_skills,
    load_skill,
    resolve_skills,
    set_active,
    write_skill,
)

__all__ = [
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "SKILLS_ENABLED_KEY",
    "SKILLS_TABLE",
    "SKILL_NAME_PATTERN",
    "AgentSkill",
    "InputRail",
    "ResolvedSkill",
    "SkillBodyRefusedError",
    "SkillDocument",
    "SkillFormatError",
    "SkillNotFoundError",
    "SkillScope",
    "SkillScopeError",
    "delete_skill",
    "list_skills",
    "load_skill",
    "parse_skill_md",
    "render_skill_md",
    "resolve_skills",
    "set_active",
    "write_skill",
]
