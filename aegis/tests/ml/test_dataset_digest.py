"""The training-data digest: which frame produced this model (OWASP LLM04).

The gap this closes was named precisely: the spine trained from a host-supplied
frame that carried no integrity digest, so nothing recorded which frame produced a
given fitted model. Two spines fitted from different data — one of them poisoned —
were indistinguishable after the fact: same members, same weights, same split
sizes, same card.

**What the mechanism is and is not.** :func:`aegis.ml.provenance.frame_digest` is
provenance and tamper-**evidence**. It does not screen, inspect or refuse a
training frame, and a caller who supplies a poisoned frame still gets a fitted
model. What changes is that the model now names its data, so the poisoning is
attributable and — against a digest recorded while the data was still trusted —
detectable. Prevention is a different control.

**The mutation that breaks each claim** is named on the test, in this file's
convention and the one used by ``aegis/tests/redteam/test_atlas_families.py``. The
load-bearing pair is:

* ``test_refitting_on_changed_data_changes_the_digest`` — delete the
  ``digest=frame_digest(...)`` call in :meth:`TrustworthyModel.train` (or return a
  constant from ``frame_digest``) and it fails. Without it the digest detects
  nothing.
* ``test_refitting_on_the_same_data_reproduces_the_digest`` — the one people
  forget. Hash anything unstable (``id()``, a timestamp, the index, ``repr`` of a
  float) and it fails. Without it the digest is noise, every comparison is a
  mismatch, and a real mismatch means nothing.

Both directions are required. A digest that always changes and a digest that never
changes are equally useless, and only one test each would let either through.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aegis.ml.model import TrustworthyModel
from aegis.ml.provenance import DIGEST_PREFIX, frame_digest
from aegis.ml.spec import ResolvedSpec


def _frame(seed: int = 0, n: int = 400) -> pd.DataFrame:
    """A small, deterministic training frame with one categorical column."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 2))
    return pd.DataFrame(
        {
            "f0": x[:, 0],
            "f1": x[:, 1],
            "region": rng.choice(["na", "emea", "apac"], size=n),
            "y": x @ np.array([2.0, -1.5]) + rng.normal(scale=0.1, size=n),
        }
    )


_SPEC = ResolvedSpec(
    features=["f0", "f1", "region"],
    target="y",
    task="regression",
    categorical_features=["region"],
)


# ─────────────────────────────────────────────────────────────────────────────
# The two directions that make a digest mean anything
# ─────────────────────────────────────────────────────────────────────────────


def test_refitting_on_changed_data_changes_the_digest():
    """One flipped cell in 400 rows moves the fingerprint. THE detection claim.

    Remove the ``frame_digest`` call from ``TrustworthyModel.train`` and both cards
    carry ``None``; make it return a constant and both carry the same string. Either
    way this fails, which is the point — a digest that does not move on poisoned data
    records nothing.
    """
    clean = _frame()
    poisoned = clean.copy()
    poisoned.loc[7, "y"] = float(poisoned.loc[7, "y"]) + 1000.0  # one poisoned label

    honest = TrustworthyModel.train(_SPEC, clean, path=None).model_card()
    tampered = TrustworthyModel.train(_SPEC, poisoned, path=None).model_card()

    assert honest.dataset_digest != tampered.dataset_digest
    # Nothing else on the card moved, which is exactly why the digest was missing:
    # before it, these two models were indistinguishable after the fact.
    assert honest.data_source == tampered.data_source == "provided"
    assert honest.training_size == tampered.training_size
    assert honest.ensemble_members == tampered.ensemble_members


def test_refitting_on_the_same_data_reproduces_the_digest():
    """The forgotten direction, and the one that makes a mismatch *mean* something.

    Two independent fits over two independently-built frames with identical content
    must agree. Hash anything unstable — the index, ``id()``, a timestamp, a float's
    ``repr`` — and this fails; at that point every comparison mismatches and the
    digest can no longer distinguish tampering from noise.
    """
    first = TrustworthyModel.train(_SPEC, _frame(), path=None).model_card()
    second = TrustworthyModel.train(_SPEC, _frame(), path=None).model_card()

    assert first.dataset_digest == second.dataset_digest
    assert first.dataset_digest.startswith(DIGEST_PREFIX)
    assert len(first.dataset_digest) == len(DIGEST_PREFIX) + 64


def test_the_digest_is_a_pinned_constant_across_processes_and_releases():
    """A golden vector — the reproducibility claim, held to a literal.

    Two fits in one interpreter can agree for the wrong reason. This pins the exact
    bytes for a fixed frame covering the awkward cases at once: ``-0.0`` beside
    ``0.0``, a ``NaN``, a ``None`` in a string column and a negative ``int64``. It
    fails on any change to the encoding (which is what ``DIGEST_VERSION`` is for) and
    on any dependence on interpreter state such as hash randomisation.
    """
    frame = pd.DataFrame(
        {
            "n": [1.5, -0.0, float("nan")],
            "s": ["na", "emea", None],
            "i": pd.Series([3, -1, 0], dtype="int64"),
        }
    )
    assert frame_digest(frame) == (
        "sha256:c7beb0901fcd2368a2db4bc4230fa2e8e52b8b27148965739d782af87d76b1cc"
    )
    # -0.0 is canonicalised: it trains identically to 0.0, so it must not look like
    # a different dataset.
    assert frame_digest(frame) == frame_digest(frame.assign(n=[1.5, 0.0, float("nan")]))


def test_a_served_model_can_be_checked_against_the_data_it_claims():
    """What the digest is *for*: rehash the trusted frame and compare.

    This is the whole verification story. The check is after the fact and needs a
    reference someone recorded while the data was trusted — it stops nothing at fit
    time, and the test asserts that plainly by fitting on the poisoned frame
    successfully and only *then* catching it.
    """
    trusted = _frame()
    poisoned = trusted.copy()
    poisoned.loc[3, "f0"] = 999.0

    served = TrustworthyModel.train(_SPEC, poisoned, path=None)  # nothing refuses it
    reference = frame_digest(trusted, columns=[*_SPEC.features, _SPEC.target])

    assert served.model_card().dataset_digest != reference, (
        "the served model was not fitted on the frame it is being credited with"
    )
    assert frame_digest(trusted, columns=[*_SPEC.features, _SPEC.target]) == reference


# ─────────────────────────────────────────────────────────────────────────────
# The determinism contract, asserted rather than asserted-in-a-docstring
# ─────────────────────────────────────────────────────────────────────────────


def test_the_digest_ignores_column_order_and_the_index():
    """Invariant to exactly what the fit is invariant to, and nothing more.

    The spine selects columns by name and splits rows positionally, so neither the
    column order nor the index label can reach the estimator. A digest that moved on
    them would fire on a ``reset_index()`` and be switched off within a week.
    """
    frame = _frame()
    shuffled_columns = frame[["y", "region", "f1", "f0"]]
    reindexed = frame.set_index(pd.RangeIndex(1000, 1000 + len(frame)))

    assert frame_digest(frame) == frame_digest(shuffled_columns)
    assert frame_digest(frame) == frame_digest(reindexed)


def test_the_digest_moves_when_rows_are_reordered():
    """The deliberate *non*-invariance, and it is not an oversight.

    ``train_test_split`` partitions positionally, so a row-reordered frame produces a
    genuinely different train/calibration/test partition and a genuinely different
    model. A row-order-insensitive digest would claim those two models came from the
    same data.
    """
    frame = _frame()
    reversed_rows = frame.iloc[::-1].reset_index(drop=True)

    assert frame_digest(frame) != frame_digest(reversed_rows)


def test_the_digest_separates_a_null_from_a_zero_and_a_dtype_from_its_values():
    """Two collisions a naive stringify-and-hash walks straight into.

    ``NaN`` filled to ``0.0`` for hashing collides with a real ``0.0``; ``int64``
    values stringify identically to their ``float64`` twins. The null mask is hashed
    beside the values and the dtype beside the column name, so neither collides.
    """
    zero = pd.DataFrame({"a": [0.0, 1.0, 2.0]})
    null = pd.DataFrame({"a": [np.nan, 1.0, 2.0]})
    assert frame_digest(zero) != frame_digest(null)

    ints = pd.DataFrame({"a": pd.Series([1, 2, 3], dtype="int64")})
    floats = pd.DataFrame({"a": pd.Series([1.0, 2.0, 3.0], dtype="float64")})
    assert frame_digest(ints) != frame_digest(floats)

    # Strings that would run together without a length prefix.
    assert frame_digest(pd.DataFrame({"a": ["ab", "c"]})) != frame_digest(
        pd.DataFrame({"a": ["a", "bc"]})
    )


def test_a_renamed_or_dropped_column_changes_the_digest():
    """Schema drift is data drift; a fingerprint blind to it names the wrong thing."""
    frame = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    assert frame_digest(frame) != frame_digest(frame.rename(columns={"a": "z"}))
    assert frame_digest(frame) != frame_digest(frame[["a"]])


def test_digesting_a_column_the_frame_does_not_have_is_an_error():
    """Fails loudly. A silently-skipped column means the digest covers less than it says."""
    with pytest.raises(KeyError):
        frame_digest(pd.DataFrame({"a": [1.0]}), columns=["a", "missing"])


def test_only_the_columns_the_model_consumes_are_fingerprinted():
    """An unrelated extra column must not change the identity of an identical model.

    The digest is scoped to ``features + target`` because that is what the fit reads.
    An audit note column added beside the data produces the same model, and claiming
    it produced different data would be as wrong as missing a poisoned label.
    """
    frame = _frame()
    with_extra = frame.assign(ingested_at="2026-08-23T00:00:00Z")

    base = TrustworthyModel.train(_SPEC, frame, path=None).model_card()
    extra = TrustworthyModel.train(_SPEC, with_extra, path=None).model_card()
    assert base.dataset_digest == extra.dataset_digest


# ─────────────────────────────────────────────────────────────────────────────
# The digest reaches the artifact and the card, not just the fit
# ─────────────────────────────────────────────────────────────────────────────


def test_the_digest_survives_the_persistence_roundtrip(tmp_path):
    """A digest that is lost on save is a digest that is absent where it is needed.

    The artifact is what every later process loads and what the ``/ml/model-card``
    route serves; provenance that lived only in the training process would answer
    nobody.
    """
    model = TrustworthyModel.train(_SPEC, _frame(), path=None)
    artifact = tmp_path / "spine.joblib"
    model.save(artifact)

    reloaded = TrustworthyModel.load(artifact)
    assert reloaded.model_card().dataset_digest == model.model_card().dataset_digest
    assert reloaded.model_card().model_dump() == model.model_card().model_dump()


def test_a_synthetic_fallback_is_still_fingerprinted():
    """Provenance applies to the noise model too, and the two labels are orthogonal.

    ``data_source`` says how the frame arrived; the digest says which frame it was. A
    synthetic model that carried no digest would be the one artifact you could not
    later prove was synthetic from its data.
    """
    spec = ResolvedSpec(features=["x0", "x1"], target="out", task="regression")
    card = TrustworthyModel.train(spec, frame=None, path=None).model_card()
    assert card.data_source == "synthetic"
    assert card.dataset_digest is not None
    assert card.dataset_digest.startswith(DIGEST_PREFIX)
