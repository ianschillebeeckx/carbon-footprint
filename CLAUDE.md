# Carbon Ledger — working rules

## Assumptions registry is the single source of truth

Every modeling constant (factor, threshold, price, policy) lives in
`src/cf/assumptions.py`. When changing any such value:

1. **Change it in the registry**, never as a literal in the template, worker,
   or scripts — the app reads the injected `ASSUME` map and Python imports
   from `cf.assumptions`.
2. **Rebuild and commit the generated pages**: run
   `.venv/bin/python scripts/build_web.py` (regenerates `web/index.html`,
   `web/methodology.html`, `web/naics.html`) and commit them — the deployed
   methodology page must never lag the code.
3. **Update the entry's prose in the same edit**: rationale, sources, and
   bias tag must describe the *current* value. The methodology page explains
   current factors and their sources only — no change history or bug
   narratives (git history records those).

New constants get a new registry entry (id, rationale, source, bias) rather
than a bare literal; `scripts/build_methodology.py` asserts every entry
renders.

## Other standing rules

- Never commit personal data (`data/*.json` transactions/rules are
  gitignored) or secrets; Cloudflare tokens are session-only, env-var only.
- `site/v2-template.html` is the single source for both the local server and
  the web build; `scripts/build_web.py` transformations assert exact template
  strings, so check the build after editing the template.
