"""Aegis settings — the catalogue every per-tenant control is an entry in.

**This is the mechanism behind "0 code change from the dashboard".** A control in phases
6, 7 and 10 is a :class:`~aegis.settings.spec.SettingSpec` plus a row in ``settings``,
never a bespoke screen and never a new table — so adding one is data, and the UI, the
permissions and the resolution all follow from the same declaration.

* :mod:`aegis.settings.spec` — the catalogue: what exists, who may read and write it,
  and **how a tenant's value combines with the platform's**. A generalisation of
  :data:`aegis.agent.harness._KNOB_SPECS`, inheriting its bijection test so a setting
  cannot be added without a UI control appearing.
* :mod:`aegis.settings.models` — the one ``settings`` table, scoped platform / tenant /
  user.
* :mod:`aegis.settings.agent` — the half that makes an agent control *bind*:
  :func:`~aegis.settings.agent.resolve_agent_config` folds a tenant's tighten-only
  floors onto the ``AgentConfig`` a run obeys, **per run**, because the config is built
  once and synchronously while resolution is per tenant and async.
* :mod:`aegis.settings.resolver` — :func:`~aegis.settings.resolver.resolve`, which
  returns ``(value, source)``, and :func:`~aegis.settings.resolver.write_setting`, which
  refuses with a reason rather than storing something that will never take effect.

The load-bearing property, stated once: for a ``tighten_only`` key the resolver folds
:func:`~aegis.settings.spec.strictest` over the scope chain, and the platform layer is
always in that chain — so **no combination of tenant and user writes can produce a value
weaker than the platform default**. Phase 7's forbidden controls are enforced by that
arithmetic rather than by a rule someone has to remember.

Requires the ``aegis[data]`` and ``aegis[governance]`` extras.
"""

from __future__ import annotations

from aegis.settings.agent import resolve_agent_config, strictest_agent_config
from aegis.settings.models import SETTINGS_TABLE, Setting, SettingScope
from aegis.settings.resolver import (
    SettingError,
    SettingNotReadableError,
    SettingNotWritableError,
    SettingValueError,
    SettingWeakerThanFloorError,
    resolve,
    resolve_all,
    write_setting,
)
from aegis.settings.spec import (
    SETTING_SPECS,
    MergeRule,
    SettingSpec,
    Strictness,
    UnknownSettingError,
    setting_controls,
    setting_keys,
    spec_for,
    strictest,
)

__all__ = [
    "SETTINGS_TABLE",
    "SETTING_SPECS",
    "MergeRule",
    "Setting",
    "SettingError",
    "SettingNotReadableError",
    "SettingNotWritableError",
    "SettingScope",
    "SettingSpec",
    "SettingValueError",
    "SettingWeakerThanFloorError",
    "Strictness",
    "UnknownSettingError",
    "resolve",
    "resolve_agent_config",
    "resolve_all",
    "setting_controls",
    "setting_keys",
    "spec_for",
    "strictest",
    "strictest_agent_config",
    "write_setting",
]
