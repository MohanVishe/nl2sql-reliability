"""Load Arcwise-Plat-SQL, the expert-corrected BIRD Mini-Dev subset.

Why this dataset and not BIRD itself: Jin et al. (VLDB 2026, arXiv:2601.08778) audited the
benchmarks everyone cites and found annotation error rates of 52.8% on BIRD Mini-Dev and
62.8% on Spider 2.0-Snow. Re-scoring 16 agents against corrected labels moved results by
-7% to +31% relative and shifted ranks by up to nine places.

That matters more here than it would for an accuracy study. This study measures
disagreement between repeated runs. On a dataset where half the answer key is wrong, a
model that "fails" is often producing correct SQL that disagrees with a broken gold query —
and the variance measured would be variance in how creatively it goes wrong, not variance
in the model. The corrected labels are what make the measurement mean anything.

The data is CC BY-SA 4.0 and is deliberately **not** vendored into this repository: it is
fetched on demand so this repo's own licence stays uncomplicated.
"""

from __future__ import annotations

import json
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

SOURCE_URL = (
    "https://raw.githubusercontent.com/uiuc-kang-lab/text_to_sql_benchmarks/"
    "main/data/arcwise_plat_sql_only_with_diff.json"
)

# Verified by download on 2026-09-16. A change here means the upstream dataset moved and
# every published number would need re-running, so it is asserted rather than assumed.
EXPECTED_ITEMS = 498
EXPECTED_DATABASES = 11

DEFAULT_CACHE = Path("data") / "arcwise_plat_sql.json"

REQUIRED_FIELDS = ("question_id", "question", "evidence", "SQL", "db_id")


@dataclass(frozen=True)
class Question:
    """One benchmark item. `evidence` is BIRD's external-knowledge hint, often empty."""

    question_id: int
    question: str
    evidence: str
    gold_sql: str
    db_id: str

    @classmethod
    def from_raw(cls, raw: dict) -> Question:
        missing = [f for f in REQUIRED_FIELDS if f not in raw]
        if missing:
            raise ValueError(f"item missing fields {missing}: {raw!r}")
        # The source JSON stores ids as strings ("1471"). Left as strings they sort
        # lexicographically -- "10" before "9" -- and, worse, a results file round-tripped
        # through JSON compares unequal to the id it came from, which silently breaks the
        # runner's resume check into re-running work it had already done.
        try:
            question_id = int(raw["question_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"question_id is not numeric: {raw['question_id']!r}") from exc

        return cls(
            question_id=question_id,
            question=raw["question"],
            evidence=raw["evidence"] or "",
            gold_sql=raw["SQL"],
            db_id=raw["db_id"],
        )


def fetch(cache_path: Path = DEFAULT_CACHE, *, force: bool = False) -> Path:
    """Download the dataset to `cache_path` unless it is already there."""
    cache_path = Path(cache_path)
    if cache_path.exists() and not force:
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
        payload = response.read()
    cache_path.write_bytes(payload)
    return cache_path


def load(cache_path: Path = DEFAULT_CACHE, *, verify: bool = True) -> list[Question]:
    """Load the dataset, fetching it first if needed.

    Args:
        verify: assert the expected item and database counts. Leave on — silently running a
            study against a changed dataset is how unreproducible numbers get published.
    """
    path = fetch(cache_path)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = [Question.from_raw(item) for item in raw]

    if verify:
        databases = {q.db_id for q in questions}
        if len(questions) != EXPECTED_ITEMS:
            raise ValueError(
                f"expected {EXPECTED_ITEMS} items, found {len(questions)}. "
                "Upstream dataset changed; re-verify before using any previous results."
            )
        if len(databases) != EXPECTED_DATABASES:
            raise ValueError(f"expected {EXPECTED_DATABASES} databases, found {len(databases)}.")
    return questions


def databases(questions: list[Question]) -> Counter:
    """Question count per database. Only these databases need downloading from BIRD."""
    return Counter(q.db_id for q in questions)


def stratified_subset(questions: list[Question], size: int, *, seed: int = 0) -> list[Question]:
    """Take `size` questions spread across every database, proportional to its share.

    The pipeline is proved on a subset before it is scaled to all 498 — a rigorous result
    on a subset beats an unfinished one on the whole set. Stratifying keeps every database
    represented, so subset results stay comparable to the eventual full run.

    Selection is deterministic for a given seed so a published subset can be reproduced.
    """
    if size >= len(questions):
        return list(questions)
    if size <= 0:
        raise ValueError("size must be positive")

    import random

    by_db: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        by_db[question.db_id].append(question)

    rng = random.Random(seed)
    chosen: list[Question] = []
    # Largest databases first, so rounding losses land where there is most to spare.
    order = sorted(by_db, key=lambda db: (-len(by_db[db]), db))

    for db in order:
        share = len(by_db[db]) / len(questions)
        take = max(1, round(share * size))
        pool = sorted(by_db[db], key=lambda q: q.question_id)
        chosen.extend(rng.sample(pool, min(take, len(pool))))

    counts = Counter(q.db_id for q in chosen)

    # Rounding each database's share independently lands near `size`, not on it. Correct in
    # whichever direction it missed, always taking from or giving to the database that is
    # furthest from its proportional share, so coverage survives the adjustment.
    if len(chosen) > size:
        while len(chosen) > size:
            fullest = max(counts, key=lambda db: (counts[db] / len(by_db[db]), db))
            for i in range(len(chosen) - 1, -1, -1):
                if chosen[i].db_id == fullest:
                    del chosen[i]
                    counts[fullest] -= 1
                    break
    elif len(chosen) < size:
        taken = {q.question_id for q in chosen}
        remaining: dict[str, list[Question]] = {
            db: [q for q in sorted(pool, key=lambda q: q.question_id) if q.question_id not in taken]
            for db, pool in by_db.items()
        }
        while len(chosen) < size:
            eligible = [db for db, pool in remaining.items() if pool]
            if not eligible:
                break
            emptiest = min(eligible, key=lambda db: (counts[db] / len(by_db[db]), db))
            chosen.append(remaining[emptiest].pop(rng.randrange(len(remaining[emptiest]))))
            counts[emptiest] += 1

    chosen.sort(key=lambda q: q.question_id)
    return chosen
