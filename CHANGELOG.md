## 0.7.0 (2026-08-07)

### Feat

- correct skipLastRest after exercise (#13)

### Refactor

- cleanup dead code (#15)

## 0.6.1 (2026-08-07)

### Refactor

- cleanup dead code (#15)

## 0.6.0 (2026-08-07)

### Feat

- correct skipLastRest after exercise (#13)

## 0.5.0 (2026-08-07)

### Feat

- granular progression and deloading (#7)
- simplify interactions with config (#6)
- narrow check (#5)
- config drives workout (#4)
- update rest time (#3)

## 0.4.0 (2026-07-29)

### Feat

- confirm a push reached the device queue
- add --version
- report every configuration error at once

### Fix

- explain a login with no terminal to type into
- find the config when installed as a package

### Refactor

- map every failure to an exit code through one hierarchy
- split the CLI into parsing, use cases and dispatch
- move exercise matching into the domain

## 0.3.0 (2026-07-29)

### Feat

- add rep range and weight step to exercise notes

### Fix

- don't accept higher load if below low_rep
- update messages and help

## 0.2.0 (2026-07-27)

### Feat

- sync latest activity per workout
- log levels

### Fix

- messages when beating target

## 0.1.1 (2026-07-26)

### Fix

- mypy findings
