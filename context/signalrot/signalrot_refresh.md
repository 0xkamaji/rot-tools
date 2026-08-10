# SignalRot Current State

Last refreshed: 2026-08-10

# Snapshot

## Overview

SignalRot is a handmade static personal site combining practical security guidance, technical notes, broad-form writing, photography, and music. The repository is clean and retains the simple HTML, CSS, vanilla JavaScript, Markdown, and asset-based architecture described by the immutable identity context. The newest substantial body of work is a seven-part OPPSEC guide collection with dedicated styling, page behavior, navigation, and imagery.

## Current sections

- OPPSEC
- Hacks
- Signals
- Frames
- Beats

## Published content

- 7 OPPSEC guides

## New since previous refresh

The repository now contains a complete seven-guide OPPSEC sequence:

- Start Here
- Protect Your Accounts
- Secure Your Devices
- Browse the Web Safely
- Public Spaces & Outside Threats
- Secure Your Home Network
- What to Do If You Get Compromised

The OPPSEC addition also includes dedicated CSS, JavaScript, a banner image, a section index, and a reusable guide-page entry point. This is the clearest new content and implementation work visible since the previous refresh.

## Current focus

Current work is concentrated on OPPSEC: approachable, practical guidance covering threat modeling, account security, device protection, safer browsing, physical and public-space risks, home-network security, and compromise recovery. The sequence is structured as a progression from basic orientation through prevention and incident response, consistent with SignalRot’s goal of improving ordinary people’s security without creating paranoia.

## Repository vs production

The repository working tree is clean, so the inspected OPPSEC state is committed rather than unfinished local work.

Production is behind the repository. The deployment dry-run shows that production does not yet contain the seven OPPSEC Markdown guides, `assets/css/oppsec.css`, `assets/js/oppsec-page.js`, `oppsec/assets/temp_banner.jpg`, or `oppsec/page/index.html`. It would also create the associated OPPSEC asset and page directories.

The existing `oppsec/index.html` differs between repository and production in content or size as well as timestamp and permissions. Directory timestamps also differ for the shared CSS and JavaScript directories and for `oppsec/`. No production-only files are identified by the supplied comparison.

## Possible next steps

- Review the seven OPPSEC guides for spelling, naming, link, and navigation consistency before deployment; notably verify the repository filename `2. Protect Your Accoutns.md`.
- Replace or deliberately approve the temporary OPPSEC banner asset before publishing.
- Preview the OPPSEC index and guide-page flow in both Candy Rot and Dead Signal themes.
- Check mobile layout, keyboard navigation, contrast, reduced-motion behavior, and JavaScript-disabled fallbacks.
- Validate that guide URLs, Markdown loading, previous/next navigation, and compromise-response links work from the production web-root layout.
- Review the unexpected permissions change reported for `oppsec/index.html` before synchronizing.
- Run one final deployment dry-run, then deploy the OPPSEC CSS, JavaScript, guides, page structure, image, and updated index together.
- After deployment, compare repository and production again and perform a live smoke test of every OPPSEC guide.
