"""Core implementation of the cardinality limiter.

The limiter tracks distinct (key, value) pairs seen across structured log
attributes. Once the configured ceiling is reached, the behaviour for new
combinations is determined by the chosen policy:

    - "drop"     : new combinations are rejected (the caller should omit or
                    replace the attributes that produced them).
    - "bucket"   : new combinations are replaced with a sentinel value
                    ("_cardinality_bucket_" by default) so downstream log
    consumers see a stable, low-cardinality placeholder instead of an
    unbounded variety.

Only JSON-serialisable scalar values are tracked. Non-scalar or
non-serialisable values (lists, dicts, sets, custom objects) are passed through
unchanged and are NOT counted toward the cardinality budget. This is a
deliberate design choice: hashable-but-arbitrary object identity is a poor
proxy for "distinct log value", and attempting to serialise every possible
object would couple the limiter to the caller's data model.

NULL handling: a value of None is a perfectly valid, countable attribute value
(e.g. {"user": None} is a distinct pair ("user", None)). It is treated like any
other scalar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


_SENTINEL_DEFAULT = "_cardinality_bucket_"


def _is_trackable(value: Any) -> bool:
    """Return True if *value* is a scalar we can reliably track.

    We track str, int, float, bool, and None. These are the JSON scalar types
    and cover the vast majority of structured-log attribute values. Booleans
    are included because JSON serialises them distinctly from integers.

    Floats: JSON does not distinguish 1 and 1.0 after serialisation, so we use
    the json.dumps() string form as the identity, not the Python repr. This
    keeps limiter semantics consistent with what a downstream JSON log
    consumer would see.
    """
    return value is None or isinstance(value, (str, int, float, bool))


def _identity(value: Any) -> str:
    """Return a stable string identity for a trackable scalar value.

    Using json.dumps() rather than repr() ensures that the identity matches
    what a structured-log pipeline consuming JSON would observe. For example,
    json.dumps(True) is 'true' while repr(True) is 'True'.
    """
    return json.dumps(value, sort_keys=False)


@dataclass
class LimitResult:
    """Outcome of processing a batch of attributes.

    ``attributes`` is the attributes the caller should emit: either the
    originals (if under budget / already seen), a bucketed version (for the
    "bucket" policy when new combinations appear post-ceiling), or an empty
    dict (for the "drop" policy when new combinations appear post-ceiling).

    ``dropped`` is True when at least one new combination was rejected.
    ``bucketed`` is True when at least one new combination was replaced.
    """

    attributes: Dict[str, Any]
    dropped: bool = False
    bucketed: bool = False


class CardinalityLimiter:
    """Track and limit the cardinality of structured-log attribute pairs.

    Parameters
    ----------
    max_combinations:
        Maximum number of distinct (key, value) pairs to track. Must be a
        positive integer.
    policy:
        "drop" or "bucket". See module docstring.
    sentinel:
        Replacement value used when policy is "bucket". Ignored for "drop".

    The limiter is stateful; each instance maintains its own set of seen
    pairs. Call :meth:`reset` to clear state.
    """

    def __init__(
        self,
        max_combinations: int,
        policy: str = "drop",
        sentinel: Any = _SENTINEL_DEFAULT,
    ) -> None:
        if not isinstance(max_combinations, int) or isinstance(
            max_combinations, bool
        ):
            raise TypeError("max_combinations must be an int")
        if max_combinations <= 0:
            raise ValueError("max_combinations must be positive")
        if policy not in ("drop", "bucket"):
            raise ValueError("policy must be 'drop' or 'bucket'")
        self._max = max_combinations
        self._policy = policy
        self._sentinel = sentinel
        # Ordered by insertion; insertion order matters only for determinism
        # of eviction-style behaviour, though we do not evict — we stop growing.
        self._seen: Dict[Tuple[str, str], None] = {}

    @property
    def max_combinations(self) -> int:
        return self._max

    @property
    def policy(self) -> str:
        return self._policy

    @property
    def sentinel(self) -> Any:
        return self._sentinel

    def seen_count(self) -> int:
        """Return the number of distinct pairs currently tracked."""
        return len(self._seen)

    def reset(self) -> None:
        """Clear all tracked pairs."""
        self._seen.clear()

    def process(self, attributes: Dict[str, Any]) -> LimitResult:
        """Process a batch of attributes and return the emit-safe result.

        The returned ``LimitResult.attributes`` is always a new dict; the
        input is never mutated.
        """
        if not isinstance(attributes, dict):
            raise TypeError("attributes must be a dict")

        result: Dict[str, Any] = {}
        dropped = False
        bucketed = False

        for key, value in attributes.items():
            if not _is_trackable(value):
                # Non-scalar values are passed through untouched and do not
                # consume cardinality budget. See module docstring.
                result[key] = value
                continue

            pair = (str(key), _identity(value))
            if pair in self._seen:
                result[key] = value
                continue

            if len(self._seen) < self._max:
                self._seen[pair] = None
                result[key] = value
                continue

            # Over ceiling: apply policy.
            if self._policy == "drop":
                dropped = True
                # Omit this key entirely.
                continue
            else:  # bucket
                bucketed = True
                result[key] = self._sentinel

        return LimitResult(
            attributes=result, dropped=dropped, bucketed=bucketed
        )
