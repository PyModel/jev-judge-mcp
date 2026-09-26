# Changelog

## [0.5.0](https://github.com/PyModel/jev-judge-mcp/compare/v0.4.1...v0.5.0) (2026-09-26)


### Features

* adapt public JevBench items to the eval harness (A8 preparation) ([d4a78a2](https://github.com/PyModel/jev-judge-mcp/commit/d4a78a28834cc0ff94f0033213b54fe41417f6af))
* add advisory calibrate subcommand fitting thresholds on caller-labeled rows ([3f3051b](https://github.com/PyModel/jev-judge-mcp/commit/3f3051b8ae2fa2a35198e4edee872f51ed2c0fbe))
* add order-sensitivity probe to the eval harness (A2) ([e4276ac](https://github.com/PyModel/jev-judge-mcp/commit/e4276ac143c5e039da62c043e8fe5f10bf0fcb8b))
* **cache:** off-loop IO, a TTL, and oldest-first eviction (ADR-0047 amendment) ([8e93953](https://github.com/PyModel/jev-judge-mcp/commit/8e93953b8c159bf173076079bb54a93bd4222f99))
* file lists name each reviewed file's action (file_actions) ([5a02bf1](https://github.com/PyModel/jev-judge-mcp/commit/5a02bf14959f0755088d160b4634e3e518c837c1))
* gate file list verifies claims once, per file review stays per file ([25016dd](https://github.com/PyModel/jev-judge-mcp/commit/25016dd6970bc2c49947b49978a2519480a23942))
* **providers:** opt-in JEV_MCP_MAX_INFLIGHT cap on concurrent requests ([fd26573](https://github.com/PyModel/jev-judge-mcp/commit/fd26573c54a77488dff863ac74a0ae5abc4653f5))
* **telemetry:** log the metrics snapshot at INFO every 100 tool spans ([0c2972b](https://github.com/PyModel/jev-judge-mcp/commit/0c2972beb4b86827e324b5d33ef5851ecce4c2f6))


### Bug Fixes

* address every critique finding on fm/jev-eval-calibrate (P2-1..4, P3-1..6, E1..3) ([0f2cdbe](https://github.com/PyModel/jev-judge-mcp/commit/0f2cdbe1494ac192efc022bdf3aa7ccc6358f4df))
* **cli:** code isError envelopes with the one parity mapping ([422c17f](https://github.com/PyModel/jev-judge-mcp/commit/422c17fdb36cf66e74a14187792fb1cc6ce7dd34))
* critique follow-ups H1-H4 (hook gates, cache leftovers, serve wiring test, off-path hops) ([14c2569](https://github.com/PyModel/jev-judge-mcp/commit/14c256973026acf3a2ed1d6d7d3a343e6dd49dcc))
* delta-critique-a follow-ups — sweep before no-evict, scaffold guard, worst-last pin (C1, C2, F1) ([0c35884](https://github.com/PyModel/jev-judge-mcp/commit/0c358842b7301bbf6d80c3c005dcb73343a32130))
* delta-critique-b nits — one source_commit call, required control (F3, F4) ([831fab4](https://github.com/PyModel/jev-judge-mcp/commit/831fab4cb7ad716ca936f3dd30d1f3db59fc0ca2))
* example collects every confidence across the real payload shapes ([f2d3c86](https://github.com/PyModel/jev-judge-mcp/commit/f2d3c86710eda8f1bdf5653a92018b5bf7c586d9))
* gate file list keeps isError, sums usage, shares file plumbing ([0cf8928](https://github.com/PyModel/jev-judge-mcp/commit/0cf89284c6c5a118c6676db7c719138409f0b3c3))
* **hook:** match completion commands on tokens, not a string prefix ([c6641f7](https://github.com/PyModel/jev-judge-mcp/commit/c6641f7724d7c326046d8ab3f936eea4313f6bb9))
* qualify file_actions' worst-entry claim and pin the clamp shapes (D1, G-A, G-B) ([38b7b34](https://github.com/PyModel/jev-judge-mcp/commit/38b7b340a4d079b86a315f5bfd030ce43afd94e1))
* **responses:** code every frozen budget refusal input_too_large ([dc06e0e](https://github.com/PyModel/jev-judge-mcp/commit/dc06e0e1e84cbdbb1644186aaba56c5327f88cb0))
* review-critique follow-ups on the gate file list (F1-F3, G1-G3) ([d33c98e](https://github.com/PyModel/jev-judge-mcp/commit/d33c98e215caecf12b1e1163944c67140394fad4))
* **server:** configure redacting logging before the judge/gate/hook subcommands ([6630fc8](https://github.com/PyModel/jev-judge-mcp/commit/6630fc8a3f5753b5282443d2d1c10f90989a018c))
* **server:** hand the HTTP listen sockets to uvicorn, closing the probe gap ([6862536](https://github.com/PyModel/jev-judge-mcp/commit/6862536f10a62fe72e569f1dad458ee7ef421eeb))


### Documentation

* caller guide for writing states and questions ([86bdcfb](https://github.com/PyModel/jev-judge-mcp/commit/86bdcfbb5dad16b3c5054d2b1f4dc8e126be7690))
* claims are positional strings, not id records ([87b65b6](https://github.com/PyModel/jev-judge-mcp/commit/87b65b60f279b35e4ed0f232912b3025f22dc896))
* fix every critique finding on fm/jev-readme-bench (P1-A..P3-8) ([fc70ce6](https://github.com/PyModel/jev-judge-mcp/commit/fc70ce6a64a269eefa0a37ddad579e1a7c8a33ca))
* fold caller-docs' EVIDENCE.md pointer into the reworked Measured results; depth now points at guidance.md ([89c5369](https://github.com/PyModel/jev-judge-mcp/commit/89c5369d9a4d334dd08271a78b0bf8511aed4b92))
* lead with measured speed, cost, and decision quality; add agent setup rules ([e386694](https://github.com/PyModel/jev-judge-mcp/commit/e386694fff6c858750540764a99813c91a6155ca))
* limits page for caps, defaults, and error codes ([66cecf3](https://github.com/PyModel/jev-judge-mcp/commit/66cecf30e809bcf2dde01f01b5289c9b58af6433))
* name the pin behind every budget refusal's error code ([47d5f4e](https://github.com/PyModel/jev-judge-mcp/commit/47d5f4e0bcd61efa6f475f997338fe24063737d4))
* per-tool cards with measured evidence and weak spots ([0c04ad8](https://github.com/PyModel/jev-judge-mcp/commit/0c04ad8fc46314b194c6dc6b1ee140b1d9701739))
* pin or source every restated frozen number ([1215d47](https://github.com/PyModel/jev-judge-mcp/commit/1215d471a5950c1d5c9211444f4ecd0bbdac79bb))
* pin the install command's -y exactly; state the public_tasks id join (F1, F2) ([a95c097](https://github.com/PyModel/jev-judge-mcp/commit/a95c097b2f152f55063e0657c45e6a310e9fb9bf))
* release evidence page and a release step that fills it ([ee28b62](https://github.com/PyModel/jev-judge-mcp/commit/ee28b62611e9cbdb8978c72233a4a768e778337a))
* state the per-file diff review rule on the limits page and tool cards ([5d579f0](https://github.com/PyModel/jev-judge-mcp/commit/5d579f0b69a36541cd54d1e1bc01c967d25c012d))
* track the JevBench run's machine evidence; point the adapter at the recorded run (F1, F2) ([e807384](https://github.com/PyModel/jev-judge-mcp/commit/e80738438b21da46ca69380d103a6a9c64b6ae1c))

## [0.4.1](https://github.com/PyModel/jev-judge-mcp/compare/v0.4.0...v0.4.1) (2026-09-26)


### Bug Fixes

* score rubric window, caller-input error codes, config log, --help ([4697a02](https://github.com/PyModel/jev-judge-mcp/commit/4697a02627b01a1aeb0e78f11795e5a37e8cb3d9))

## [0.4.0](https://github.com/PyModel/jev-judge-mcp/compare/v0.3.0...v0.4.0) (2026-09-26)


### Features

* state item shapes plainly in array argument descriptions ([f91cb5e](https://github.com/PyModel/jev-judge-mcp/commit/f91cb5e055df0e6145b281edeacf501385c38326))


### Bug Fixes

* judge envelope reads each tool's own decision field ([27eec8c](https://github.com/PyModel/jev-judge-mcp/commit/27eec8c513225345ccc312749d859129f8f9d0e5))


### Documentation

* document renamed_ids duplicate-id semantics ([0ff8cc7](https://github.com/PyModel/jev-judge-mcp/commit/0ff8cc7d16acaee2ad49eda7ccecefbbc6668183))

## [0.3.0](https://github.com/PyModel/jev-judge-mcp/compare/v0.2.2...v0.3.0) (2026-09-26)


### Features

* **cli:** add judge and gate commands and an opt-in completion hook ([76dd45a](https://github.com/PyModel/jev-judge-mcp/commit/76dd45afd7ff4e91cba123c6a8cc26d9f768a3a5))
* **doctor:** print policy_version beside the policy defaults ([8cdba50](https://github.com/PyModel/jev-judge-mcp/commit/8cdba50f818765948ea15ed68b472185db024d6d))
* **hook:** split decision rendering and add JEV_HOOK_REQUIRED ([50febb1](https://github.com/PyModel/jev-judge-mcp/commit/50febb1d33fe5e7242908bc9fa58bcffc1149ace))
* **mcp:** build initialize instructions from the tool registry ([2d708f1](https://github.com/PyModel/jev-judge-mcp/commit/2d708f1001e132fee6f630afe53b2457543e15a6))
* **tools:** add response fields, implicit evidence, and file diffs ([a8d6af5](https://github.com/PyModel/jev-judge-mcp/commit/a8d6af5114378ca8a957ba444e04c2fe5cb0b965))


### Bug Fixes

* close the Claude dogfood release blockers ([b05d646](https://github.com/PyModel/jev-judge-mcp/commit/b05d646b05576f3f203f10ffd1f77097598f37cd))
* close the stage-8 findings on the CLI, hook, and file list ([cc70abd](https://github.com/PyModel/jev-judge-mcp/commit/cc70abdab0cf56d6b4ccf2a71119fe81da6b054e))
* **harness:** match Bash so the completion hook can fire ([b06caec](https://github.com/PyModel/jev-judge-mcp/commit/b06caec35d6700eca448a6f0b1341f4d44161930))
* keep the live typesafe request id header ([6b52f8f](https://github.com/PyModel/jev-judge-mcp/commit/6b52f8f0cc69e335f2784482799be52fdcde9222))
* map sanitized ids back to the caller with renamed_ids ([8422a3e](https://github.com/PyModel/jev-judge-mcp/commit/8422a3eec1c0f8f2a33709f05c9c69c3da5bd821))


### Documentation

* describe the CLI, completion hook, and response fields ([0a1681e](https://github.com/PyModel/jev-judge-mcp/commit/0a1681ebd33457a9d5c9d562f0ba262bdc6c10ca))

## [0.2.2](https://github.com/PyModel/jev-judge-mcp/compare/v0.2.1...v0.2.2) (2026-09-25)


### Documentation

* **changelog:** list the fixes that shipped in 0.2.1 ([5490f1c](https://github.com/PyModel/jev-judge-mcp/commit/5490f1c6fe3d7b63e98ff65c3bcab0a972248443))
* use compact rounded README badges ([7037cf5](https://github.com/PyModel/jev-judge-mcp/commit/7037cf5723bff11d663b838574c5f0e59aa39a49))

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
