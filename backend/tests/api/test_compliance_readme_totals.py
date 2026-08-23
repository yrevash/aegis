"""``docs/compliance/README.md``'s headline counts must be the table's own counts.

The compliance table derives every coverage figure it serves — ``_coverage`` counts the
four states over the real ``ControlEntry`` list, with a comment saying "derived, so it
cannot drift". The prose README in front of it does not: its totals and its "enforced in
every mapped control" sentence are typed in, and nothing re-derived them when a control
changed state.

That is the same failure mode ``web/src/components/landing/standardsSummary.ts`` refuses
by construction, in its own words — *a count typed into a marketing section is a count
nobody re-derives when a control changes state* — and the README is the document a
reviewer is most likely to read instead of the endpoint. A stale headline there is worse
than a stale one anywhere else in the repo, because it is the number that gets quoted.

So the counts get the same treatment as the band: derived here, asserted against the
prose, and the test names the correct line in its failure so the fix is a paste.

Two claims are pinned, and they are different in kind:

* the **four state totals**, which drift by one whenever any control's state changes;
* **which frameworks are enforced in full**, which is the strongest sentence in the
  document and the one a jury will test first. It must name exactly the frameworks whose
  coverage is complete — no more (an overclaim) and no fewer (a claim we have earned and
  buried).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.platform.compliance import build_compliance

README = Path(__file__).resolve().parents[3] / "docs" / "compliance" / "README.md"

#: "**Total: 114 controls — 31 enforced · 56 partial · 22 not implemented · 5 not
#: applicable.**", tolerating the line wrap the prose actually uses.
_TOTALS = re.compile(
    r"\*\*Total:\s*(?P<total>\d+)\s*controls\s*—\s*"
    r"(?P<enforced>\d+)\s*enforced\s*·\s*"
    r"(?P<partial>\d+)\s*partial\s*·\s*"
    r"(?P<not_implemented>\d+)\s*not\s+implemented\s*·\s*"
    r"(?P<not_applicable>\d+)\s*not\s+applicable\.\*\*",
    re.IGNORECASE,
)


def _readme() -> str:
    """The README with newlines collapsed, so a wrapped sentence still matches."""
    assert README.exists(), f"{README} is missing — the compliance pack has no front page"
    return re.sub(r"\s+", " ", README.read_text(encoding="utf-8"))


def _mark(framework_name: str) -> str:
    """The short display mark a document actually writes, from the framework's full name.

    ``Framework.name`` carries the formal title — "NIST AI RMF — AI Risk Management
    Framework" — and no document writes that in a sentence; prose uses the mark before
    the em-dash. Matching on the full name would fail on a README that is correct.
    """
    return framework_name.split("—")[0].strip()


def test_the_readme_headline_totals_are_the_tables_own_totals() -> None:
    """Every state count in the headline is re-derived from the served table."""
    table = build_compliance()
    controls = [control for framework in table.frameworks for control in framework.controls]

    expected = {
        "total": len(controls),
        "enforced": sum(1 for c in controls if c.state == "enforced"),
        "partial": sum(1 for c in controls if c.state == "partial"),
        "not_implemented": sum(1 for c in controls if c.state == "not_implemented"),
        "not_applicable": sum(1 for c in controls if c.state == "not_applicable"),
    }
    # A guard against the assertion passing because the table came back empty.
    assert expected["total"] > 100, "the compliance table did not load"

    match = _TOTALS.search(_readme())
    assert match is not None, (
        "docs/compliance/README.md has no '**Total: N controls — …**' line to check. "
        "It should read: " + _headline(expected)
    )

    found = {key: int(value) for key, value in match.groupdict().items()}
    assert found == expected, (
        "docs/compliance/README.md's headline totals have drifted from the table it "
        f"describes.\n  README says: {found}\n  table says:  {expected}\n"
        "Replace that line with: " + _headline(expected)
    )


def test_the_readme_names_exactly_the_frameworks_enforced_in_full() -> None:
    """The 'enforced in every mapped control' sentence over- and under-claims nothing.

    Under-claiming is a real failure, not a safe one: a framework whose every mapped
    control is enforced and which the front page does not name is a claim earned and
    thrown away — and the landing page *does* name it, so the two documents would
    disagree in public.
    """
    table = build_compliance()
    complete = [
        framework
        for framework in table.frameworks
        if framework.coverage.total > 0
        and framework.coverage.enforced == framework.coverage.total
    ]
    text = _readme()

    for framework in complete:
        assert _mark(framework.name) in text, (
            f"{_mark(framework.name)} is enforced in every one of its "
            f"{framework.coverage.total} mapped controls, and docs/compliance/README.md "
            "never names it. That is a claim we have earned and buried, and the landing "
            "band already prints it — update the headline paragraph."
        )

    # And the inverse: nothing incomplete may be described as enforced in full.
    for framework in table.frameworks:
        if framework in complete:
            continue
        claim = re.search(
            re.escape(_mark(framework.name))
            + r"[^.]{0,80}enforced in (?:full|every mapped)",
            text,
            re.IGNORECASE,
        )
        assert claim is None, (
            f"docs/compliance/README.md claims {_mark(framework.name)} is enforced in "
            f"full, but "
            f"it is {framework.coverage.enforced} of {framework.coverage.total}: "
            f"{claim.group(0) if claim else ''!r}"
        )


def _headline(counts: dict[str, int]) -> str:
    """The exact line the README should carry, so a failure is a paste and not a puzzle."""
    return (
        f"**Total: {counts['total']} controls — {counts['enforced']} enforced · "
        f"{counts['partial']} partial · {counts['not_implemented']} not implemented · "
        f"{counts['not_applicable']} not applicable.**"
    )
