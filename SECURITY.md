# Security

## Reporting a vulnerability

Please use a [private GitHub security advisory](https://github.com/Attackwave/caid/security/advisories/new) for vulnerabilities. Avoid disclosing exploitable details in a public issue.

## Supported versions

Security fixes target the latest development version. Earlier `0.x` versions do not receive backports.

## Scope

CAID runs as a KiCad plugin and can read project files, invoke local tools, and connect to configured services. Reports involving command execution, credential exposure, project-file handling, or unexpected network access are in scope. Hardware design correctness is tracked separately as an engineering issue.
