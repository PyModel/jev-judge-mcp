# Changelog

## [0.2.1](https://github.com/PyModel/jev-judge-mcp/compare/v0.2.0...v0.2.1) (2026-09-25)


### Bug Fixes

* **evals:** carry the partial evidence on the readers-never-stopped path too ([825c8fb](https://github.com/PyModel/jev-judge-mcp/commit/825c8fbc83f0f18f5c2af1060ef8b32b95fbcbc0))
* **evals:** never return a silently partial agent transcript ([b72c53a](https://github.com/PyModel/jev-judge-mcp/commit/b72c53afeb6f01062cf3d2b36d17ba6dd3019407))
* **install:** build the verify error tail after the drain has finished ([637c996](https://github.com/PyModel/jev-judge-mcp/commit/637c996b7fd2b59cfefa8e4105c0fe25d01118b0))
* **server:** the served regex pool starts warm (ADR-0058) ([99ac61e](https://github.com/PyModel/jev-judge-mcp/commit/99ac61e6e121e35206a520d462729d6899256591))


### Documentation

* add a PyPI download count badge to the README ([39b1544](https://github.com/PyModel/jev-judge-mcp/commit/39b1544b334adfadb6fa45e117b4a4fe39837a4e))
* the passing-case evidence moves to the branch head ([fee5188](https://github.com/PyModel/jev-judge-mcp/commit/fee51887aa657027209502c481d6f88d75febe0d))
* the pre-push gate in contributing and ADR-0056 ([0ba2cfb](https://github.com/PyModel/jev-judge-mcp/commit/0ba2cfb9a5affeff5d412661b7cdc3ee2a000113))


### Continuous Integration

* base the linux leg on the slim uv image with a checksum-verified node ([fe32b0d](https://github.com/PyModel/jev-judge-mcp/commit/fe32b0d83b47290f576177bc6c0ecf7d2c3016db))
* block pushes on the full ci workflow, native and linux (ADR-0056) ([060880e](https://github.com/PyModel/jev-judge-mcp/commit/060880eb886eaa7b69a2a3f164c58513d9a9d8d0))
* emulate security-one-cpu with the job's own docker run contract ([9b73bd0](https://github.com/PyModel/jev-judge-mcp/commit/9b73bd081ad39d1d00d35126dad1c1dee8a11c82))
* guard the watchdog's pkill against the not-yet-started race ([0de3ee7](https://github.com/PyModel/jev-judge-mcp/commit/0de3ee781c1ee6bf744fd15c060118ed1796ef9c))
* make the gate hermetic, bounded, checkout-agnostic, and pushed-commit-faithful ([2e19b66](https://github.com/PyModel/jev-judge-mcp/commit/2e19b66604e7df14cdba07508cbd0fcde11267f4))
* mirror ci.yml's security-one-cpu job in the linux leg ([71a17b0](https://github.com/PyModel/jev-judge-mcp/commit/71a17b04c19d8ac8951e4ecf1f667eca4c50a8a3))
* reap orphans in the linux leg with --init ([53cb5e7](https://github.com/PyModel/jev-judge-mcp/commit/53cb5e764ba5fe7831d08dcf1426c65774df38c2))
* run the linux leg as a non-root runner with the tools the tests shell out to ([b842154](https://github.com/PyModel/jev-judge-mcp/commit/b8421546257c464203627970a4d98c6229709cf4))
* run the ReDoS storm contract on a one-CPU quota ([81e8022](https://github.com/PyModel/jev-judge-mcp/commit/81e802262e01fb680f52cae48e69c327a0b8a2cb))
* stop the one-cpu image lookup dying on a flaky sigpipe ([eb41999](https://github.com/PyModel/jev-judge-mcp/commit/eb419997061eee530698811383cbc34b9ed926af))

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
