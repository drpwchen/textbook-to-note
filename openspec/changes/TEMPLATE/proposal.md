# <change-name>

## Why

<The problem, in terms of observed behaviour. What goes wrong today, for whom, how often.
If there is a measurement, it goes here. "It would be nicer if" is not a why.>

## What changes

<The behaviour difference a user would notice. Not the implementation.>

## Not doing

<The scope guard. List what a reasonable reader might expect this change to include, and
state that it does not. This list is checked at review time and must still match the spec
when the change ships.>

-
-

## Risk

<What breaks if this is wrong. Which existing requirement could regress. Whether the
change alters default-ON behaviour (which needs corpus measurement) or adds an opt-in flag
(which does not).>

## Affected specs

- `openspec/specs/<domain>/spec.md` — <added / modified / removed>
