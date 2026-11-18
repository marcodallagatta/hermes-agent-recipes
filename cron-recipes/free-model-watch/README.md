# Free model catalog and benchmark watch

An opt-in Hermes cron recipe that watches selected public model catalogs and compares their free routes with an Artificial Analysis Intelligence Index gate you choose. It reports catalog/score changes and can ask Hermes for a brief independent-source check when a newly listed route has no comparable score.

**It does not call or benchmark the models itself.** Scores are third-party benchmark data, not a guarantee of performance, availability, or price stability.

## Contents

- `scripts/free_model_watch.py` — bounded pre-run collector; standard library only.
- `tests/test_free_model_watch.py` — synthetic, offline tests.
- `config.example.json` — placeholder configuration; replace all placeholder model names.
- `prompt.md` — self-contained agent instructions.
- `.env.example` — variable name only, never a key.

## Requirements

- Hermes with cron support.
- A recipient-owned Artificial Analysis API key. The script reads `ARTIFICIAL_ANALYSIS_API_KEY` from the process environment or the active profile's `.env`; it sends the key only in the documented `x-api-key` header to Artificial Analysis. Never put the key in the repo, prompt, job arguments, or chat.
- Public network access to the configured catalog APIs and Artificial Analysis.
- Hermes `web` toolset for the optional research of unbenchmarked models. Keep `no_mcp` enabled for the job unless a source explicitly requires another reviewed tool.

## Configure for a new profile

1. Copy the script to `$HERMES_HOME/scripts/free_model_watch.py`.
2. Copy `config.example.json` to `$HERMES_HOME/cron/free-model-watch/config.json`.
3. Replace the gate and every reference with model names and effort variants present in Artificial Analysis. Choose a timezone and the provider catalogs you want. Do not schedule until the config validates against live names.
4. Add your own `ARTIFICIAL_ANALYSIS_API_KEY` to the active profile's `.env`, or provide it through a secure environment mechanism. Do not reuse another profile's key.
5. Copy `prompt.md` into the cron prompt. Choose your own schedule, timezone, delivery target, and reasoning effort. Use a plain job name such as **Free model catalog and benchmark watch**—do not copy private invocation tags like `[SA]`.
6. Configure the job as an agent-backed pre-run script: the script's `{"wakeAgent": false}` output suppresses quiet runs; changed or incomplete results invoke the agent. Use minimal tool scope (`web`, `no_mcp`). The script has no checkout, model invocation, account writes, or message sending.
7. Run once manually to seed the local baseline. The first run is intentionally silent. Review the saved job and output before enabling the recurring schedule.

Create the job through Hermes' cron UI, `/cron`, or `hermes cron create`; the latter accepts `--script free_model_watch.py`, `--timezone`, `--deliver`, and `--reasoning-effort`. The cron recipe folder is a reviewed setup bundle, not a one-command cron installer. Hermes does not automatically schedule a job when you copy these files.

## State and failure behavior

State and optional cache live under the recipient's `$HERMES_HOME/cron/free-model-watch/`. First run, changed config, or a new Artificial Analysis index version silently re-baselines rather than reporting every existing model as new. A source failure must not advance the comparison baseline; the report must name the coverage gap and must not claim an all-clear. Identical repeated source errors are suppressed after the first notice. The script commits its detected-change snapshot before the agent's final response is delivered; if the agent run or delivery fails after that point, a change may need manual recovery from cron history/state rather than an automatic retry. Provider catalogs and API schemas change over time, so verify the live sources and rate limits during setup.

## Safety and portability

- No schedule, delivery destination, account ID, project ID, user name, reference model, promo, or fallback model is included.
- The recipient owns the baseline models, threshold and timezone. This recipe does not inherit the author's defaults.
- The agent must relay only the script's evidence, distinguish direct scores from unbenchmarked routes, and never present proxy or vendor claims as measurements.
- No purchase, model switch, or account configuration change is authorized by a watch alert.

## Test offline

From this directory:

```bash
python3 -m unittest discover -s tests -v
```

The tests use synthetic catalog/model data and make no network calls.
