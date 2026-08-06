"""Generator tests — output validates against the schema; LLM is mocked."""

from __future__ import annotations

from app.adapter.generator import (
    GeneratorConfig,
    assess_quality,
    generate_synthetic,
)
from app.adapter.schema import Category, Priority, SyntheticDataset


async def test_output_validates_against_schema(fake_complete):
    cfg = GeneratorConfig(num_requests=25, num_documents=6, seed=7)
    dataset = await generate_synthetic(cfg, complete=fake_complete)

    # A SyntheticDataset only constructs if every nested record is schema-valid.
    assert isinstance(dataset, SyntheticDataset)
    assert len(dataset.requests) == 25
    assert len(dataset.customers) == cfg.num_customers
    assert len(dataset.agents) == cfg.num_agents
    assert len(dataset.documents) == 6
    # Re-validating the serialised form is a second, explicit schema check.
    SyntheticDataset.model_validate(dataset.model_dump())


async def test_llm_content_is_used(fake_complete):
    cfg = GeneratorConfig(num_requests=20, seed=1)
    dataset = await generate_synthetic(cfg, complete=fake_complete)

    assert fake_complete.calls, "the generator should have called the gateway"
    # At least some requests carry the fabricated LLM titles.
    assert any(r.title.startswith("LLM ticket") for r in dataset.requests)
    assert any(d.title.startswith("LLM doc") for d in dataset.documents)


async def test_deterministic_seed_reproduces_structure():
    cfg = GeneratorConfig(num_requests=15, seed=42, use_llm=False)
    first = await generate_synthetic(cfg)
    second = await generate_synthetic(cfg)
    assert first.model_dump() == second.model_dump()


async def test_runs_without_any_llm():
    # use_llm=False and no injected complete → pure templated, still schema-valid.
    cfg = GeneratorConfig(num_requests=10, seed=3, use_llm=False)
    dataset = await generate_synthetic(cfg)
    assert dataset.metadata.llm_used is False
    assert all(r.title for r in dataset.requests)


async def test_metadata_label_count_matches():
    cfg = GeneratorConfig(num_requests=30, seed=5, use_llm=False)
    dataset = await generate_synthetic(cfg)
    assert dataset.metadata.num_labelled == len(dataset.labelled_requests())
    assert 0 < dataset.metadata.num_labelled <= 30


async def test_target_signal_is_predictable():
    # Higher priority should resolve faster on average → the signal is learnable.
    cfg = GeneratorConfig(num_requests=400, seed=11, use_llm=False, noise_scale=2.0)
    dataset = await generate_synthetic(cfg)

    def mean_hours(priority: Priority) -> float:
        vals = [
            r.resolution_hours
            for r in dataset.labelled_requests()
            if r.priority is priority and r.resolution_hours is not None
        ]
        return sum(vals) / len(vals)

    assert mean_hours(Priority.URGENT) < mean_hours(Priority.LOW)


async def test_quality_gate_passes_on_generated_dataset():
    cfg = GeneratorConfig(num_requests=40, seed=13, use_llm=False)
    dataset = await generate_synthetic(cfg)
    report = assess_quality(dataset)

    assert report.ok
    assert report.referential_integrity
    assert report.category_coverage  # every category is represented
    assert report.temporal_consistency
    assert report.pii_free
    assert report.num_labelled > 0


async def test_category_coverage_even_for_small_n():
    # Fewer requests than categories still covers as many distinct classes as it can,
    # and the coverage guarantee fills every category once N ≥ number of categories.
    cfg = GeneratorConfig(num_requests=len(list(Category)), seed=2, use_llm=False)
    dataset = await generate_synthetic(cfg)
    present = {r.category for r in dataset.requests}
    assert present == set(Category)


async def test_quality_gate_detects_referential_break():
    cfg = GeneratorConfig(num_requests=10, seed=1, use_llm=False)
    dataset = await generate_synthetic(cfg)
    dataset.requests[0].customer_id = "cust-does-not-exist"
    report = assess_quality(dataset)
    assert report.referential_integrity is False
    assert report.ok is False
