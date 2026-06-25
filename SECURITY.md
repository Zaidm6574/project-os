# Security Policy

## Reporting Privacy Or Security Issues

If you find a privacy leak, secret exposure risk, unsafe default, or security issue in this template, do not publish raw private data in a public issue.

For a private friend/project copy, contact the repository owner directly.

For a public fork, use the security reporting channel configured by that repository owner. If no private channel exists, open a minimal public issue that describes the affected file and behavior without including secrets, raw chats, local paths, screenshots with personal data, or private exports.

## Scope

In scope:

- template files
- setup script behavior
- chat import script behavior
- `.gitignore` privacy protections
- documentation that could cause users to commit private material

Out of scope:

- private memories, raw chat exports, or local projects created from the template
- optional OSVec or GraphOS tools that users add later
- AI tool/provider behavior outside this repository

## Policy Versus Enforcement

This repository contains markdown rules, setup scripts, and templates. Those rules can guide an AI assistant, but they do not create a technical sandbox by themselves.

Do not treat the template alone as proof of:

- air-gapped browsing
- network egress filtering
- container isolation
- model routing
- secret scanning beyond the included local checks
- safe execution of untrusted code

If a project needs those guarantees, verify them with the host tool, operating system sandbox, container runtime, CI policy, or security scanner before relying on them.

## Safe Review Standard

Before publishing a fork or sharing a project made from this template, run a local privacy review and inspect the results manually. Automated redaction helps, but it is not a guarantee that every kind of personal data or secret was removed.
