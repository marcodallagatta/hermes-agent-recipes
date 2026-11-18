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

Cron recipes are meant to be reviewed and configured by each user. Do not import a personal `jobs.json` entry blindly: adapt the prompt, schedule, timezone, required skills/tools, script paths, and delivery target to your own setup. See [`cron-recipes/README.md`](cron-recipes/README.md) for the recipe format and safety expectations.

### Available recipes

- [Free model catalog and benchmark watch](cron-recipes/free-model-watch/README.md) — configurable provider-catalog watch with a user-chosen Artificial Analysis score gate.
- [Granola meeting action candidates](cron-recipes/granola-action-candidates/README.md) — notes-only, cited action candidates with a recipient-owned processing ledger.

These are setup bundles, not live jobs; review and configure them before creating a cron.

## License

MIT. See [LICENSE](LICENSE). Individual entries may specify additional attribution or licensing requirements in their own documentation.
