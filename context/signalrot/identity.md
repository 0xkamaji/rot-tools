# SignalRot Identity

This file defines what SignalRot is, what it is trying to be, and the
principles that should guide changes to it.

This file is human-maintained.

Rotbot and AI agents may read this file for context but should never
automatically rewrite it.


# What SignalRot Is

SignalRot is my personal website, digital garden, technical notebook,
creative archive, and home for things I make, learn, document, and think about.

It lives at:

https://signalrot.net

SignalRot intentionally combines different parts of my interests rather than
splitting them into separate brands or projects.

These include:

- cybersecurity and operational security
- reverse engineering
- computers and technical experiments
- hardware and tinkering
- photography
- music and beat-making
- writing
- personal projects
- observations and miscellaneous interests

SignalRot should feel like a place that belongs to one person rather than a
product built for an audience.


# Personality

SignalRot should feel:

- personal
- handmade
- experimental
- strange
- playful
- slightly degraded
- technically curious
- imperfect in intentional ways

The visual language is influenced by:

- old computers
- terminals
- analog media
- corrupted signals
- obsolete hardware
- cassette tape
- DIY electronics
- underground / independent web culture

Do not automatically "clean up" unusual design choices simply because they
differ from standard modern web design.


# What SignalRot Should Not Become

Do not turn SignalRot into:

- a corporate portfolio
- a startup landing page
- a SaaS application
- a generic developer blog
- a commercial photography portfolio
- a commercial music site
- a heavily templated modern website

Usability, accessibility, maintainability, and security matter, but improvements
should preserve the personality of the site.


# Technical Philosophy

Prefer simple, understandable technology.

SignalRot should remain something I can inspect, understand, modify, and repair
myself.

Prefer:

- HTML
- CSS
- vanilla JavaScript
- Markdown
- JSON
- small Python or shell utilities

Avoid adding:

- frameworks
- large dependency trees
- databases
- complicated build systems
- unnecessary services

unless they solve a concrete problem that cannot be handled cleanly by the
existing architecture.

Simple solutions are preferred over technically impressive ones.


# Content Philosophy

SignalRot does not need to stay focused on one subject.

The site's broadness is intentional.

Different areas of the site can have different tones while still belonging to
the same overall SignalRot identity.


## OPPSEC

OPPSEC is practical cybersecurity and operational-security guidance intended
primarily for ordinary people.

The goal is to make people meaningfully safer without creating paranoia.

Important principles:

- realistic threat modeling
- practical security habits
- privacy
- account protection
- device security
- decentralization where useful
- usability matters
- security people actually maintain is better than theoretically perfect
  security they abandon

Technical accuracy matters, but material should remain understandable.


## Hacks

Hacks is a technical notebook.

It can contain:

- reverse-engineering writeups
- debugging
- jailbreaks
- server work
- configuration
- experiments
- fixes
- mistakes
- lessons learned

Posts do not need to read like polished professional documentation.

The process of learning is part of the content.


## Signals

Signals is the broad writing section.

It can contain:

- essays
- projects
- experiments
- observations
- personal technology
- hardware
- music
- tinkering
- miscellaneous interests

Signals should remain flexible rather than developing a rigid subject matter.


## Frames

Frames represents photography, contact sheets, visual fragments, street
photography, and things seen in passing.

It does not need to behave like a professional photography portfolio.


## Beats

Beats represents:

- music
- loops
- samples
- sketches
- unfinished work
- audio experiments
- beat-making

It does not need to behave like a commercial music website.


# Visual Themes

Candy Rot and Dead Signal are both part of SignalRot's identity.

Neither should be treated as an obsolete theme.

Changes to shared interface elements should account for both visual systems.


# Rules for Rotbot and AI Agents

When working on SignalRot:

1. Read this identity file before making design or architectural recommendations.
2. Inspect the current repository before describing how the site works.
3. Do not assume conventional web-development practices are automatically better.
4. Preserve SignalRot's handmade character.
5. Prefer changes that fit the existing architecture.
6. Avoid unnecessary dependencies and abstraction.
7. Clearly distinguish bugs from personal design preferences.
8. Clearly distinguish observations from recommendations.
9. Do not rewrite this file automatically.
10. If the current implementation appears to conflict with this file, point out
    the conflict rather than silently redefining SignalRot.
