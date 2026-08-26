# Tasks — <change-name>

Every task maps to a Requirement in the delta spec. A task that maps to nothing is either
out of scope or a missing requirement — resolve it before implementing.

## Implementation

- [ ] <task>  → Requirement: <name>
- [ ] <task>  → Requirement: <name>

## Verification

- [ ] Test for each Scenario in the delta spec
- [ ] `T2N_*` flag default matches what the spec says
- [ ] Byte-identical fallback verified, if the spec claims one
- [ ] Corpus measurement recorded, if this changes default-ON behaviour

## Ship

- [ ] `CHANGELOG.md` entry
- [ ] Version bump + tag
- [ ] Archive: fold delta into `openspec/specs/`, move this directory to `changes/archive/`
