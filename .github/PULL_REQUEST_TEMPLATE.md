<!--
Thanks for the PR. Quick checklist before review:

- 100% line coverage is enforced. Add tests for any new code.
- ruff check + ruff format must be clean.
- Manifest sync (plugin.yaml) is enforced — if you added a tool / hook,
  update both repo-root and inner plugin.yaml.
- For changes to signing paths or wallet modes: explicitly call out the
  threat-model implications below.
-->

## Summary

<!-- 1-3 bullets on what changed and why -->

## Type

- [ ] Bug fix
- [ ] New tool / command / hook
- [ ] Refactor (no behavior change)
- [ ] Documentation
- [ ] Other (describe)

## Risk

<!--
Pick one and explain:

- Read-only (no signing, no state writes)         — typically safe
- New write tool (gates through @write_tool)      — confirm policy gate hit
- Wallet / keystore / signing path change         — call out threat-model impact
- Service / hook ordering change                  — confirm test_plugin_loading
- External API integration (new third-party)      — note auth + rate limits
-->

## Tests

<!-- Coverage delta. New test files / classes. How you verified locally. -->

## Checklist

- [ ] `pytest tests/` passes locally
- [ ] `ruff check clawmes tests` passes
- [ ] `ruff format` applied
- [ ] If touching plugin.yaml: both repo-root and inner copies updated
- [ ] If adding a tool: registered in `tools/__init__.py:register_all` AND `plugin.yaml:provides_tools`
- [ ] If adding a hook: registered in `hooks/__init__.py:register_all` AND `plugin.yaml:provides_hooks`
- [ ] If touching docs: README / CHANGELOG / SECURITY (as appropriate) updated
