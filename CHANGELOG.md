## 0.9.0 (2026-08-08)

### Feat

- report optimal range (#26) ([97d1fd6](https://github.com/theadzik/repwise/commit/97d1fd6445f7479f6b6885c459058e7a86630574))

## 0.8.0 (2026-08-07)

### Feat

- check too narrow or wide rep range (#23) ([553cb11](https://github.com/theadzik/repwise/commit/553cb11da10dfb65b41537038e2f5a021e7687cc))

## 0.7.0 (2026-08-07)

### Feat

- bump python to 3.14 (#20) ([15fa729](https://github.com/theadzik/repwise/commit/15fa7298b484e1dd8fd731722ef69c3bd6e0ba80))

## 0.6.1 (2026-08-07)

### Refactor

- cleanup dead code (#15) ([e76dcf7](https://github.com/theadzik/repwise/commit/e76dcf799e4599b17277bc9ef207013a9df2edcc))

## 0.6.0 (2026-08-07)

### Feat

- correct skipLastRest after exercise (#13) ([b190b43](https://github.com/theadzik/repwise/commit/b190b43d784ca8e9c9c7987216676e865b91ae00))

## 0.5.0 (2026-08-07)

### Feat

- granular progression and deloading (#7) ([06196d1](https://github.com/theadzik/repwise/commit/06196d1281f0df36352bd62fbf904d6df9b77b21))
- simplify interactions with config (#6) ([05b7f32](https://github.com/theadzik/repwise/commit/05b7f32589d350cac0e15ac58b1d07d9cc8b7f78))
- narrow check (#5) ([86c11c7](https://github.com/theadzik/repwise/commit/86c11c7bc0c6a7e109325f42b6a991112035e4bf))
- config drives workout (#4) ([14b2d76](https://github.com/theadzik/repwise/commit/14b2d76cf1d871f68032135167075c1c0f6a4491))
- update rest time (#3) ([a46add2](https://github.com/theadzik/repwise/commit/a46add250dbf2bbd4f6a46cb1013c351027e62d0))

## 0.4.0 (2026-07-29)

### Feat

- confirm a push reached the device queue ([5a85c62](https://github.com/theadzik/repwise/commit/5a85c625222962a115adb1eb376f423e1af3f216))
- add --version ([329b0ff](https://github.com/theadzik/repwise/commit/329b0ff9b02b731b657eb6f7768ec2049c3bbbad))
- report every configuration error at once ([466605f](https://github.com/theadzik/repwise/commit/466605f9f8c60426e1ebb2bf0694b48f5d461be3))

### Fix

- explain a login with no terminal to type into ([5cd183d](https://github.com/theadzik/repwise/commit/5cd183db7c0a2ed85226f41a8ee2c914f35d0e80))
- find the config when installed as a package ([39d5ba0](https://github.com/theadzik/repwise/commit/39d5ba00938d64c71a6d9b6ac9a3e8fc24b88230))

### Refactor

- map every failure to an exit code through one hierarchy ([a6c0890](https://github.com/theadzik/repwise/commit/a6c08903ea4f00bd2d36989f7bc78b19dfe754b0))
- split the CLI into parsing, use cases and dispatch ([0566fbd](https://github.com/theadzik/repwise/commit/0566fbd1a5ee3ae2809599f298fa65ce66cdee64))
- move exercise matching into the domain ([ec8eca9](https://github.com/theadzik/repwise/commit/ec8eca9dafe2b612838ce16287f39b92e17fa723))

## 0.3.0 (2026-07-29)

### Feat

- add rep range and weight step to exercise notes ([5e2d598](https://github.com/theadzik/repwise/commit/5e2d5981d212958b0df72c6ec0d8179bc245273f))

### Fix

- don't accept higher load if below low_rep ([812cf0d](https://github.com/theadzik/repwise/commit/812cf0d1732d5d7e645f25f5eb22acc584b0efa1))
- update messages and help ([de21bff](https://github.com/theadzik/repwise/commit/de21bff491e4d58438f7a0eeed88ba5e6eb93601))

## 0.2.0 (2026-07-27)

### Feat

- sync latest activity per workout ([159d1b0](https://github.com/theadzik/repwise/commit/159d1b08fec67d6c8d1f030b75e7e54e587bd090))
- log levels ([59406b7](https://github.com/theadzik/repwise/commit/59406b7b7cfa3db735661537a7b07d732d87fa29))

### Fix

- messages when beating target ([0848a97](https://github.com/theadzik/repwise/commit/0848a97fdb59744491052560c630b38961c14950))

## 0.1.1 (2026-07-26)

### Fix

- mypy findings ([3a07d7a](https://github.com/theadzik/repwise/commit/3a07d7a7585a5198750832ac5fab3e7a67431687))
