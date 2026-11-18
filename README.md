# hermes-skills

Community skills for [Hermes Agent](https://hermes-agent.nousresearch.com).

Install a single skill directly from GitHub:

```bash
hermes skills install marcodallagatta/hermes-skills/skills/tailscale-exit-node-browser-egress
```

Or add this repo as a tap to search and install any skill in it:

```bash
hermes skills tap add marcodallagatta/hermes-skills
hermes skills search tailscale
hermes skills install marcodallagatta/hermes-skills/tailscale-exit-node-browser-egress
```

Skills from community taps are security-scanned on install and show a third-party trust warning. That is expected.

## Skills

### [tailscale-exit-node-browser-egress](skills/tailscale-exit-node-browser-egress/SKILL.md)

Route **one browser** through a Tailscale exit node without changing the host's default route. It walks through picking and preparing a device that can act as an exit node, what to do when no suitable device exists, an isolated userspace Tailscale client with a loopback-only SOCKS proxy, a low-volume egress check that never prints addresses, and teardown. It also explains why a browser proxy does not automatically route agent web search or provider-side page fetching.

A [human-readable guide](docs/route-browser-through-tailscale-exit-node.md) is included for people who do not use Hermes.

Safety boundaries in the skill: no global `HTTP_PROXY`/`HTTPS_PROXY`, no host-wide exit node unless explicitly requested, device authorization links treated as bearer secrets, and no solving or bypassing CAPTCHAs or bot challenges.

## License

MIT. See [LICENSE](LICENSE).
