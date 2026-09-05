# quiz

Reusable single-answer multiple-choice quiz engine for Discord Adapter.

The repository contains no topic-specific question data. Question packs are
separate Python packages discovered through the `quiz.question_sets` entry-point
group. Discord Adapter loads this package through `discord_adapter.addons`.

## Runtime contract

Enable the addon in Discord Adapter:

```env
DISCORD_ADDONS=quiz
DISCORD_ADDON_STRICT=true
QUIZ_QUESTION_SET_STRICT=true
```

GTNH and TFG Modern are the built-in default question sets. When their data
packages are installed, both load automatically without setting
`QUIZ_QUESTION_SETS`.

`QUIZ_QUESTION_SETS` is only an optional override. Set it to a comma-separated
subset such as `gtnh` or `tfg`, or to `*` to load every installed question-set
entry point.

A data package registers itself like this:

```toml
[project.entry-points."quiz.question_sets"]
gtnh = "gtnh_quiz_data:load_manifest"
```

Schema version 1 remains supported for ordinary weighted quizzes. Schema version
2 can additionally define an ordered `tiers` list and a `tier` on calibrated
questions. Tier-aware quizzes maintain a Bayesian probability distribution over
the declared progression tiers. After each answer, the engine updates that
distribution and chooses the next unused question nearest the posterior median,
so strong answers move the probe upward while misses move it downward. The final
result includes the weighted score, estimated tier, and a likely tier range.

The engine validates question IDs, choice IDs/text, exactly one correct choice,
sample size, Discord alias names, tier IDs, and tier coverage before registering
commands. Choice text may be up to 500 characters. Questions and full answer text
render in an embed; the interaction buttons use short `A` through `E` selector
labels so Discord's button-label limit does not constrain the knowledge being
tested.

`QUIZ_QUESTION_SET_STRICT` defaults to `true`. If a default or explicitly selected
question pack is missing or invalid, addon setup fails instead of silently
starting with a partial catalog. Set it to `false` only when partial availability
is intentional.

## Discord commands

- `/quiz list` lists loaded question sets.
- `/quiz start <quiz_id>` starts a quiz.
- A question set may declare legacy top-level command aliases. This is intended
  for migrations such as `/gtnh_intelligence_test`.

Only the user who started an attempt can answer it. Each answer is accepted once;
stale/replayed button interactions are rejected by the session state machine.
Starting a new attempt invalidates prior buttons for that user/channel. Completed,
timed-out, replaced, and idle sessions are removed from memory. Scores are
normalized to 100 using the selected questions' weights.

The current session store is intentionally in-process and is suitable for the
single Discord Adapter replica used by the production deployment. A multi-replica
deployment should provide a shared session-store implementation before scaling
horizontally.
