# ML — the theory

Gradient boosting, the conformal guarantee and its proof sketch, Shapley values and their
axioms, and the alternatives we did not choose.

---

## 1. Gradient boosting

Boosting builds an additive model in stages:

$$F_M(x) = \sum_{m=1}^{M} \nu \, h_m(x)$$

where each $h_m$ is a shallow tree and $\nu$ is the learning rate. At stage $m$, $h_m$ is
fitted to the **negative gradient** of the loss with respect to the current prediction —
the direction that most reduces the loss. For squared error that gradient *is* the
residual, which is why boosting is often described as "fit the errors."

**XGBoost** (Chen & Guestrin, 2016) adds a second-order expansion (it uses both gradient
and Hessian), explicit regularisation on leaf weights and tree complexity, and a
sparsity-aware split finder. **Histogram-based** boosting (LightGBM, and sklearn's
`HistGradientBoosting*`) buckets continuous features into ~256 bins before split search,
turning an $O(n \log n)$ sort per split into an $O(n)$ histogram pass.

### Why tabular data resists deep learning

Grinsztajn, Oyallon & Varoquaux (2022), *Why do tree-based models still outperform deep
learning on tabular data?*, give three reasons that are worth being able to recite:

1. **Irregular target functions.** Tabular targets are often piecewise-constant with sharp
   boundaries. Trees model that natively; neural networks have a smoothness bias that
   fights it.
2. **Uninformative features.** Real tables carry junk columns. Trees ignore them by never
   splitting on them; MLPs must learn to suppress them.
3. **Rotational invariance is a liability.** MLPs are approximately invariant to
   rotations of the feature space, but individual tabular columns *mean* something —
   rotating them destroys information that trees exploit directly.

### The ensemble

$$\hat y = \frac{1}{K}\sum_k \hat y_k$$

Averaging $K$ models with error variance $\sigma^2$ and pairwise correlation $\rho$ gives
variance:

$$\rho\sigma^2 + \frac{1-\rho}{K}\sigma^2$$

The benefit is entirely in **decorrelation**: averaging two identical models buys
nothing. Two *differently implemented* boosters — XGBoost's exact/approx split finder and
sklearn's histogram binning — make partially different errors, so $\rho < 1$ and the
average genuinely reduces variance.

For classification, **soft voting** averages predicted probabilities rather than taking a
majority of hard labels, which preserves confidence information.

---

## 2. Conformal prediction, properly

### The guarantee

Let $(X_1,Y_1),\dots,(X_n,Y_n),(X_{n+1},Y_{n+1})$ be **exchangeable**. Define a
nonconformity score $s(x,y)$ — for regression, $|y - \hat f(x)|$. Compute
$s_1,\dots,s_n$ on calibration and let

$$\hat q = \text{the } \lceil (n+1)(1-\alpha) \rceil \text{-th smallest of } s_1,\dots,s_n$$

Then the prediction set $C(X_{n+1}) = \{y : s(X_{n+1},y) \le \hat q\}$ satisfies

$$\mathbb{P}\big(Y_{n+1} \in C(X_{n+1})\big) \ge 1-\alpha$$

**Proof sketch, and it is short enough to give in an interview.** Under exchangeability,
$s_{n+1}$ is equally likely to occupy any rank among the $n+1$ scores. So
$\mathbb{P}(s_{n+1} \le s_{(k)}) \ge k/(n+1)$. Set $k = \lceil (n+1)(1-\alpha)\rceil$ and
the bound follows. No distributional assumption is used anywhere — only the symmetry.

### Why the calibration set has a hard minimum

The quantile rank must be **in range**: $\lceil (n+1)(1-\alpha)\rceil \le n$.

For $\alpha = 0.1$ (90% coverage): $n = 5$ gives $\lceil 6 \times 0.9\rceil = 6 > 5$. No
finite quantile exists — the requested level is unattainable, whatever the data looks
like. The smallest workable $n$ is 9.

A system that does not check this either raises deep inside a library or, worse, returns
something. The correct behaviour is to refuse with an explanation naming the arithmetic.

### Marginal, not conditional

The guarantee is **marginal**: averaged over the whole distribution. It is *not*
conditional coverage — it does not promise 90% for each subgroup. A model that is
systematically worse on one segment can hit 90% overall while covering that segment at
70%.

Conditional coverage is provably impossible to achieve in full generality without
assumptions. **Mondrian / group-conditional conformal** recovers it per declared group by
calibrating separately within each — at the cost of needing enough calibration rows *per
group*.

Know this distinction. "Is your coverage marginal or conditional?" is a real interview
question and the correct answer is "marginal, and here is what that does not promise."

### Variants

| Method | Idea | Trade-off |
|---|---|---|
| **Split (inductive)** | One held-out calibration split | One model fit; loses data to calibration |
| **Full (transductive)** | Refit per candidate label | Statistically efficient; computationally absurd |
| **CV+ / Jackknife+** | Cross-fit residuals | Better data use; $K$ fits |
| **CQR** | Conformalise quantile regression | Adaptive interval width; needs a quantile model |
| **Mondrian** | Calibrate within groups | Group-conditional coverage; needs per-group data |

Aegis uses **split conformal** via MAPIE with `prefit=True`. It is one extra fit, has the
lowest operational complexity, and the guarantee is identical. The honest cost is fixed
interval width — split conformal on absolute residuals gives every prediction the *same*
half-width, so it cannot express "this row is harder than that one". CQR is the upgrade
path if adaptivity is needed.

### Stratification

For classification, the split must be **stratified** on the label. Without it, a rare
class can land entirely outside the calibration split — and then its conformal sets carry
no guarantee at all, silently.

Stratification needs at least 2 members per class. A frame too degenerate to stratify has
to fall back, and the fallback must be **logged**, because it is a silent invalidation of
the guarantee for whichever class is missing.

---

## 3. Shapley values and SHAP

### The axioms

For a value function $v$ over coalitions $S \subseteq N$, the Shapley value of player $i$
is:

$$\phi_i = \sum_{S \subseteq N\setminus\{i\}} \frac{|S|!\,(|N|-|S|-1)!}{|N|!}\big[v(S\cup\{i\}) - v(S)\big]$$

It is the **unique** assignment satisfying four axioms:

1. **Efficiency** — $\sum_i \phi_i = v(N) - v(\emptyset)$. Everything is accounted for.
2. **Symmetry** — players with identical marginal contributions get identical values.
3. **Dummy** — a player who never changes the value gets zero.
4. **Additivity** — values are additive across games.

Uniqueness is the reason to use Shapley rather than any other attribution. It is not "a"
fair division; it is the only one with these properties.

For ML: players are features, $v(S)$ is the model output using only features in $S$, and
efficiency gives the **local accuracy** property $f(x) = \phi_0 + \sum_i \phi_i$.

### TreeSHAP

The exact computation is $O(2^n)$. **TreeSHAP** (Lundberg et al., 2020) computes exact
Shapley values for tree ensembles in $O(TLD^2)$ — trees × leaves × depth² — by pushing all
coalition subsets down the tree simultaneously and tracking their proportions.

This is the practical reason tree models are chosen when explanations are a requirement:
exact, fast attributions with no sampling noise.

### Aggregating across ensemble members

Shapley values are **additive across the game** (axiom 4). For an averaged ensemble
$f = \sum_k w_k f_k$ with $\sum w_k = 1$:

$$\phi_i(f) = \sum_k w_k \phi_i(f_k)$$

So the weighted mean of per-member attributions is **exact** for regression, where the
ensemble output really is the weighted mean of member outputs.

For soft-voting classification it is an **approximation**, because members are averaged in
*probability* space while TreeSHAP explains in *margin* (log-odds) space, and the mapping
between them is nonlinear. The resulting driver *ranking* is faithful; the magnitudes are
not exact. That distinction should be documented rather than glossed.

### Aggregating one-hot columns back to features

A categorical feature becomes $L$ one-hot columns. The user wants one attribution for
`region`, not five for `region_emea`, `region_apac`, …

Efficiency makes this trivially valid: **sum** the encoded columns belonging to a parent
feature.

The correct way to build that mapping is **structurally**, from the fitted preprocessor's
column layout — the one-hot block emits `len(categories_[i])` columns for the *i*-th
categorical, in declared order, then the numeric passthroughs.

Building it by **matching name prefixes** is ambiguous and silently wrong. With a
categorical `plan` and a numeric `plan_age`, the passthrough column `plan_age` starts with
`plan_`, so its whole contribution folds into `plan` and `plan_age` reports 0.0. See
[`30-deep-dive.md`](30-deep-dive.md).

### The binary sign problem

`TreeExplainer.shap_values(X)` returns:

- regression → `(n, n_features)`
- **binary** classification → `(n, n_features)` — a single margin, always **toward class
  1**
- multiclass → `(n, n_features, n_classes)`

So for binary, when the predicted label is class 0, the raw values explain the class that
was **not** predicted. Every sign reads backwards beside the prediction. The fix is to
negate; only genuinely 3-D multiclass values are re-indexed by class.

---

## 4. Imputation, and why silent imputation is dishonest

A caller may omit a feature. Options: refuse the prediction; use a median/mode; or use a
model that handles missingness natively (XGBoost learns a default direction per split).

Median/mode imputation is standard and defensible for one missing feature. It becomes
indefensible when it is **silent and widespread**.

Consider a caller who mistypes every feature name. Every feature is missing, so every one
is imputed. The model returns a fully confident prediction — of the **median training
row** — and nothing in the response says that none of the caller's input was used.

Reporting `imputed_features` and `unknown_features` converts an invisible failure into a
visible one at no cost. It is the same principle as `CostSource.UNPRICED` in the gateway:
the number is fine; the *claim it makes* needs qualifying.

---

## 5. Measuring the model honestly

Three disjoint splits, not two:

| Split | Used for | Why disjoint |
|---|---|---|
| **train** | Fitting the ensemble | — |
| **calibration** | The conformal quantile | Calibrating on training residuals underestimates error — the model has seen those rows |
| **test** | Measuring accuracy and **empirical coverage** | Neither fitted nor calibrated on, so the numbers are observations |

Metrics: $R^2$ for regression, accuracy for classification, and **empirical coverage** —
the fraction of test rows whose true value fell inside the interval or prediction set.

**Empirical coverage is the only coverage number that can disappoint you.** It is
therefore the only one worth reporting as a measurement. A model card that reports the
requested level as achieved is an echo of its own configuration.

And note what a *good* honest system looks like: requesting 0.9 and reporting an achieved
0.762 with a flag saying the request was not met. That is the shape of a trustworthy
number.

---

## 6. Why the synthetic fallback is a design trap

A domain-agnostic ML package needs to be trainable with no domain code — for tests, for
CI, for a fresh checkout. So it ships a **synthesiser** producing a learnable frame from
random numbers.

That is fine. What is not fine is the resolution order:

```
in-process model → persisted artifact → train on the synthesiser
```

The third step turns a testing convenience into a production liar. The synthetic model
fits, calibrates, produces an interval and produces SHAP drivers — all structurally
valid, all containing **zero domain signal**. A caller cannot tell it apart from a real
model.

And if it is *persisted*, a one-off fallback becomes permanent: every later process loads
it from disk as though it were real.

The correct resolution order has **two steps and then a refusal**. The reasoning is
worth stating carefully: the ML signal is best-effort and never gates, so omitting it is
*safe*. Serving a number with no signal is not. Between "no evidence" and "fake
evidence", no evidence wins every time.

Two supporting rules follow:

- **Never auto-persist a synthetic model.** An explicit `save()` is allowed; `train()`
  will not do it for you.
- **Label the provenance on every response**, so even a deliberately saved synthetic model
  announces itself on every prediction.

---

## What you should now be able to explain

- Gradient boosting as stagewise fitting of the loss gradient; XGBoost's second-order
  step and histogram binning
- The three reasons trees beat deep nets on tabular data
- The conformal guarantee and its one-paragraph proof from exchangeability
- Why the calibration set has a hard minimum for a given $\alpha$
- Marginal vs conditional coverage, and what Mondrian conformal buys
- Why split conformal was chosen, and its fixed-width limitation
- Why classification splits must be stratified, and what a failed stratification silently
  invalidates
- The four Shapley axioms and why uniqueness matters
- Why ensemble SHAP is exact for regression and approximate for soft voting
- Why one-hot aggregation must be structural, not prefix-matched
- The binary margin sign problem
- Why silent imputation is a correctness problem, not a convenience
- Why a synthetic-training fallback must be a refusal

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
