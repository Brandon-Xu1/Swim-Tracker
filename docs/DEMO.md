# Two-minute demo

The files in `docs/demo` contain fictional swimmers and a synthetic January–April 2025
season. Regenerate them with `python -m scripts.make_demo`. They are demonstration
fixtures for this app's CL2 parser, not official meet exports.

1. Create an account and team; save the recovery code privately.
2. Upload all four synthetic season files on Meet data. Show the accepted/excluded/error
   counts and import each file. Rename a copy and import it to demonstrate deduplication.
3. On Swimmers, select Alex Example and 100-yard Free. Explain the history, best-time
   table and progress chart. Save a `1:04.00` goal and show the remaining gap.
4. Search for Alex Example; change pages or revisit Search to show persistent results.
5. On Account, create a viewer invitation. In a separate browser session, join as a
   second account and demonstrate that read access works while write controls are absent.
6. If AI is configured, interpret a question, correct a filter and explicitly run it.
   Try an unsupported request such as “Who improved most?” and show the limitation.

Never include recovery codes, invitation codes or real API keys in a recording.

## User feedback and evidence

Recruit a few willing coaches or swimmers yourself. No users have been contacted by
this implementation and no adoption or time-saving metrics are assumed.

Give each tester these tasks without explaining the controls first:

- Import the synthetic season and identify any rejected records.
- Find Alex Example's fastest uploaded 100-yard freestyle.
- Determine how much Alex improved between January and April.
- Set a goal and explain what “best time in uploaded meets” means.
- Join as a viewer and locate the team's results.

Record completion time, success/failure, confusion and suggestions in `feedback.csv`.
Avoid collecting personal swimmer information. Use observations to choose the next
iteration; report participant counts and actual measurements honestly on your resume.

## Resume wording

Current defensible example:

> Built a Python swim analytics app with team-scoped accounts, transactional meet imports,
> swimmer progress tracking and structured AI search; benchmarked 100,000 synthetic results
> and tested rollback, concurrent imports and authorization across SQLite/Postgres CI suites.

Only add real user counts or live AI accuracy after measuring them. Be prepared to explain
profile identity matching, duplicate detection, course separation and the benchmark limits.
