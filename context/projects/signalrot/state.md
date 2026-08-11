# SignalRot Current State

Last refreshed: 2026-08-10

# Snapshot

## Overview

SignalRot is a handmade static personal site combining practical security guidance, technical notes, broad writing, photography, and music. It uses simple HTML, CSS, vanilla JavaScript, Markdown, and local assets in keeping with its immutable identity. The repository working tree is clean. The newest substantial body of work visible in the supplied state is the seven-part OPPSEC collection and its dedicated presentation layer.

## Current sections

- OPPSEC
- Hacks
- Signals
- Beats
- Frames

## Published content

- 7 OPPSEC guides

## Section updates

### OPPSEC
- Current: seven published practical-security guides with a section index, reusable guide page, dedicated CSS and JavaScript, navigation, and banner imagery
- Latest addition or change: complete seven-guide sequence covering orientation, accounts, devices, web browsing, public-space threats, home networks, and compromise recovery
- Last changed: 08/10/26 5:16pm

### Hacks
- Current: technical notebook section for reverse engineering, debugging, configuration, experiments, fixes, and lessons learned
- Latest addition or change: no newer content addition or meaningful change could be established from the supplied comparison
- Last changed: 08/10/26 5:16pm

### Signals
- Current: broad writing section for essays, projects, experiments, observations, hardware, music, and miscellaneous interests
- Latest addition or change: added write up for tape player pitch modification, bringing the current number of signal postings to two!
- Last changed: 08/10/26 5:16pm

### Beats
- Current: external link section
- Latest addition or change: external-link routing remains the latest established configuration; no newer destination change was identified
- Last changed: 08/10/26 5:16pm

### Frames
- Current: external link section
- Latest addition or change: external-link routing remains the latest established configuration; no newer destination change was identified
- Last changed: 08/10/26 5:16pm

### Contact
- Current: contact entry associated with the main site rather than a counted publishing section
- Latest addition or change: no newer contact-content or configuration change could be established from the supplied comparison
- Last changed: 08/10/26 5:16pm

## New since previous refresh

No additional content newer than the previous refresh is established by the supplied repository and production state. The seven-guide OPPSEC sequence remains the newest identifiable addition:

- Start Here
- Protect Your Accounts
- Secure Your Devices
- Browse the Web Safely
- Public Spaces & Outside Threats
- Secure Your Home Network
- What to Do If You Get Compromised

Its supporting implementation includes `assets/css/oppsec.css`, `assets/js/oppsec-page.js`, a section index, a reusable guide-page entry point, and `oppsec/assets/temp_banner.jpg`.

## Current focus

Current development is concentrated on OPPSEC. The section forms a practical progression from threat-model orientation through account, device, browsing, physical-space, and home-network protection to compromise recovery. This aligns with SignalRot’s stated aim of making ordinary people meaningfully safer without encouraging paranoia or requiring an unnecessarily complex technical stack.

## Repository vs production

The repository working tree is clean, indicating that the inspected state is committed rather than unfinished local work.

Production is behind the repository. The supplied deployment dry-run would add:

- `assets/css/oppsec.css`
- `assets/js/oppsec-page.js`
- Seven OPPSEC Markdown guides
- `oppsec/assets/temp_banner.jpg`
- `oppsec/page/index.html`
- The associated `oppsec/assets/` and `oppsec/page/` directories

The existing `oppsec/index.html` also differs in content or size, modification time, and permissions. Directory timestamps differ for `assets/css/`, `assets/js/`, and `oppsec/`. The comparison identifies no production-only files.

## Possible next steps

- Correct or deliberately retain the filename typo `2. Protect Your Accoutns.md`, then verify every reference to it.
- Replace or explicitly approve `temp_banner.jpg` before deployment.
- Review the seven guides for factual accuracy, spelling, consistent terminology, internal links, and previous/next navigation.
- Preview the OPPSEC index and guide pages in both Candy Rot and Dead Signal.
- Test mobile layout, keyboard navigation, contrast, reduced-motion behavior, and JavaScript-disabled fallbacks.
- Verify that Markdown loading and guide URLs work from the production web-root layout.
- Investigate the permissions difference reported for `oppsec/index.html`.
- Run a final deployment dry-run and deploy the OPPSEC files as one coherent release.
- Smoke-test all seven live guides after deployment, then repeat the repository-to-production comparison.
- Record dated changes for Hacks, Signals, Beats, Frames, and Contact in future refreshes so section activity can be tracked reliably.
