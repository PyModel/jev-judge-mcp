# Changelog

## [0.6.0](https://github.com/PyModel/jev-judge-mcp/compare/v0.5.0...v0.6.0) (2026-09-28)


### Features

* a lowering-only JEV_AB_MAX_USD spend override for the A/B study ([1b65b35](https://github.com/PyModel/jev-judge-mcp/commit/1b65b35985a5f3d99add08f2cf9d7717f429f833))
* confine live agent evals behind a broker sidecar and a capability (ADR-0074) ([f6453b8](https://github.com/PyModel/jev-judge-mcp/commit/f6453b82a11c85f77b70fea71045e6a8a2dfff87))
* grade added tests apart from weakened ones ([67bc438](https://github.com/PyModel/jev-judge-mcp/commit/67bc438f04e6076566c9652538ba0fa308e9b91b))
* out-of-task exploration is recorded, not a study stop (ADR-0074) ([1a92ded](https://github.com/PyModel/jev-judge-mcp/commit/1a92ded7b08ab4975dd133b418e768ff29596dbd))
* package the owner's jev skill and keep Jev on demand ([56ccb0f](https://github.com/PyModel/jev-judge-mcp/commit/56ccb0f686a002fe7910f51114f0d177a14cae9d))
* ship the jev skill with the MCP server ([19100ac](https://github.com/PyModel/jev-judge-mcp/commit/19100acf6912bfece4fedb33d0227aa841c0f656))
* stop the study when a run leaves its boundary ([5cfcaae](https://github.com/PyModel/jev-judge-mcp/commit/5cfcaaec1e60cdd23b15d5543f815132c7d00378))
* the cap persists in the ledger, and a paid pi model needs a ceiling ([85bd171](https://github.com/PyModel/jev-judge-mcp/commit/85bd1716f66c4796b41745c9bc299a5fae2a7931))
* the without-Jev arm runs a boundary with no TypeSafe channel ([3ed1c09](https://github.com/PyModel/jev-judge-mcp/commit/3ed1c09f0b0082d5b818121101b9ff0f39c4ba3d))


### Bug Fixes

* allow the README hero in the hidden-character guard ([62827ca](https://github.com/PyModel/jev-judge-mcp/commit/62827ca798fca8e95a1e2208d37942bba9c02eef))
* an irrelevant added test fails the run ([8eacae7](https://github.com/PyModel/jev-judge-mcp/commit/8eacae739653ee119722d5c053f26f1d36325bb8))
* answer every question the load stub is asked ([525b7c9](https://github.com/PyModel/jev-judge-mcp/commit/525b7c918fc57da57ed5fd381c8db6de8204fdeb))
* assert the load stub's derived source names the caller's evidence ([33631a1](https://github.com/PyModel/jev-judge-mcp/commit/33631a1eaf664badda9958a893dedbf32bdc8381))
* attribute the repo to elkaix and reject hidden characters ([337fd52](https://github.com/PyModel/jev-judge-mcp/commit/337fd52d64df50ab509af28f76a4f0105e5ba56e))
* close the j6, j7, and j8 acceptance defects ([30099f8](https://github.com/PyModel/jev-judge-mcp/commit/30099f8ce8f1fb50a3416ac2008a3ddd70ab1df4))
* close the review findings on the hidden-character guard and adapter path ([98124e5](https://github.com/PyModel/jev-judge-mcp/commit/98124e55dc1ec38c33ef3448cfdd91ea8b343c03))
* correct the bench D3 telemetry ([cbf6c36](https://github.com/PyModel/jev-judge-mcp/commit/cbf6c36f34fbef0fe7dec191430f37bb1fbb6295))
* do not count an unasked source question as fail-closed ([925c53b](https://github.com/PyModel/jev-judge-mcp/commit/925c53b94fcf1832f7d22b4f5a8dc665f406deba))
* do not reflow the packaged jev skill ([f9078b8](https://github.com/PyModel/jev-judge-mcp/commit/f9078b8716afe61cf70050e7f6b642918c8b1392))
* doctor --help prints a real argparse page and exits 0 ([edee34b](https://github.com/PyModel/jev-judge-mcp/commit/edee34bc4825394300cc922e03bbc2adba13698f))
* doctor reports whether the typesafe SDK is importable ([3c381e9](https://github.com/PyModel/jev-judge-mcp/commit/3c381e9b3054c4c9a34b9b05cab7849bc96adf0a))
* **evals:** keep Claude login working and refuse dead batches ([a829584](https://github.com/PyModel/jev-judge-mcp/commit/a82958493e56fe7fccbbff16977e5c5eee6317e6))
* **evals:** scope the keychain link and refuse a dead preflight ([f19c02b](https://github.com/PyModel/jev-judge-mcp/commit/f19c02ba987cd24262505c978d20cbba942d381c))
* exploration and the failure category part ways, derived at render ([f58f3da](https://github.com/PyModel/jev-judge-mcp/commit/f58f3da9837b17bbf26701f9744c281ff9e8143a))
* fail the build when a wheel file has no uv cache key ([5c27bc1](https://github.com/PyModel/jev-judge-mcp/commit/5c27bc1c0940edb18b40f4472ee916a493cc2e75))
* grade added tests the way the agent ran them ([30d9a05](https://github.com/PyModel/jev-judge-mcp/commit/30d9a05185765197db4a039de4130b15fbe425a8))
* inspect exactly one built artifact and name the check for what it does ([383593e](https://github.com/PyModel/jev-judge-mcp/commit/383593edab58d81a860d86ba5a77c385337fee58))
* install the pinned actionlint in hosted CI so the workflow lint runs there ([77e9309](https://github.com/PyModel/jev-judge-mcp/commit/77e93097b029fc5cd63763fb710ac47258e90bac))
* keep host paths out of the agent-visible MCP config ([d995026](https://github.com/PyModel/jev-judge-mcp/commit/d995026a365fda8da3409992972f22647e0ab746))
* keep the ci stage list on the ci line and sync as a prerequisite ([cf009bb](https://github.com/PyModel/jev-judge-mcp/commit/cf009bb9f3d11e9249e10d7cdcbd30dce17671f9))
* keep the escape category on every record path ([b9c69ab](https://github.com/PyModel/jev-judge-mcp/commit/b9c69ab40f0bde44e5ec37e24908cb54ebb7cae5))
* keep the study key out of the harness process environment ([7f942a3](https://github.com/PyModel/jev-judge-mcp/commit/7f942a3d5580f16c1cc654744abf1542a33bcda8))
* keep treehouse checkouts from shipping ignored sdist files ([a1f252d](https://github.com/PyModel/jev-judge-mcp/commit/a1f252d52e2ab255edf9e3ebe0eb16239c8df828))
* launch the study servers from a wheel-built venv outside the repo ([8cbb522](https://github.com/PyModel/jev-judge-mcp/commit/8cbb5223c8028e7c670ae48b20fc021b404333d2))
* let make ci-linux check a linked worktree ([eb1b13b](https://github.com/PyModel/jev-judge-mcp/commit/eb1b13bc00d259928973e14a2bfb901eec57ef40))
* make j4 through j9 hinge on their judgment ([d1a6c0e](https://github.com/PyModel/jev-judge-mcp/commit/d1a6c0eff353c846d547a6cd51f33287d9920dda))
* make restraint on a control measurable ([7083023](https://github.com/PyModel/jev-judge-mcp/commit/70830238f3cb139362ee95e2e5db6ad35ab14f0a))
* make sdk_importable's docstring true and its probe cover RetryPolicy ([92cd660](https://github.com/PyModel/jev-judge-mcp/commit/92cd660a6018279ec28838d8b62de3019877364e))
* make the outcome charts readable in the README ([f908726](https://github.com/PyModel/jev-judge-mcp/commit/f9087260f16d993f123dd754d9594bb0168dd033))
* map an upstream 401 to auth ([7b863e5](https://github.com/PyModel/jev-judge-mcp/commit/7b863e587aa98030b576776d1ba9d6a038407f28))
* match the diagram to the server and free the replay route ([81f725c](https://github.com/PyModel/jev-judge-mcp/commit/81f725cedcb005bac9d732f4c3dbb47b54be07ce))
* name elkaix as the only author and maintainer ([94e8ffb](https://github.com/PyModel/jev-judge-mcp/commit/94e8ffb94589ad873bdcde128862010098d78181))
* name every file that carries the reference tool text ([a58e17f](https://github.com/PyModel/jev-judge-mcp/commit/a58e17fdba8f52ef753efebb10f2cc25056124c7))
* name the skills, the instructions, and the CLI surface in the README ([e8feb0c](https://github.com/PyModel/jev-judge-mcp/commit/e8feb0c342ba7f0f1e3b2c3578a1c334edd28ed8))
* name the uv 0.10.10 cache-key boundary and cover LICENSE ([b339b1f](https://github.com/PyModel/jev-judge-mcp/commit/b339b1f9e397cd5f429ce5e3fc96d8387460d938))
* narrow the notice-test walk for strict pyright ([f317365](https://github.com/PyModel/jev-judge-mcp/commit/f3173657c831ef46e6bc8b5d570a701118d24990))
* notice the copied tool text and the Archify diagram ([8c888b3](https://github.com/PyModel/jev-judge-mcp/commit/8c888b3b47ae47d87d27950a8d909307d2e54aa2))
* one AST rule for pre-existing test content ([9ef905e](https://github.com/PyModel/jev-judge-mcp/commit/9ef905e156f510905c67e7aedc4784c9c71d608b))
* one shell tokenizer for the canary (heredocs, quotes, redirections) ([dc55d5d](https://github.com/PyModel/jev-judge-mcp/commit/dc55d5dd2a4b3ae9f8153a103ed41fe4a7f9a0a7))
* patch the probe seam in the gate tests, never sys.modules ([6df8a73](https://github.com/PyModel/jev-judge-mcp/commit/6df8a737e0a9063ea63858c25868ea08d15d3818))
* pin uv to 0.12.19 across local, Linux, and GitHub CI ([91a5702](https://github.com/PyModel/jev-judge-mcp/commit/91a5702c10e3362350f351239be9e3cef4787933))
* reach the Jev server's key by sandbox keyfile, not the config env ([84b17b5](https://github.com/PyModel/jev-judge-mcp/commit/84b17b52aa291e0ae3afe5a4fa9f25ce32f1a71a))
* read Jev's answer from the one field that carries it ([c823c0c](https://github.com/PyModel/jev-judge-mcp/commit/c823c0c987ccd9fa1d89d09d8e1c8bf646a77418))
* rebuild the escape canary on a run boundary ([295aa2f](https://github.com/PyModel/jev-judge-mcp/commit/295aa2f7039de03d2fb0e76dd59b985cbb411ca3))
* rebuild uvx from current sources instead of a stale cache ([8138719](https://github.com/PyModel/jev-judge-mcp/commit/8138719b617597ace0cf02a1c565d8eceb27aafa))
* refuse at startup when the selected typesafe provider cannot run ([dae5ffe](https://github.com/PyModel/jev-judge-mcp/commit/dae5ffe5404100e805e2a7cb6c91d0d8fa48c4f3))
* refuse checkout installs on uvx older than 0.10.10 ([853a231](https://github.com/PyModel/jev-judge-mcp/commit/853a231c945def505cf3d028449796f244dbef6c))
* render the diagram without an embedded font ([193113d](https://github.com/PyModel/jev-judge-mcp/commit/193113d91b351adc1ea6ad55dadfe80acfe2585f))
* replace the release workflow's ls count with find; lint workflows with actionlint ([c14296f](https://github.com/PyModel/jev-judge-mcp/commit/c14296f96c61cc5b204ef072a5b64c3b6d5c7709))
* rerender the architecture diagram on Archify 7c3ae9a ([6dc23f8](https://github.com/PyModel/jev-judge-mcp/commit/6dc23f8778d1aeb6d586551c70d253307e3d8678))
* restore owner labelers and cited sources ([8f22070](https://github.com/PyModel/jev-judge-mcp/commit/8f2207056443ea077ceb008735bdebe00903d412))
* resync the packaged jev skill to a596996 ([164b44f](https://github.com/PyModel/jev-judge-mcp/commit/164b44f18ffd2fa4a0508658f3a7831dac29fa74))
* satisfy the fake-secret guard on the new test credentials ([77041ee](https://github.com/PyModel/jev-judge-mcp/commit/77041eec933c7775b4748ba200d40f74bbe5f6cd))
* scope models.json and make the key strip unconditional ([13454cf](https://github.com/PyModel/jev-judge-mcp/commit/13454cf3adcfbbbb8eaf0ea2c12980806bcdee9d))
* scope the agent auth copy to the arm's provider ([2d2b6a1](https://github.com/PyModel/jev-judge-mcp/commit/2d2b6a1d6bd36c44520577dc19e11fd74946509a))
* state the completion hook's ask reason as a fact, not a directive ([6bcc531](https://github.com/PyModel/jev-judge-mcp/commit/6bcc53135032a460c569b79e8bacec4142b26fc4))
* stop naming the removed PROVENANCE resource; point threshold warnings at limits.md ([1e429d4](https://github.com/PyModel/jev-judge-mcp/commit/1e429d44578d4f8804291c30419c7143d2b6af34))
* strip quotes and brackets from URIs the skill-text guard scans ([1749a91](https://github.com/PyModel/jev-judge-mcp/commit/1749a91ea7bb46c6f0cc0503a6d3d3fe0657fb93))
* sweep the critique's P3 rows ([495bc61](https://github.com/PyModel/jev-judge-mcp/commit/495bc618bd3000fe9b285aed562dd48451239a9f))
* sync the dev extras before the local CI stages ([143caa5](https://github.com/PyModel/jev-judge-mcp/commit/143caa55978618ed4499211cc7f19cc9291d448c))
* the capability token is scrubbed from the kept records; no operator paths in tests ([bbe53df](https://github.com/PyModel/jev-judge-mcp/commit/bbe53df3b68d828d379e43871413835a6c2c743c))
* the confinement review findings F1-F11, the P3s, Linux CI, and the D3 re-run shape (ADR-0074) ([a9ef345](https://github.com/PyModel/jev-judge-mcp/commit/a9ef3453b9e0de3374bb96b00b569b19458c71a4))
* the container canary reads tool data as data, not as commands ([e2d1883](https://github.com/PyModel/jev-judge-mcp/commit/e2d1883a317271ca5729556fab8ed2dcee8883c2))
* the fixture tripwire checks real paths, on every file tool ([2e073a7](https://github.com/PyModel/jev-judge-mcp/commit/2e073a7d58bdafc7e535b2560db7b8219b3c9b48))
* the report's Jev telemetry says only what the data can support ([4631130](https://github.com/PyModel/jev-judge-mcp/commit/463113012240bde9130d3db8ec4699fb7de0b242))
* type the cache-key glob walk for pyright ([96d8a0f](https://github.com/PyModel/jev-judge-mcp/commit/96d8a0fb77051955013eca8c9e29c416c3055b95))


### Documentation

* ADR-0074 amendment, the canary polices outcomes not wandering ([b2b9203](https://github.com/PyModel/jev-judge-mcp/commit/b2b920399a70cf926c61cf912615e52c68651699))
* ADR-0075 records the selected-uninstallable-provider startup refusal ([23270fa](https://github.com/PyModel/jev-judge-mcp/commit/23270fad01821155f5706e0bcdd5cb66bac231ec))
* cite the parity freeze for the exists thresholds ([9010769](https://github.com/PyModel/jev-judge-mcp/commit/9010769d103e4d2bba377fb7c28222a2c73d2ec8))
* collapse the README rule block ([2cc7173](https://github.com/PyModel/jev-judge-mcp/commit/2cc7173a90d4fdb4766c41766ab07f6a3c58955f))
* correct the Jev docs drift the research note recorded ([45f5abf](https://github.com/PyModel/jev-judge-mcp/commit/45f5abff72e52f43f74e6f55eb05305b4b63cd26))
* index the official TypeSafe Jev docs; keep the corpus in a local cache ([1a7f790](https://github.com/PyModel/jev-judge-mcp/commit/1a7f79033d68cfab22a1ea3706302c9ca32dda4c))
* name the exact Archify viewer source behind the diagram ([5c9dd21](https://github.com/PyModel/jev-judge-mcp/commit/5c9dd21471b83db3e9bcc8e8bb5e203275c0afce))
* name the wire-arguments behavior half as the non-vacuity tie it is ([d6f73c6](https://github.com/PyModel/jev-judge-mcp/commit/d6f73c60f6e503cda3b5c885067372a9e933c9f3))
* pin the 2026-09-27 agent-study figures ([f9f12a2](https://github.com/PyModel/jev-judge-mcp/commit/f9f12a2d49981c99c1bfc6790355909b9180bacd))
* record that a required completion hook asks on an upstream 401 ([848b283](https://github.com/PyModel/jev-judge-mcp/commit/848b2835d34302c5865102b00ffc8a4a62526d06))
* record that the five drift decisions are settled ([1a36711](https://github.com/PyModel/jev-judge-mcp/commit/1a3671140097d220e6d452c0dc6838d5e34ef2d4))
* record the D3 paired study ([44babc0](https://github.com/PyModel/jev-judge-mcp/commit/44babc0fcf0f90174167b89dbb9c1843fcf22bed))
* record the TypeSafe key exposure in the void notice ([461c38c](https://github.com/PyModel/jev-judge-mcp/commit/461c38c93feafb86f134a5065e870f971cda9f0a))
* renumber the grader ADR to 0073 ([5863d4e](https://github.com/PyModel/jev-judge-mcp/commit/5863d4e5dd782cfc77aa12639de8520262932a9b))
* replace the README banner with an optimized hero ([46b595e](https://github.com/PyModel/jev-judge-mcp/commit/46b595e2865128fbc37d022484344b6e08e41df8))
* research the best use of Jev and how this MCP could enforce it ([2638ac3](https://github.com/PyModel/jev-judge-mcp/commit/2638ac3aa99cc7647b1ae458ad45e37cb2088986))
* say classify thresholds probability, not confidence ([49add52](https://github.com/PyModel/jev-judge-mcp/commit/49add52d2c6c5f8df834250b8663e2497bd89b4e))
* say plainly what single-evidence verify rows carry ([c9e7002](https://github.com/PyModel/jev-judge-mcp/commit/c9e7002f937eb3df7e5f91d1e89b020556b1b6ed))
* split the operator-notes lead-in from the later facts ([a8f1512](https://github.com/PyModel/jev-judge-mcp/commit/a8f151220c85f3fe230f6397ae846c6b4d7848b6))
* state the wire-argument strip semantics where callers read them ([c30df48](https://github.com/PyModel/jev-judge-mcp/commit/c30df4854d8faf19f9b46240cb32aafd988568de))
* state which model each provider reports ([9e8f795](https://github.com/PyModel/jev-judge-mcp/commit/9e8f795c6560652380a4e3e4f0d7a04dbcf0e819))
* the confined D3 paired comparison, and the canary fixes it forced ([e3eea69](https://github.com/PyModel/jev-judge-mcp/commit/e3eea69a2220c08c991ee36863e9283183c93226))
* the confined D3 report, re-rendered under the critique's corrections ([58cbc1a](https://github.com/PyModel/jev-judge-mcp/commit/58cbc1a8771c06855b04b19e4d213ce05ea69c42))
* tick the 64-concurrent load gate ([661bd8a](https://github.com/PyModel/jev-judge-mcp/commit/661bd8a1bf715f830344070dea7313fdea61162d))
* void the D3 paired report ([2e9f8df](https://github.com/PyModel/jev-judge-mcp/commit/2e9f8df6a7a636a858d474b3cdea2d6e9d83be03))
* wrap the operator notes at the surrounding column ([7cb270b](https://github.com/PyModel/jev-judge-mcp/commit/7cb270b2289ff1f3ecb0ce66b385f05538730c27))
* wrap the README code blocks that still scroll ([bd7e4b4](https://github.com/PyModel/jev-judge-mcp/commit/bd7e4b4c6c730c75d9c7847ff354ff55f22637b6))

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
