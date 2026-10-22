# mini-log-record-cardinality-limiter

Tracks the number of distinct attribute key-value combinations emitted in structured logs and drops or buckets new combinations beyond a configured cardinality ceiling.

## Usage

```python
from mini_log_record_cardinality_limiter import CardinalityLimiter

limiter = CardinalityLimiter(max_combinations=100, policy="bucket")

# During your logging path:
result = limiter.process({"user_id": "abc", "route": "/home"})
print(result.attributes)  # -> {"user_id": "abc", "route": "/home"}

# Once the ceiling is reached, new combinations are bucketed:
result = limiter.process({"user_id": "xyz", "route": "/other"})
print(result.attributes)  # -> {"user_id": "_cardinality_bucket_", "route": "_cardinality_bucket_"}
print(result.bucketed)   # -> True
```

With `policy="drop"`, over-ceiling pairs are omitted entirely and `result.dropped` is set to `True`.

Exported names: `CardinalityLimiter`, `LimitResult`.

## Why

Structured-log backends (indexers, dashboards) degrade badly when a single field carries thousands of distinct values — high-cardinality attributes blow up index sizes and make queries slow or unusable. This library sits in the logging path and enforces a hard ceiling on the number of distinct `(key, value)` pairs it will let through, either dropping new combinations or replacing them with a stable sentinel string so downstream consumers see bounded cardinality.

The trade-off is precision: once the ceiling is hit, you lose the ability to distinguish between new values. That is the point — you are choosing a bounded, predictable log volume over complete fidelity for a field that is growing without bound.

## Edges to know about

- **Non-scalar values are not tracked.** Lists, dicts, and other non-JSON-scalar values are passed through untouched and do not count toward the cardinality budget. The limiter only tracks `str`, `int`, `float`, `bool`, and `None` — the JSON scalar types. If you pass `{"tags": ["a", "b"]}`, it flows through unchanged. This avoids coupling the limiter to your data model and avoids accidental object-identity-based tracking.

- **`None` is a tracked value.** The pair `("user", None)` is a distinct, countable combination, distinct from `("user", "None")`. This is intentional: `None` is a legitimate structured-log value.

- **`1` and `1.0` are treated as distinct.** Identity is based on the JSON serialisation (`json.dumps`), not Python's `repr` or `==`. `json.dumps(1)` is `"1"` and `json.dumps(1.0)` is `"1.0"`, so they count as two different pairs. This matches what a downstream JSON-consuming log pipeline would observe.

- **`True` and `1` are distinct** for the same reason (`json.dumps(True)` is `"true"`).

- **No eviction.** Once the ceiling is reached, it stays reached. The limiter does not drop old pairs to make room for new ones; that would make log output non-deterministic across process restarts. Call `reset()` if you need to clear state (e.g. at the start of a new request scope).
