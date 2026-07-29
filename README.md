# Swim Tracker

Swim Tracker is a Streamlit app for importing Hy-Tek/Team Manager `.cl2` meet
files and searching completed individual swim results. It supports ordinary
filter-based search without any external service. An OpenAI API key optionally
enables natural-language search.

## Requirements

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

For a hosted deployment, add `OPENAI_API_KEY` through that platform's secret
or environment-variable settings. Do not place it in source code, a Dockerfile,
or a committed `.env` file.

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

The suite checks fixed-width parsing, correct 1,650-yard and long-time parsing,
idempotent database imports, parameterized searches, and Streamlit startup.
