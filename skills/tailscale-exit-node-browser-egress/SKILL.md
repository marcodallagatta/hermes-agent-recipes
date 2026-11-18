---
name: tailscale-exit-node-browser-egress
description: "Guide setup of a Tailscale exit node and browser egress."
version: 0.1.0
author: Marco (mae), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Tailscale, ExitNode, Browser, Egress, Networking]
    related_skills: [blocked-page-recovery, computer-use]
---

# Tailscale Exit Node Browser Egress

Make one browser appear to browse from a device on your Tailscale network, without changing the host's default route. This covers choosing and setting up that device, routing a dedicated browser through it with a loopback-only SOCKS proxy, verifying the egress safely, and tearing it down. It does not hide all host traffic, and it never solves or bypasses a bot challenge.

## When to Use

- The user wants browser traffic to exit from a specific device (a home connection, a different region, or a residential IP) while other apps stay on the normal route.
- The user asks to use a Tailscale device as an exit node for browsing, or to route search/browser traffic through one.
- A page is blocked for the host's IP and a user-controlled exit node is available.

Don't use for:
- A general VPN setup, hiding all host traffic, or evading a CAPTCHA/bot challenge.
- Setting a system-wide exit node when only one browser was in scope; ask first.

## Prerequisites

- Tailscale installed and signed in on the host that runs the browser (the client).
- A second device on the same tailnet that can act as an exit node, with a stable connection and the desired egress location.
- Ability to approve the new client in the Tailscale admin console (a one-time device authorization).
- No root needed for the recommended path: userspace networking plus a loopback SOCKS proxy.

## How It Works

An exit node is a tailnet device that relays internet traffic. Selecting one at the OS level changes every app's route. Instead, run a second isolated Tailscale client in userspace with a SOCKS proxy bound to `127.0.0.1`, select the exit node only in that client, and point one dedicated browser at that proxy. The system Tailscale stays direct.

## Part 1 - Pick and prepare the exit-node device

### Choose a device

Candidates include an always-on Linux machine or Raspberry Pi, a desktop, a NAS, a phone, or an Apple TV/tvOS device with the Tailscale app. Pick one that is:

- Powered on and reachable when needed.
- On the upstream connection whose IP/location you actually want.
- Already on your tailnet, with exit-node capability enabled and no expired key.
- A device you control and trust: its operator sees the traffic you send through it.

### Verify it is usable from the client

A device visible in the admin console may be absent from a particular client's peer list (tailnet sharing or split permissions). After the client is authorized, inspect that client's own peer map:

- `tailscale --socket=<socket> status` and the peer list should show the named device as online and advertising an exit-node option.
- If MagicDNS lookup fails but the peer is present, take its Tailscale address privately from the peer record and use that address.
- If the peer is absent, stop. Use only a fallback the user explicitly approves and that this client can actually see. A configured preference or an admin-console approval alone is not proof the route works.

### If there is no suitable device

1. Set one up: install Tailscale on an always-on device you control, sign in, enable exit-node mode (`tailscale up --advertise-exit-node` on Linux, or the app's exit-node toggle on Apple TV/mobile/desktop), then approve it in the Tailscale admin console under Machines -> Edit route settings -> Enable exit node. Keep the device powered and online.
2. If you cannot add a device, use a user-approved alternative: a shared exit node from another tailnet, a commercial VPN/proxy endpoint you trust, or a small cloud VM you control running a SOCKS proxy. Be explicit that this changes who operates the egress.
3. If none of that is possible, continue with ordinary retrieval (caches, archives, provider results) and say plainly that no exit node was used. Do not claim the route was used without verifying it.

## Part 2 - Set up the isolated browser path

Linux example with a unique socket and a loopback-only SOCKS listener; adapt the same shape for macOS or Windows.

1. **Record the baseline.** Read the system daemon's exit-node preferences (`tailscale status`, `tailscale status --json`; fields `ExitNodeID`, `ExitNodeIP`, `RouteAll`). Completion: you can restore this exact state later.
2. **Start a separate userspace client.** Run `tailscaled --tun=userspace-networking --socket=<socket> --socks5-server=127.0.0.1:<port> --state=mem:` for a temporary identity, or point `--state` at a protected directory for a persistent one. Completion: the SOCKS listener appears on `127.0.0.1` only and the system daemon's preferences are unchanged.
   - Keep any state directory at mode `0700` and the state file at `0600`; never print, copy, or commit its contents. A `mem:` identity is lost on stop; a persistent one survives restarts but needs one device authorization.
3. **Authorize the isolated client.** Send every control command through `tailscale --socket=<socket> ...`; a plain `tailscale up` can change the host-wide route. The auth link printed by `up` is a one-time bearer secret: do not paste it into chat, logs, or notes; open it in a browser the user can see and let them sign in, then remove any local URL file. Completion: the client reports authenticated and online, not `NeedsLogin`.
4. **Select the exit node in that client only.** `tailscale --socket=<socket> set --exit-node=<device-or-address>`. Completion: that client shows the exit node selected while the system daemon still reports no exit node and `RouteAll=false`.
5. **Attach only the dedicated browser.** Launch it with `--proxy-server=socks5://127.0.0.1:<port>` (use `socks5h://` where supported so DNS resolves remotely) and a separate browser profile. Completion: the specific browser process command line carries the flag and the browser loads a neutral page. Never set global `HTTP_PROXY`/`HTTPS_PROXY` or change the host's exit node for this.
6. **Verify egress without leaking addresses.** Send one small, non-personal request directly and one through the proxy to a neutral IP-echo endpoint. Compare the two addresses in memory and print only status codes, a validity flag, and a `differs` boolean. Completion: both HTTP 200, valid addresses, `differs=true`, system route unchanged. Do not use a blocked login or CAPTCHA page as the probe.
7. **Use, then tear down.** Keep only the dedicated browser and proxy running and state that teardown is pending. On teardown: close the browser, stop the exact daemon/service, verify the SOCKS listener is gone and system preferences are unchanged, and let the temporary node expire or remove it. Do not delete a persistent state file or registered device unless the user explicitly asked to remove that identity.

For persistence across restarts, use a dedicated user service with `UMask=0077` and file-backed state, enable lingering for the user, and verify it reconnects after a service restart before promising boot survival.

## Search and page-fetch caveat

A browser proxy does not automatically route an agent's web search or page extraction. Check the provider configuration (`hermes config get web` in Hermes) and distinguish local provider requests from the provider fetching a page on its own infrastructure. Proxying an API call does not make the origin site see the exit node. If the local search provider supports a proxy setting, set it only for that process (for example, a DDGS `DDGS_PROXY=socks5h://127.0.0.1:<port>` service drop-in) and verify with per-call `served_by`/`rescued_from`. Never set global proxy variables.

## Pitfalls

- Treat the device authorization URL as a bearer secret; keep it out of chat, logs, and planning files.
- A configured preference or an admin-console approval is not a working route; verify actual egress.
- A peer visible to the system daemon may be missing from the isolated client's peer list.
- Do not prove the browser is proxied by counting command lines containing the flag: the inspection command itself contains the string. Identify the actual PID/executable.
- If a CAPTCHA or bot challenge appears, stop and hand off to a human in the same session. Never solve, disguise, or bypass it. A block seen during a human-driven login is a human-observed outcome, not an automation failure.
- Keep probes and browsing low-volume so both links stay healthy; do not route traffic through a device you do not trust.

## Verification

- [ ] System exit-node preferences recorded first and unchanged at the end.
- [ ] SOCKS listener bound to loopback only.
- [ ] Isolated client authenticated, online, and selecting the intended exit node.
- [ ] A dedicated browser process has the proxy flag.
- [ ] Low-volume A/B returned 200, valid, and `differs=true` without printing IP addresses.
- [ ] No global proxy variables set and no challenge was bypassed.
- [ ] Teardown completed, or the pilot is explicitly left running with teardown still pending.
