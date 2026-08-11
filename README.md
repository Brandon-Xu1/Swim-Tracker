# Swim Tracker

[![Tests](https://github.com/Brandon-Xu1/Swim-Tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/Brandon-Xu1/Swim-Tracker/actions/workflows/tests.yml)

Swim Tracker is a Streamlit app for importing Hy-Tek/Team Manager `.cl2` meet
files and searching completed individual swim results. It supports ordinary
filter-based search and optional natural-language search.

## Features

- **Filter search** by swimmer name, age/gender group, event, course
  (SCY, SCM, or LCM), and meet-date range, sorted by swimmer or fastest time,
  with CSV download of the matching results.
- **Ask AI** turns a natural-language question, such as *"fastest 100 free
  times for girls 11-12 in December 2024"*, into the same validated filters —
  including course and date ranges — without ever executing model output as
  SQL.
- **Meet data management**: import any number of `.cl2` files, re-import a
  file to replace its earlier rows without duplicates, and remove any imported
  meet again. Removed data stays removed across restarts; the bundled sample
  meet is only seeded into a brand-new database once.
- **Course-aware event labels**: events from meter meets (course `L` or `S`)
  are labeled in meters, and yard meets in yards.
- **Team accounts**: anyone can register a team (sidebar) and import meets
  that are visible only to that team's signed-in sessions, alongside the
  shared public demo data. Passwords are stored as salted scrypt hashes;
  originals of imported files are stored and downloadable. The public demo
  data itself is managed by the deployment admin.

## Live app

[Launch Swim Tracker](https://swim-tracker.streamlit.app/)

The app is deployed on Streamlit Community Cloud. Use **Filters** to search the
bundled meet results, or use **Ask AI** to turn a natural-language question into
validated search filters.

## Local Requirements

- Python 3.12
- A virtual environment
- An OpenAI API key only if you want to use **Ask AI**

## Install and run

From this repository:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open <http://localhost:8501> if your browser does not open automatically.

The first launch upgrades the legacy database from the bundled meet file.
Later Streamlit reruns only read the database; they do not drop or duplicate
results. Use **Meet data** to import another `.cl2` file.

## Persistent storage with Neon Postgres

By default the app stores data in a local SQLite file, which is perfect for
development and tests. Streamlit Community Cloud's filesystem is ephemeral,
though: every redeploy resets local files, so meets uploaded to the live app
would disappear. To make uploads persist, point the app at a free hosted
Postgres database:

1. Create a free project at [neon.tech](https://neon.tech) (no credit card
   required; Neon auto-suspends when idle and wakes on the next connection,
   so an idle demo app never breaks).
2. Copy the project's connection string, which looks like
   `postgresql://user:password@host/dbname?sslmode=require`.
3. Add it to the deployment's **App settings → Secrets** (or a local
   `.streamlit/secrets.toml`):

   ```toml
   DATABASE_URL = "postgresql://user:password@host/dbname?sslmode=require"
   ```

On first launch against an empty database the app creates the schema and
seeds the bundled sample meet, exactly as it does locally. Without
`DATABASE_URL` the app keeps using SQLite, so nothing changes for local
development. The test suite runs against SQLite by default; set
`SWIMTRACKER_TEST_DATABASE_URL` to a Postgres URL to run the database tests
against Postgres as well.

## Protect meet data on public deployments

Everyone who visits the deployed app shares one database. Set an
`ADMIN_PASSWORD` secret (same TOML format as above) to require a password
before anyone can import, remove, or reload meet data; searching stays open
to everyone. Password comparison uses `hmac.compare_digest`, and the
password itself is read only server-side. When `ADMIN_PASSWORD` is not
configured — for example during local development — write access stays
open and the app behaves as before.

## Configure the OpenAI API key safely

Filter search works without a key. To enable **Ask AI**, create a new key on
the [OpenAI API keys page](https://platform.openai.com/api-keys), then use one
of these server-side configuration methods.

### Option 1: Streamlit secrets for local development

```bash
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml`:

```toml
OPENAI_API_KEY = "your-new-key"
OPENAI_MODEL = "gpt-5.6-luna"
```

The real `secrets.toml` is listed in `.gitignore`; the example file is safe to
commit because it contains no real credential.

### Option 2: An environment variable

Set the key in the same terminal before starting the app:

```bash
export OPENAI_API_KEY="your-new-key"
export OPENAI_MODEL="gpt-5.6-luna"
streamlit run streamlit_app.py
```

The live Streamlit deployment stores `OPENAI_API_KEY` and `OPENAI_MODEL` under
**App settings → Secrets** using the same TOML format shown above. Do not place
the key in source code, a Dockerfile, or a committed `.env` file.

### Why the key stays hidden

- Git ignores `.streamlit/secrets.toml` and `.env`, so new secrets are not
  included in commits.
- The app reads the key only inside the Python server process.
- The browser receives rendered Streamlit elements, not the key.
- The OpenAI client sends the credential directly from the server to OpenAI.
- The app never prints, logs, or displays the key.

This protects the new key from normal source-control and browser exposure. It
cannot protect a key that was committed previously. This repository's old keys
must be revoked because they remain in Git history even after being removed from
the current files. Key rotation makes those historical values unusable.

OpenAI recommends keeping API keys out of code and public repositories and
providing them through environment variables or a secret manager:
[OpenAI production best practices](https://developers.openai.com/api/docs/guides/production-best-practices#api-keys).

## Keep the OpenAI bill bounded

Three layers keep a public deployment from running up API costs:

- **Per-visitor rate limit**: each session may ask 5 AI questions per minute
  and 30 per hour. Filter search is never limited.
- **Response caching**: identical questions within 24 hours are answered
  from an in-memory cache without an API call, and cache hits do not
  consume the rate limit.
- **Hard spend cap**: set a monthly budget limit on the
  [OpenAI usage limits page](https://platform.openai.com/settings/organization/limits)
  so that even in the worst case spending stops at a number you chose.
  This is configured on the OpenAI dashboard, not in this repository.

## How AI search is kept safe

The original app asked the model to generate arbitrary SQL and executed the
result. The current app asks the Responses API for validated structured search
filters. Those filters are applied to fixed, parameterized SQL queries. Model
output is never executed as SQL.

The default model is `gpt-5.6-luna`, chosen for an efficient, high-volume
lookup task. Change `OPENAI_MODEL` if your OpenAI project uses a different
supported model. The integration follows OpenAI's
[Structured Outputs guidance](https://developers.openai.com/api/docs/guides/structured-outputs).

## Tests

Run the test suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The suite checks fixed-width parsing (including meter-course event labels and
1,650-yard and long-time parsing), idempotent database imports, parameterized
searches with course and date-range filters, per-meet deletion, the AI layer's
happy path and its error paths (unparseable model output, hallucinated age
groups, invalid dates), rate limiting and response caching, the admin gate,
password hashing and team registration, per-team data isolation, stored
original files, and Streamlit startup including seed-once behavior after all
meets are deleted. The database tests additionally run against real Postgres
in CI and wherever `SWIMTRACKER_TEST_DATABASE_URL` is set.
