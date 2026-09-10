# Request-aware trend capabilities and refusal labels

This layer prevents recognized requests from being silently executed with a
different window. It supplements, rather than replaces, strict tool argument and
plant authorization checks. The change does not implement longer history or
hourly aggregation.

## Before and during execution

`capabilities.py` recognizes explicit Chinese relative-duration trend/statistics
requests such as past 2 hours, past 120 minutes, past 30 days, and hourly grouping.
The current trend tool supports a whole-window summary for the latest 1–24 integer
hours, at most 1000 samples, not time buckets. Recognized unsupported windows or
grouping remove `tag_trend` from offered tools. A malicious or mistaken model call
is also checked before reaching the data-query tool. Supported explicit windows
must match the submitted hours; 2 hours cannot silently become the default 1 hour.

Other tools remain available, so explanations and supported parts of mixed
requests need not be refused wholesale. Each constraint is exposed as capability
evidence, visibly distinct from actual tool measurements or document evidence.
The audit `tool_names` records attempted calls, including blocked ones; it must not
be interpreted as proof every attempted query executed.

## Boundaries

This is a conservative grammar, not full natural-language parsing. It does not
claim comprehensive understanding of absolute dates, all Chinese colloquialisms,
negation, historical comparisons or multilingual questions. Multiple detected
windows with any unsupported range disable trend for that question instead of
guessing which subquestion to drop. An unrecognized expression is not evidence
the system supports it. Existing typed hours limits remain enforced regardless.

No capability text authorizes database writes, device control or access to other
plants. The final authorization and knowledge-version checks remain in place.

## Refusal status

The prompt distinguishes direct action refusals (`no_answer`) from grounded
explanations (`answered`). A narrow explicit-refusal normalizer also aligns some
Chinese refusal openings, such as inability to bypass permissions, with
`no_answer`, retaining their original explanation and citations. The audit records
the normalized status. It does not turn all negative sentences into refusals;
for example, inability to infer a fault from Bad remains a valid explanation.
This is not a general semantic classifier or a substitute for model evaluation.

## Regression evidence

Tests cover supported/unsupported windows, decimal and Chinese duration forms,
grouping, mixed requests, non-trend requests, malicious calls to a hidden tool,
wrong hours despite a valid argument schema, refusal labels and audit agreement.
Browser checks label capability evidence separately. The frozen v1 corpus is not
changed; repeated real-model runs are explicitly regression, never fresh holdout
accuracy. The benchmark plan hashes this capability module too.
