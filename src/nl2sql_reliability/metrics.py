"""Capability and reliability estimators.

Run a model on the same question n times and count how many attempts were correct. Two
different questions can be asked of that count:

    pass@k  — would at least one of k attempts be correct?   (capability)
    pass^k  — would all k attempts be correct?               (reliability)

Leaderboards report something like the first. Anything running unattended depends on the
second. A model can score 0.9 on one and 0.3 on the other, and that distance is what this
study exists to measure.

Both are computed as **unbiased estimators over n drawn samples**, not as functions of a
point estimate. The naive plug-in forms substitute the observed rate p = c/n into a
with-replacement formula:

    pass@k ≈ 1 - (1 - p)^k
    pass^k ≈ p^k

Both are biased, in opposite directions, and the reason is the same for each. Drawing k of
n actual samples happens *without* replacement, which makes extreme runs less likely than
independent draws would: once a failure is drawn, fewer failures remain to draw again. So
all-k-fail is rarer than (1-p)^k, making true pass@k **higher** than the plug-in suggests,
and all-k-succeed is rarer than p^k, making true pass^k **lower**. The plug-in therefore
understates capability and overstates reliability — flattering in exactly the direction
this study is trying to measure.

The unbiased forms below instead ask the question directly: drawing k of the n actual
samples without replacement, what is the probability that at least one (or all) are
correct? Binomial coefficients answer that exactly, with no distributional assumption.

References:
    pass@k — Chen et al., "Evaluating Large Language Models Trained on Code" (2021),
             arXiv:2107.03374.
    pass^k — Yao et al., "τ-bench" (2024), arXiv:2406.12045, which introduced all-of-k
             success as the metric that exposes how much apparent capability is luck.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import comb, fsum


@dataclass(frozen=True)
class Outcome:
    """Results of running one question n times."""

    question_id: int
    attempts: int
    correct: int

    def __post_init__(self) -> None:
        if self.attempts <= 0:
            raise ValueError(f"question {self.question_id}: attempts must be positive")
        if not 0 <= self.correct <= self.attempts:
            raise ValueError(
                f"question {self.question_id}: correct={self.correct} outside 0..{self.attempts}"
            )

    @classmethod
    def from_flags(cls, question_id: int, flags: Sequence[bool]) -> Outcome:
        return cls(question_id, len(flags), sum(1 for f in flags if f))


def pass_at_k(attempts: int, correct: int, k: int) -> float:
    """Probability that at least one of k drawn attempts is correct.

    The complement of drawing k failures in a row:
        1 - C(n-c, k) / C(n, k)
    """
    _validate(attempts, correct, k)
    failures = attempts - correct
    if failures < k:
        return 1.0
    return 1.0 - comb(failures, k) / comb(attempts, k)


def pass_hat_k(attempts: int, correct: int, k: int) -> float:
    """Probability that all k drawn attempts are correct.

        C(c, k) / C(n, k)

    Zero unless at least k of the n attempts succeeded — which is the point. One failure in
    n is enough to make a question unreliable at k, however high its pass@k looks.
    """
    _validate(attempts, correct, k)
    if correct < k:
        return 0.0
    return comb(correct, k) / comb(attempts, k)


def _validate(attempts: int, correct: int, k: int) -> None:
    if k <= 0:
        raise ValueError("k must be positive")
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    if k > attempts:
        raise ValueError(
            f"k={k} exceeds attempts={attempts}: cannot estimate k-sample behaviour from "
            "fewer than k samples"
        )
    if not 0 <= correct <= attempts:
        raise ValueError(f"correct={correct} outside 0..{attempts}")


@dataclass(frozen=True)
class SuiteResult:
    """Suite-level estimates at one value of k."""

    k: int
    questions: int
    pass_at_k: float
    pass_hat_k: float

    @property
    def reliability_gap(self) -> float:
        """How much of the apparent capability does not survive repetition.

        This is the headline number: the share of questions a model can solve but does not
        solve dependably.
        """
        return self.pass_at_k - self.pass_hat_k


def summarize(outcomes: Sequence[Outcome], k: int) -> SuiteResult:
    """Average both estimators across questions.

    Every question carries equal weight regardless of how many times it was run, so a
    question that got extra attempts cannot quietly dominate the suite score.
    """
    if not outcomes:
        raise ValueError("no outcomes to summarize")
    at_k = fsum(pass_at_k(o.attempts, o.correct, k) for o in outcomes) / len(outcomes)
    hat_k = fsum(pass_hat_k(o.attempts, o.correct, k) for o in outcomes) / len(outcomes)
    return SuiteResult(k=k, questions=len(outcomes), pass_at_k=at_k, pass_hat_k=hat_k)


def sweep(outcomes: Sequence[Outcome], max_k: int | None = None) -> list[SuiteResult]:
    """Summaries for every k from 1 up to the smallest attempt count.

    The gap widens with k; showing the curve rather than a single k is what makes the
    finding legible instead of a number the reader has to take on trust.
    """
    if not outcomes:
        raise ValueError("no outcomes to sweep")
    ceiling = min(o.attempts for o in outcomes)
    if max_k is not None:
        ceiling = min(ceiling, max_k)
    return [summarize(outcomes, k) for k in range(1, ceiling + 1)]
