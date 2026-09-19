## Summary

<!-- What changes and why. Reference the SPECIFICATIONS.md sections this implements. -->

## Specification deviations

<!-- None, or list each deviation with the ADR / open-question entry that records it (§91). -->

## Definition of done (SPECIFICATIONS.md §82)

- [ ] Implementation and tests exist; edge cases are handled
- [ ] Authorization is verified for every user-owned resource touched
- [ ] Errors are handled and use the standard error envelope
- [ ] Documentation updated where necessary (README, architecture, ADRs)
- [ ] `make check` passes locally (format, lint, type check, tests, build)
- [ ] No security regression (secrets, input validation, dependencies)
