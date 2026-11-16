# Product context: the CR automation framework and what we know about it

**Created**: 2026-11-16 | **Status**: Reference (read before planning a feature)

Plain-language overview of the product, the manual process and a glossary: [docs/product-overview.md](../../docs/product-overview.md).

## Purpose

Every spec in this folder changes how the platform produces a **recipe** (workflow YAML + CIQ JSON + validation
rules + helper scripts) for the CR automation framework. This page records what that framework is, the manual
process it replaces, and how well we know it — so features are planned against the real target, not assumptions.

## The manual baseline the framework replaces

An engineer follows the MOP by hand with the CIQ's values: log in through NIAM → health check → backup to the repo
server → change → verify against the CIQ → health check → restore by hand on failure → save logs and report. Slow,
error-prone, hard to repeat across many nodes, dependent on experience (details: product overview).

## The framework (cli-automation)

- `lib/cli-automation-standalone.jar`; production runs a differently named JAR with the same code (user, 2026-11-11);
  production's local server is Linux.
- Inputs: **workflow YAML** (`--yaml`: nodes, phases, steps — command, success regex, registers, `when`,
  `on_failure`), **CIQ JSON** (`--json`, `schemaVersion: "2"`), **validation rules** (`--schema`).
- `execute --node <node group> --cr <CR group> [--var NAME=value …]`: logs in over SSH (NIAM keyboard-interactive,
  Ctrl+P Ctrl+E for root), runs each step, checks output with Java regexes, follows the failure / rollback rules
  (spec 011), writes its output and report.
- `generate` renders the commands per node group without running them (the MOP of step 7).

## What we know, and how we know it

| Source | What it gave us |
|--------|-----------------|
| JAR probes (2026-11-03, -04, -05, -11; `javap` + `execute` / `generate`) | Working and empty placeholder forms, Java regex rules, behaviour on empty output, `expr` / `when` limits, loops and records, default stop on failure, rollback phases, Windows-only LOCAL quirks — CLAUDE.md "Workflow Variable Patterns", spec 011 research, spec 013 probes P-1..P-8 |
| 24 production workflows bundled in the JAR (`templates/yaml/`) + `templates/schema/cli-automation-schema_v1.2.html` | Production layout, node model, phase keys and names, retry / checksum / guard patterns (spec 013, `production-style.md`) |
| Three use cases run end to end (PGW_RDS_1058, MRF announcement loading, EIR_1029 TAC loading) | Organisation conventions: NIAM login, repo-server backup and naming, checked transfers, rollback-only runs, never touching unrelated files |
| Bug registers (`bugs.md`, ~330 OB- / DEP- / BUG- entries) | Every behaviour that surprised us, with the fix |

## What we do not know (gaps)

- **No official specification.** Knowledge comes from probing and from the production examples; each new UC has
  still revealed new behaviour.
- **Our CIQ JSON layout differs from production** (`crGroups[]` wrapper; production has `nodeGroups` at the root),
  so production's bare `${niamID}`-style placeholders are empty with ours (spec 013 P-7).
- **Open behaviour**: rollback phases execute in a successful run (OB-25).
- **Not covered yet**: REST and email nodes, notifications, the GitLab API seen in production workflows.
- **Conventions live in code and notes**, not in a profile an admin can see and edit (backup naming, repo route,
  transfer checks, backup scope).

## Consequence for planning

The user should never need framework knowledge (syntax, regexes, placeholders). Features that move this
knowledge into the platform — and make the remaining gaps visible early — come first.
