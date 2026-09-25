# Changelog

## [0.2.0](https://github.com/PyModel/jev-judge-mcp/compare/v0.1.1...v0.2.0) (2026-09-24)


### Features

* **compare:** warn when an aspect contradicts the overall relation ([4dfcbf0](https://github.com/PyModel/jev-judge-mcp/commit/4dfcbf0244052c29fd908dd756c4fec96d4a60f5))
* **http:** require a bearer token for non-loopback binds ([cec103b](https://github.com/PyModel/jev-judge-mcp/commit/cec103bfb4c79a30b8c89afa85185708b0697b2b))
* **installer:** pin the published PyPI package by default (ADR-0051) ([bfa34d8](https://github.com/PyModel/jev-judge-mcp/commit/bfa34d8d31a1e56c6dc3513bc25ad676ef4d7956))
* **providers:** retry transient upstream failures under one bounded policy (ADR-0057) ([5d4f60b](https://github.com/PyModel/jev-judge-mcp/commit/5d4f60b54249c8f0279e00f104b5cc8542f94539))


### Bug Fixes

* default the HTTP port to 8088 and retry a taken bind ([3ee9d3b](https://github.com/PyModel/jev-judge-mcp/commit/3ee9d3bd7f0ab9260542fd9c9ba4a0bb9e244e8b))
* **deps:** bound runtime and optional dependencies to the tested series ([e07c279](https://github.com/PyModel/jev-judge-mcp/commit/e07c279ccffb6107924df1208ade3079426f0cdc))
* **evals:** bound the live cap to real requests with SDK retries off ([c07d71c](https://github.com/PyModel/jev-judge-mcp/commit/c07d71c9d12e293b3e7093be67d5b38435c50d42))
* **evals:** certify the AUTO operating point on locked_test and gate on it ([a34e49b](https://github.com/PyModel/jev-judge-mcp/commit/a34e49bdda6660b3148b874050303bc2c49c67f1))
* exit cleanly when the HTTP port is already in use ([616a689](https://github.com/PyModel/jev-judge-mcp/commit/616a689e8bbd0b221915bdc3e89f9a80e2969235))
* **extract:** cover the worker spawn window against cancellation ([927085e](https://github.com/PyModel/jev-judge-mcp/commit/927085e90f83e89e06adb4d44d41df25daac4fcb))
* hermetic listing test and NUL-separated git listing ([11f1868](https://github.com/PyModel/jev-judge-mcp/commit/11f1868e9eae213572c8879405320f62caa50e77))
* **installer:** honor the python floor in entries and verify (ADR-0053) ([553bdb0](https://github.com/PyModel/jev-judge-mcp/commit/553bdb028e51a7bd9bb152055d6ab287dea94be2))
* **installer:** record the post-write verify outcome in state ([c5fe9a1](https://github.com/PyModel/jev-judge-mcp/commit/c5fe9a106c76129410f5349615e97cb5b1482d9e))
* **providers:** classify resets, permanent transports, and the hard budget ([1502fe9](https://github.com/PyModel/jev-judge-mcp/commit/1502fe92128fd0756b10280e8b89fc23a8be75e6))
* **providers:** report budget-bound timeouts as exhausted retries ([cd29bfb](https://github.com/PyModel/jev-judge-mcp/commit/cd29bfb32a448e73b81555e0ed03c67ce9943e69))
* report a checkout build identity on the wire ([e536cc6](https://github.com/PyModel/jev-judge-mcp/commit/e536cc6aa0c514c375935bf04e72f2c77815491c))
* scan only repo-owned files for short fake secrets ([7bdbd69](https://github.com/PyModel/jev-judge-mcp/commit/7bdbd69f785c69a4dea3b4db463920f384f8ea03))
* skip deleted-tracked files and pin the listing contract in a real repo ([e661e8b](https://github.com/PyModel/jev-judge-mcp/commit/e661e8bb1ad586609a53808a5a8239bc912e8ef7))
* treat TIME_WAIT as free on the HTTP port probe ([dded642](https://github.com/PyModel/jev-judge-mcp/commit/dded642651b2b5c257a84f4a296b23772ad9dbac))
* type the JSON fixture walk for pyright ([d9bab9c](https://github.com/PyModel/jev-judge-mcp/commit/d9bab9cd5e98943506286a77114007b3a4f4e619))


### Documentation

* add test-audit skill gating test authorship and audits ([4a0d61a](https://github.com/PyModel/jev-judge-mcp/commit/4a0d61a06c359532a32f1758db30dde6a7838c70))
* bounded provider retries in the operator notes (ADR-0057) ([c23acf5](https://github.com/PyModel/jev-judge-mcp/commit/c23acf59d666c9f4635c0979f2af951d07dc638a))
* explain CI guards and release checks ([7912345](https://github.com/PyModel/jev-judge-mcp/commit/7912345614431f8dbe230d332f7a16271139e58e))
* installer python request and checkout warning (ADR-0053) ([3281a99](https://github.com/PyModel/jev-judge-mcp/commit/3281a99dcb3e990b5f4c67a1f8ac0aaaa810c356))
* name the full sync that development and make typecheck need ([d143a3a](https://github.com/PyModel/jev-judge-mcp/commit/d143a3a555d3c4fc83801e93701e5780c23087bd))
* note how to read an escalate from a low-confidence score ([1f89be3](https://github.com/PyModel/jev-judge-mcp/commit/1f89be378006e56dc7a56f8b90f5c840f32bb9ce))
* note that a flat jev_rerank ordering is weak ([48bbbd5](https://github.com/PyModel/jev-judge-mcp/commit/48bbbd547de73b675fd4185c40aa24cfe22e6d47))
* **readme:** note the uncapped verify/screen input and the stdio provider deadline ([0c12e9a](https://github.com/PyModel/jev-judge-mcp/commit/0c12e9ae67ef174210f34c00813b4b047d47e7d0))

## [0.1.1](https://github.com/PyModel/jev-judge-mcp/compare/v0.1.0...v0.1.1) (2026-09-24)


### Documentation

* **readme:** collapse installation into disclosure blocks and tidy the layout ([2625ff0](https://github.com/PyModel/jev-judge-mcp/commit/2625ff07a2f61357e0b8f408d6dae9aee810db50))
* regenerate the architecture diagram for the jev-judge-mcp rename and embed it in the README ([f3820b2](https://github.com/PyModel/jev-judge-mcp/commit/f3820b2c21258cbed8bf7d3eb6715440e3f4a8ad))

## 0.1.0 (2026-09-23)


### Features

* jev_score extension tool ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
* jev-judge-mcp, a Python MCP server for TypeSafe's Jev judgment tools ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
* offline eval, parity, and security suites ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
* opt-in response cache, opt-in command hook, and experimental Streamable HTTP transport ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
* policy that turns Jev's probabilities into auto, review, or escalate ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
* setup, install, and doctor commands; install registers the server with Claude Code, Claude Desktop, Codex, Cursor, OpenCode, Pi (eager, direct tools), omp, and Pythinker ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
* ten Jev judgment tools over stdio (jev_verify, jev_screen, jev_find, jev_classify, jev_decide, jev_rerank, jev_compare, jev_extract, jev_review, jev_gate), wire-compatible with the reference server and checked by recorded parity fixtures ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
* TypeSafe, OpenRouter, Cloudflare, and compatible-endpoint providers, configured from the environment only, with secrets redacted from logs and errors ([bff5450](https://github.com/PyModel/jev-judge-mcp/commit/bff54502de8409a2e69e21de9d11f9e75c01dc03))
