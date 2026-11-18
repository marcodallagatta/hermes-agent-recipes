# Hermes Agent Recipes

A community collection of reusable workflows for [Hermes Agent](https://hermes-agent.nousresearch.com). This repo is community-maintained, not an official Nous Research distribution.

## Contents

- [`skills/`](skills/) — installable Hermes skills.
- [`cron-recipes/`](cron-recipes/) — opt-in recipes for creating your own scheduled jobs. These are examples, not live jobs; review and adapt them before scheduling.
- [`guides/`](guides/) — standalone guides and technical walkthroughs.

## Install a skill

Install the Tailscale browser-egress skill directly:

```bash
hermes skills install marcodallagatta/hermes-agent-recipes/skills/tailscale-exit-node-browser-egress
```

Or add this repo as a tap to search and install skills from it:

```bash
hermes skills tap add marcodallagatta/hermes-agent-recipes
hermes skills search tailscale
hermes skills install marcodallagatta/hermes-agent-recipes/tailscale-exit-node-browser-egress
```

Community skills are security-scanned when installed. Review the skill and its requested capabilities before accepting it.

### Available skill

[`tailscale-exit-node-browser-egress`](skills/tailscale-exit-node-browser-egress/SKILL.md) covers choosing and preparing an exit-node device, routing a dedicated browser through an isolated loopback SOCKS proxy, checking egress safely, and tearing down. It does not bypass CAPTCHAs or route provider-side page fetching. See the [standalone guide](guides/route-browser-through-tailscale-exit-node.md) for the full walkthrough.

## Cron recipes

Cron recipes are opt-in setup bundles, not live jobs. Hermes does not install or schedule them from a repo copy; each reader creates their own job after review. Do not import a personal `jobs.json` entry blindly: adapt the prompt, schedule, timezone, required skills/tools, script paths, and delivery target to your own setup. See [`cron-recipes/README.md`](cron-recipes/README.md) for the recipe format and safety expectations.

Use plain job names without private execution-mode tags such as `[SA]`. The placeholders below are yours to replace; no schedule, destination, account, or timezone is supplied by this repo.

### Install the free model catalog and benchmark watch

1. Copy `cron-recipes/free-model-watch/scripts/free_model_watch.py` into the active profile's scripts directory (`$HERMES_HOME/scripts/`; for the default profile when `HERMES_HOME` is unset, this is `~/.hermes/scripts/`).
2. Copy `config.example.json` to `<active HERMES_HOME>/cron/free-model-watch/config.json` and replace every placeholder with an exact Artificial Analysis model name and tested effort level. Choose your sources and timezone.
3. Put your own `ARTIFICIAL_ANALYSIS_API_KEY` in the active profile's `.env` (see `.env.example`); never commit it.
4. Create the job paused so it cannot fire on its schedule yet:

   ```bash
   hermes cron create "<your cron schedule>" "$(cat /path/to/hermes-agent-recipes/cron-recipes/free-model-watch/prompt.md)" \
     --name "Free model catalog and benchmark watch" \
     --script free_model_watch.py \
     --deliver "<your-delivery-target>" \
     --timezone "<your-iana-timezone>" \
     --reasoning-effort low \
     --paused --paused-reason "Review configuration before enabling"
   ```

   The CLI does not expose per-job toolsets; in the Cron UI, allow `web` for unbenchmarked-source research and `no_mcp` to exclude unrelated MCP servers.

5. Run one manual canary with `hermes cron run <job-id>` to seed the baseline. The first run should be silent. Because an operator-triggered run may resume a paused job, immediately check `hermes cron list` and pause it again if needed. Verify the config, state file, prompt and tool scope; resume only after review.

### Install the Granola meeting action candidates recipe

1. Connect your own Granola account and confirm the exact read-only list/query tool names in your profile.
2. Copy `cron-recipes/granola-action-candidates/ledger.example.json` to a private path under the active profile, such as `<active HERMES_HOME>/cron/granola-action-candidates/ledger.json`.
3. Replace every `{{...}}` placeholder in `prompt.md` with your tool names, connection, owner name, lookback, timezone, and ledger path.
4. Create the job paused, with the prompt file and your own schedule/destination:

   ```bash
   hermes cron create "<your cron schedule>" "$(cat /path/to/hermes-agent-recipes/cron-recipes/granola-action-candidates/prompt.md)" \
     --name "Granola meeting action candidates" \
     --deliver "<your-delivery-target>" \
     --timezone "<your-iana-timezone>" \
     --paused --paused-reason "Review connector and ledger before enabling"
   ```

5. Configure the paused job to expose only the Granola read-only list/query tools and local file access; do not attach Linear, email-send, or other write tools. Run one manual canary with `hermes cron run <job-id>` to seed the ledger silently. Because an operator-triggered run may resume a paused job, immediately check `hermes cron list` and pause it again if needed. Inspect the ledger and run result, then resume only after the setup is correct.

Both recipes suppress quiet runs. Review `hermes cron list` and one manual run before enabling delivery.

## License

MIT. See [LICENSE](LICENSE). Individual entries may specify additional attribution or licensing requirements in their own documentation.
