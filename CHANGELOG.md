# Changelog

All notable changes to foliot will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and releases use [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Broadened the public positioning from game-oriented language to
  domain-agnostic persistent simulations.

## [0.1.0] - 2026-09-05

### Added

- Mandatory `BaseAction` lifecycle with stable admission identity.
- Scheduled and recurring actions.
- Suspension, exact deadline shifting, and the `on_resume` hook.
- Counter-based deterministic random streams and secure world-seed creation.
- Structural `Store` and `Txn` protocols.
- Dependency-free `MemoryStore` reference implementation.
- Atomic deterministic `Simulation` tick processing.
- Inclusive `ManualDriver` and drift-resistant `RealtimeDriver`.
- Optional post-effect `TickFinalizer` and owner-wide action deletion.
- Optional `foliot.events` layer for simultaneous participant Intents.
- `EventMemoryStore`, stable Event/entity id templates, and explicit Event
  continuation and ending.
- Runnable Layer-1 Tinyworld and Layer-2 Eventworld examples.

[Unreleased]: https://github.com/wordedword75049/foliot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/wordedword75049/foliot/releases/tag/v0.1.0
