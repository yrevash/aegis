"""Render the pipeline declarations as Markdown — the fourth consumer.

The gap the user named was never prose: ``docs/teaching/`` is a current, accurate
sixteen-module course and rewriting it would buy nothing. What was missing is a
**machine-readable structure** for the flows those modules compose into, and a document
generated from that structure — so the page describing the ingest pipeline cannot
describe five stages while the pipeline runs six.

``python -m aegis.pipelines`` writes the rendering to stdout; the checked-in copy at
``docs/module/PIPELINES.md`` is pinned against it by a test, exactly as the console's
offline topology snapshot is pinned against the compiled graph.
"""

from __future__ import annotations

from aegis.pipelines.spec import CHANNEL_MEANING, PIPELINES, PipelineSpec

__all__ = ["render_markdown"]

def _stage_table(spec: PipelineSpec) -> list[str]:
    """Render one pipeline's stages as a Markdown table.

    Args:
        spec: The pipeline.

    Returns:
        The table's lines.
    """
    lines = [
        "| # | Stage | Owns it | What it does | Emits |",
        "|---|-------|---------|--------------|-------|",
    ]
    for index, stage in enumerate(spec.stages, start=1):
        emits = (
            "<br>".join(
                f"`{emission.channel.value}` `{emission.name}` — {emission.detail}"
                for emission in stage.emits
            )
            or "nothing observable"
        )
        name = f"`{stage.name}`" + (" *(conditional)*" if stage.optional else "")
        lines.append(
            f"| {index} | {name} | `{stage.owner}` | {stage.summary} | {emits} |"
        )
    return lines


def render_markdown() -> str:
    """Render every declared pipeline as one Markdown document.

    Returns:
        The document, ending in a newline. Deterministic: the same declaration always
        renders the same bytes, which is what lets a test pin the checked-in copy.
    """
    out: list[str] = [
        "# The Aegis pipelines",
        "",
        "**Generated from `aegis.pipelines.spec` — do not edit by hand.**",
        "Regenerate with `python -m aegis.pipelines > docs/module/PIPELINES.md`;",
        "`aegis/tests/pipelines/test_pipeline_spec.py` fails if this file and the",
        "declaration disagree.",
        "",
        "Aegis runs **three** pipelines. A module is not a pipeline: the sixteen-module",
        "course in [`../teaching/`](../teaching/README.md) explains the parts, and this",
        "document is the flows they compose into. Each stage below names the module that",
        "owns it and what it puts on a wire or a row, and each of those claims is bound",
        "to the code by `aegis.pipelines.bindings` — a declaration that disagrees with",
        "the runtime raises `PipelineDriftError` rather than quietly misleading a reader.",
        "",
        "The same declaration is served by `GET /pipelines` and read by the console's",
        "pipeline-health page, so the screen, the API and this document cannot drift",
        "apart.",
        "",
        "## Where a stage's output goes",
        "",
        "| Channel | Meaning |",
        "|---------|---------|",
    ]
    out.extend(
        f"| `{channel.value}` | {note} |" for channel, note in CHANNEL_MEANING.items()
    )
    out.append("")
    for spec in PIPELINES:
        out.extend(
            [
                f"## {spec.title} — `{spec.name}`",
                "",
                spec.summary,
                "",
                f"**Entry point:** `{spec.entrypoint}`  ",
                "**Durable record:** "
                + (
                    f"`{spec.durable_record}`"
                    if spec.durable_record
                    else "none — nothing here survives the request"
                ),
                "",
            ]
        )
        out.extend(_stage_table(spec))
        out.append("")
        if spec.limits:
            out.extend(["**What this pipeline does not record**", ""])
            out.extend(f"- {limit}" for limit in spec.limits)
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
