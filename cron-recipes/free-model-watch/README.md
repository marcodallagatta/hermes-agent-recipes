# Free model catalog and benchmark watch

An opt-in Hermes cron recipe that watches selected public model catalogs against an Artificial Analysis (AA) Intelligence Index gate you choose. Reports contain direct AA scores and AA-backed proxy scores only. Direct AA scores require an exact full route-ID match, preserving punctuation and variants. Proxy matching strips only a terminal free-route suffix, then requires an exact full base-model ID match to an AA slug; ambiguous or leaf-only matches are omitted. Proxy selection prefers `proxy_effort` when set, otherwise the gate effort; if that effort is absent, it uses only a single unambiguous AA match and labels the effort. Routes without either score are omitted; the recipe does not research third-party score claims or run model inference.

## Contents

- `scripts/free_model_watch.py` — bounded pre-run collector; Python standard library plus Hermes' installed provider registry for alias normalization. Pillow is optional for charts.
- `tests/test_free_model_watch.py` — synthetic, offline tests.
- `config.example.json` — placeholders for the gate and references; no default promotions.
- `prompt.md` — self-contained report and optional reply instructions.
- `.env.example` — variable name and placeholder only, never a real key.

## Requirements

- Hermes with cron support.
- A recipient-owned Artificial Analysis API key. The script reads `ARTIFICIAL_ANALYSIS_API_KEY` from the process environment or the active profile's `.env`; it sends the key only in the documented `x-api-key` header to Artificial Analysis. Never put the key in the repo, prompt, job arguments, or chat.
- Public network access to the configured catalog APIs and Artificial Analysis.
- Pillow is optional; when installed in the cron's Python environment, score changes and expiry alerts can include a PNG chart. If Pillow is missing or rendering fails, the text report still works.
- No web research or unrelated MCP tools are needed. Keep `no_mcp` enabled for the scheduled job.
- Optional promotion reminders require a configured promotion and read-only access to Hermes' current model chain through `hermes config get`, including entries still stored under legacy `fallback_model`. Provider aliases are normalized through the installed Hermes registry; if that helper is unavailable, routing status is reported as unavailable rather than guessed.

## Configure for a new profile

1. Copy `scripts/free_model_watch.py` to `$HERMES_HOME/scripts/free_model_watch_recipe.py`; this distinct filename avoids replacing any existing local implementation.
2. Copy `config.example.json` to `$HERMES_HOME/cron/free-model-watch-recipe/config.json`. Replace the gate and each reference with exact Artificial Analysis model names and effort levels present in its live catalog. Choose sources and timezone.
3. To track a time-limited offer, add an entry to `promotions`. Use the catalog's exact source and model ID, the exact Hermes provider/model pair, its end date, and `end_date_estimated: true` when the cutoff is uncertain. Leave the list empty when you have no offers to track. The watch alerts once the day before and once on the expiry date when the route is configured as primary or a fallback. If Hermes cannot read the current chain (including an `auto` provider), it labels the status unavailable rather than claiming the route is in use or silently skipping the reminder. Alerts list current qualifying AA alternatives excluding the expiring route itself, or mark alternatives unavailable when watch coverage is incomplete; the pre-run script reads the chain but never changes it.
4. Add your own `ARTIFICIAL_ANALYSIS_API_KEY` to the active profile's `.env` (see `.env.example`); never commit it.
5. Create the job paused so it cannot fire on its schedule yet:

   ```bash
   hermes cron create "<your cron schedule>" "$(cat /path/to/hermes-agent-recipes/cron-recipes/free-model-watch/prompt.md)" \
     --name "Free model catalog and benchmark watch" \
     --script free_model_watch_recipe.py \
     --deliver "<your-delivery-target>" \
     --timezone "<your-iana-timezone>" \
     --reasoning-effort low \
     --paused --paused-reason "Review configuration before enabling"
   ```

   The scheduled report does not need web tools. Keep `no_mcp` enabled to exclude unrelated MCP servers.

6. If you want to reply to the report with a model chain, opt this job into `attach_to_session: true` in the Cron UI (or the supported cron-job manager). `hermes cron edit --continuity` is different: it carries prior run output, not replies. Hermes mirrors the delivered report — not the job prompt — into the continuable chat, so the prompt requires every report to end with a short reply-contract footer; that footer is what the continued session sees. Use this reply feature only in a one-to-one private chat tied to the account that owns the model settings, with the active bot/profile's inbound allowlist verified to admit only that account and no pending pairings, wildcard, or shared grants. The recipe script itself cannot authenticate senders. Do not enable model-list changes in groups or shared threads. If the owner-only boundary cannot be verified, leave `attach_to_session` off. The continued chat must have access to the Hermes CLI. Send exact provider/model pairs, one per line, as `provider | exact-model-id`; the first is primary and the rest are ordered fallbacks. Ambiguous names should be clarified, not guessed. Only an explicit list is applied. The reply sets only the primary/fallback chain, clears legacy `fallback_model` if present (Hermes otherwise appends it after `fallback_providers`), and clears an inherited `base_url` if the provider changes. It reads the settings back, verifies `hermes fallback list` matches the requested order, and runs `hermes fallback check` without `--live`; it does not modify the cron job or other settings.

7. Run one manual canary with `hermes cron run <job-id>` to seed the local baseline. The first run is intentionally silent unless a configured promotion is due. Because an operator-triggered run may resume a paused job, immediately check `hermes cron list` and pause it again if needed. Review the saved prompt, config, state file, and tool scope; resume only after review.

Create the job through Hermes' cron UI, `/cron`, or `hermes cron create`. The recipe folder is a reviewed setup bundle, not a one-command installer; copying the files does not schedule a job.

## Promotion configuration

Each optional promotion entry has this shape:

```json
{
  "label": "<your label>",
  "source": "<configured catalog name>",
  "model_id": "<exact free catalog model ID>",
  "provider": "<Hermes provider key>",
  "model": "<exact Hermes model ID>",
  "ends": "YYYY-MM-DD",
  "end_date_estimated": false
}
```

The alert says whether the route is configured as primary or a fallback. A configured fallback is not proof that it served a particular turn; Hermes' visible fallback-switch notice is the evidence of an actual switch. Leave warning notifications enabled if you want to see those notices.

## State and failure behavior

Watch state lives under the recipient's `$HERMES_HOME/cron/free-model-watch-recipe/`; the last successful AA response is cached separately under `$HERMES_HOME/cache/free-model-watch-recipe/` for 20 hours. Generated charts use a single private `scores.png` in that cache directory, replacing the previous chart on each report. If an AA refresh fails after that window, the watch uses the last good response and labels its timestamp. The first run, changed config, or a new AA index version silently re-baselines scores rather than reporting every existing model as new; an independently due promotion reminder is still sent. Non-finite, negative, unrepresentable, or implausibly large scores are rejected. Incomplete or inconsistent AA pagination is rejected; only a prior complete cache may be used, with its age labeled. Proxy scores always name the related AA-scored model and are never presented as measurements of the free route. A source failure must not advance the score baseline; the report names the coverage gap and does not claim an all-clear. Identical repeated source errors are suppressed after the first notice, except when a promotion reminder is due: that reminder always carries a coverage warning and marks alternatives unavailable. The script commits its detected-change snapshot before the agent's final response is delivered; if delivery fails after that point, a change may need manual recovery from cron history/state rather than an automatic retry. Provider catalogs and API schemas change over time, so verify live sources and rate limits during setup.

## Safety and portability

- This repository copy is separate from any installed profile's script, config, and state; editing it does not change an existing job. Install only the reviewed recipe files, and never copy profile data or runtime state back into the repository.
- No schedule, delivery destination, account ID, user name, model route, promotion date, fallback choice, or timezone is included.
- The recipient owns the baseline models, threshold, timezone, promotions, and model chain.
- The watch only reads model configuration. A reply-based model-list update is opt-in, applies only to an explicit authorized-user message, and changes only the primary/fallback chain.
- No purchase, model switch, or account configuration change is authorized by an alert alone.

## Test offline

From this directory:

```bash
python3 -m unittest discover -s tests -v
```

The tests use synthetic catalog/model data and make no network calls.
