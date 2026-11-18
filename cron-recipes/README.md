# Cron recipes

This directory is for reusable, opt-in examples of Hermes scheduled jobs. It is not a registry of live jobs and does not auto-create or schedule anything.

## Suggested layout

Give each recipe its own directory:

```text
cron-recipes/<recipe-name>/
├── README.md       # purpose, requirements, schedule, setup and verification
├── prompt.md       # complete prompt to adapt (if it does not fit in the README)
├── scripts/        # optional scripts used by the job
└── .env.example    # optional variable names/placeholders only; never real secrets
```

A recipe should tell the reader:

- What it does, what it reads or changes, and what it deliberately does not do.
- Required Hermes version, skills, tools/MCP servers, commands, and environment variables.
- A suggested schedule and timezone, clearly identified as examples.
- A self-contained prompt: cron runs start in a fresh session without the current chat's context.
- How to choose the reader's own delivery destination; never bake in a personal chat, account, or topic ID.
- How to test the job with a manual run before enabling its schedule, and how to pause/remove it.

## Safety and portability

- Publish recipes, not raw exports of a personal `~/.hermes/cron/jobs.json`. Live entries can contain account-specific prompts, private delivery targets, local paths, job IDs, or assumptions from memory.
- Replace personal names, locations, project details, IDs, home paths, ports, and service names with meaningful placeholders and explain where the reader supplies their own values.
- Never include API keys, tokens, OAuth material, browser state, session data, cron history, or private source data.
- Give jobs plain, descriptive titles. Do not carry over a local owner's execution-mode tags such as `[S]`, `[SA]`, `[AS]`, or `[A]` unless the recipe explicitly teaches a user-defined convention.
- Make external calls and write effects explicit. Mark recipes that send messages, modify files, create tickets, or spend money; require the user to review and intentionally enable those effects.
- Prefer silent behavior when there is nothing to report; document failure and alert behavior.
- Keep scripts bounded, inspectable, and independently testable. State what was actually tested and on which platform.

To create a job, follow the recipe's reviewed setup steps using Hermes' cron interface. A copied prompt or script is not scheduled until the user explicitly creates and enables the job.
