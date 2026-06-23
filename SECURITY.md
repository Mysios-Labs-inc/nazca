# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** — do not open a public GitHub issue.

Use GitHub's **private vulnerability reporting** (the repository's *Security → Report a
vulnerability* tab), or contact Mysios Labs, Inc. through the repository's GitHub
organization.

Please include:

- a description of the issue and its impact,
- steps to reproduce (or a proof of concept),
- affected version / commit.

We aim to acknowledge reports within a few business days and will keep you updated on
remediation. Please give us reasonable time to address the issue before any public
disclosure.

## Scope

nazca reads provider credentials from **environment variables only** (`FAL_KEY`,
`ARK_API_KEY`, `OPENAI_API_KEY`) and Google Vertex Application Default Credentials. It
never stores secrets in the repository. Reports concerning credential handling, command
execution, or data exfiltration are in scope.
