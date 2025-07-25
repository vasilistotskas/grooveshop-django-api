# CHANGELOG




## v1.21.0 (2025-07-15)

### Bug fixes

* fix: Clear BlogTag objects before name filter test

Added deletion of all BlogTag objects at the start of the test_filtering_by_name method to ensure a clean state for the test. This prevents interference from existing tags and improves test reliability. ([`a547fdf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a547fdf1b6e7db3c7e4c7546578baba8c76b554f))

* fix: Update admin list display tests and blog tag search

Removed 'id' from expected list display fields in admin tests for contact, pay way, user, and vat modules to reflect updated admin configuration. Changed blog tag view test to use 'search' parameter instead of 'name' for filtering tags. ([`07a8523`](https://github.com/vasilistotskas/grooveshop-django-api/commit/07a85235bf161a0a61ffb8b0ff013e0f0a7f7104))

### Features

* feat: Bump UV, remove "id" from admin `list_display ` and some admin classes renamed ([`f9dc562`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f9dc562b371c7c662e98ca6b1ab44cce29409e1a))

* feat: Add more tests ([`9f1398d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9f1398dc2e14e15f175c7278a9870b47c13dfa38))

## v1.20.0 (2025-07-11)

### Bug fixes

* fix: CI test failure in profiler tests

- Fixed timestamp calculation issue in test_add_memory_snapshot and test_add_slow_query
- Issue occurred when hardcoded start_time (1000.0) was greater than time.perf_counter() in CI environment
- Added @patch('time.perf_counter') to mock consistent time values
- Used assertAlmostEqual for floating point precision tolerance
- Ensures tests pass consistently across different environments ([`2a988cd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a988cdaa76cb3c7ebd6b3b5f8033752f55266a5))

### Features

* feat: New tests, Bump Versions, pytest settings fixes ([`3ac7795`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3ac7795d4a5e0b54613568f337473a7c2e6b9dde))

## v1.19.0 (2025-07-09)

### Bug fixes

* fix: `ProductCategoryFactory` translations ([`b049fd3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b049fd3d6b11f3039a63d44e39aea64f6833d48f))

### Features

* feat: Refactor category factory and update type checking config

Simplified logic in ProductCategoryFactory for checking translation needs and reformatted argument placement. Removed mypy configuration from pyproject.toml ([`0a01f04`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a01f047537cb1d91cde56427514eb1f80bee873))

## v1.18.0 (2025-07-09)

### Features

* feat: Bump Versions, improve factories and seeding ([`e8a0264`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e8a0264caf0ecce3574d33361e4b701b793c6e72))

## v1.17.3 (2025-07-05)

### Bug fixes

* fix: factories auto_translations set False ([`e8d7254`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e8d7254c9c173b0c1cae12d88c56e0ca32550958))

## v1.17.2 (2025-07-05)

### Bug fixes

* fix: Update Dockerfile ([`f559b67`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f559b674a583c44917cb4edbe455c9d0ffcc5818))

## v1.17.1 (2025-07-05)

### Bug fixes

* fix: update uv.lock ([`f6d80dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f6d80dc7c636fe6971b1ae38cd849bbb31ee7989))

## v1.17.0 (2025-07-05)

### Features

* feat: Update pytest and coverage ([`86ab3fa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/86ab3fa1530e9ba9386bd0bb2e6d3ba152131cc5))

* feat: type improvements, Bump Version and bug fixes ([`cfc17c9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cfc17c998727ea40e95855142ea9e36b21d5afe0))

## v1.16.0 (2025-06-27)

### Bug fixes

* fix: tests ([`34e9a38`](https://github.com/vasilistotskas/grooveshop-django-api/commit/34e9a38b2f77c024e230d0e6a917a06f70ffb484))

* fix: lint ([`416bf75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/416bf75cf009b1765223b96b156366faff1121c6))

* fix: product signals, test fix ([`308dc95`](https://github.com/vasilistotskas/grooveshop-django-api/commit/308dc9514b2ba8d8703a38c40a69208c032046cb))

* fix: Product category `__str__` fix ([`916146b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/916146bef12b0ae458b4fc4a3662eac1c631570b))

* fix: lint ([`651f5b1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/651f5b125168e5931c36029b3b27e541f1497e79))

* fix: test_str_representation ([`4641ade`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4641ade0fb2fb81b60b399182c31109fa00108e0))

* fix: test_str_representation ([`49357f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/49357f0c920f4db2848d0a74b6e8013cab85e53b))

### Features

* feat: Seed all command rewrite

- New `core\utils\dependencies.py`
- New `core\utils\profiler.py` ([`b2201b4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b2201b4bc9ba41609b5d6fbec16af6a2ca3c3cd2))

## v1.15.0 (2025-06-25)

### Features

* feat: Improve admin with `unfold`, Bump Versions ([`60b85d8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/60b85d8ae29f4fe25fc4d6b04215d3c8614ea5a4))

## v1.14.0 (2025-06-22)

### Features

* feat: Include more tests, added managers, added filters, split cart and cart item, ProductCategoryImage and more ([`c93cc31`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c93cc31ef74779091ad868d8ae15a2d8b295db66))

* feat: Remove vat API views, remove tip app, remove slider app, refactor API endpoints and schema and Bump Versions ([`e626219`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e6262191c133b2ad456afe5114aebac9569d3be0))

## v1.13.0 (2025-06-07)

### Bug fixes

* fix: remove useless tests ([`8d40865`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d40865308f4ae0258d5c8b12ff8fae8a8277d78))

### Features

* feat: postgres specified version, tasks improved and Bump Versions ([`7b17681`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b176810cc7fec3ac7e4c0ff044922eb4491c85b))

## v1.12.0 (2025-06-04)

### Chores

* chore: remove useless files ([`50526fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50526fb47da4186472d8513e6a64cf0a181e73ba))

### Features

* feat: Increase postgres pool, Bump Versions ([`1c35327`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c353276d440056d6de457cdcb7a5393da3faf5d))

## v1.11.0 (2025-06-01)

### Features

* feat: Improve API schema docs, generate locales and Bump Versions ([`d6c88a1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d6c88a17d23751c8e6c59f05ea1d29d795f87e8f))

## v1.10.0 (2025-05-31)

### Features

* feat: Update user address API view, Cart refactor to use headers instead of session, Remove `@override` decorators, update API docs, blog API filters, ([`d73fa58`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d73fa5899cfb1dd14db761ec0a1e948ba2c4bc55))

## v1.9.0 (2025-05-24)

### Features

* feat: `ci.yml` build wheel with `--installer uv`, added `session_key` at cart serializer, app structure improvements, payment api view serializers implemented ([`a32a366`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a32a36626787a5ca50bb2b4d33752f317f88eef3))

## v1.8.1 (2025-05-23)

### Bug fixes

* fix: remove [build-system] ([`c5806a0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c5806a0cbbae01000283a77af027d39fb8dc6063))

## v1.8.0 (2025-05-23)

### Features

* feat: Update `pyproject.toml` and `ci.yml` fix ([`3c0aa56`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3c0aa5695f4dcde8c2aa890ea43d5c1dc7f48c03))

## v1.7.1 (2025-05-23)

### Bug fixes

* fix: error exposure ([`839993b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/839993b71c6160104cbafb3691b3f984a1847bd8))

## v1.7.0 (2025-05-23)

### Features

* feat: remove `thumbnail` fields, ImageAndSvgField refactor, github ci permissions added, remove useless stuff, ([`14c440e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/14c440ed8f85726a16cbaa821baea33e4c6136c2))

* feat: Optimize Dockerfile ([`d20369f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d20369f8415ad2ff6af74a0120e062fa33f86c80))

## v1.6.0 (2025-05-21)

### Bug fixes

* fix: test fix ([`b05c890`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b05c8904c76346510e610553bff309b5053a0083))

### Features

* feat: Use django daphne ([`fcb1cf7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fcb1cf74d79baa5c1670a0260a0356f5c9532041))

## v1.5.0 (2025-05-21)

### Features

* feat: Remove `image_tag`, docker updates ([`e0cb53e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e0cb53edcf7195f2d4d72a3deb2ca297ce99badd))

## v1.4.6 (2025-05-17)

### Bug fixes

* fix: ci ([`a3643bd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3643bd6f9f9031707e131d98f16759508c17eeb))

## v1.4.5 (2025-05-17)

### Bug fixes

* fix: ci ([`4d8d587`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4d8d5871704c5b80a2e6a182111c63a4891e3de2))

## v1.4.4 (2025-05-17)

### Bug fixes

* fix: ci ([`c2bc11f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c2bc11f6d9f931ed2591c4f9703edc0d815f5d91))

## v1.4.3 (2025-05-17)

### Bug fixes

* fix: ci ([`c5ab944`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c5ab944172c845d02f7b9fd033bfe0cd3f020f05))

## v1.4.2 (2025-05-17)

### Bug fixes

* fix: ci ([`3999706`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3999706be4f14e621d581e668775d0a26debe680))

## v1.4.1 (2025-05-17)

### Bug fixes

* fix: uv sync ([`b03c255`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b03c255a097941da345114bece91a85c92e0c5eb))

* fix: ci ([`84bc944`](https://github.com/vasilistotskas/grooveshop-django-api/commit/84bc944ef11e0955d765d914fc0b633dc136c552))

## v1.4.0 (2025-05-17)

### Bug fixes

* fix: find_packages at setup.py ([`e13e7aa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e13e7aa3f90c014e2b274f98d618ee406f964617))

* fix: ruff format ([`1f8d547`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1f8d54744b4372c14beaae743470fa1c078ded43))

* fix(tests): increase stock at order test ([`8806e37`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8806e372d6e17a361d48b727d8a1759d4ed0a578))

* fix: ci ([`5548e1b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5548e1b91d286027042317aa237d789cb59ac1fc))

* fix: ci ([`5b4372e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5b4372e498abb85e7f6f77a45122c6d1afd74c55))

* fix: ci ([`d1fdfd5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d1fdfd575a818ee5354d558880b7d94490c98916))

* fix: ci ([`a3121ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3121ed9f7fbe76893be9529f55a0e5560efa8c2))

* fix: ci ([`38948aa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/38948aa22e4a9fd0c45da051f517de51c22bdc71))

* fix: ci ([`6565aee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6565aee50410eabcba1ae229514d3ed7a8f31753))

* fix: add python-version file ([`0997afc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0997afc595d758b706dead787acf436059f20576))

### Features

* feat: replace poetry with uv ([`683bfb6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/683bfb6671695e90fe9d0d6832f002d916c2ed2e))

## v1.3.0 (2025-05-17)

### Features

* feat: compile messages and api schema, ci release check to run only at main branch and pay way admin fix ([`7229ca6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7229ca60ab5bec0bf71b293bfcf736095a1ff096))

## v1.2.0 (2025-05-17)

### Bug fixes

* fix(tests): Increase popular_post view count ([`bc2a42f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bc2a42f2ff2f2c99c71be84df63dd0c693bd3f58))

* fix: pre-commit run ([`8430ba7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8430ba79fef9350e46180415df78ffc9c8f936d3))

* fix: pre commit run ([`c869e28`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c869e2849d74ecefd79c9328cd8a50492b15ea00))

* fix: remove useless comments and prints, test fixes ([`392d802`](https://github.com/vasilistotskas/grooveshop-django-api/commit/392d80239082cf0d6faffd88d06841feaf31e325))

* fix: metrics update ([`b5109b5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b5109b5a1e6057f733db39f1ad308db6d305a698))

### Chores

* chore: update poetry lock ([`afd5f4d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/afd5f4d3a74b7a0f81d06d4456756a5e3aba9cfe))

### Continuous integration

* ci: Increase testing timeout ([`bfb27a1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bfb27a18fed2f3822742ae23f151075b521bcc07))

### Features

* feat: Improve tests performance 8s->2s ([`d79a99d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d79a99d46e7fb1791d0983650e4f1fee0e5aefe7))

* feat: Pay way app improved ([`27442f1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/27442f11043b7e81ddf7636e7b31b23a43350275))

* feat: Rewrite order, general improvements

- SUPPORT_EMAIL rename to INFO_EMAIL ([`199eec0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/199eec08384ad1ebd705d61ce862894cf5f69571))

* feat: Remove `status` field from blog post, factory and admin panel fixes ([`d21076a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d21076a91af5f49367067e6a521a87c315d32d6c))

* feat: Update model indexes and blog post filters ([`1ae899e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1ae899e57929118156973c9e015510f3a2125b54))

* feat: progress ([`479b813`](https://github.com/vasilistotskas/grooveshop-django-api/commit/479b813df7844b3f7cf1265f81bad570653b3f89))

* feat: Init prototype ([`89f3915`](https://github.com/vasilistotskas/grooveshop-django-api/commit/89f391566a2e311204a35a8c132ddff0711b3f17))

## v1.1.0 (2025-05-05)

### Features

* feat: Bump Versions, Admin panel enhance ([`91125d2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/91125d2255f32c7a4fc4b48fc3f60a38248b241c))

## v1.0.1 (2025-05-02)

### Bug fixes

* fix: cart migration ([`7f6b37d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7f6b37db08ed909026d9882ed9b4e6b8bf40f5dc))

## v1.0.0 (2025-04-30)

### Bug fixes

* fix: cart factory ([`8862002`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8862002c883232b9b8ceca732697c05f63b3ed69))

* fix: cart session_key ([`1252e88`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1252e88af6e8dd07723cabc4f214da7e63013a26))

* fix: cart session_key migration ([`0eafd57`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0eafd5760c726eb21532fa4529e8c5892706c313))

* fix: ruff format ([`dbae591`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dbae5914485d5a12417d1c87dd1745fe5fbcc5c8))

### Features

* feat(core): implement guest cart functionality and system-wide improvements

* cart: add session-based guest cart support with new session_key field
* admin: integrate UnfoldAdminSite and optimize admin views
* logging: replace custom LogInfo with Python logging module across multiple components
test: add comprehensive tests for guest cart functionality
build: upgrade multiple dependencies including poetry, django-unfold, and celery
docs: update API documentation and schema.yml for cart endpoints

BREAKING CHANGE: Cart model schema updated with session_key field ([`eeec158`](https://github.com/vasilistotskas/grooveshop-django-api/commit/eeec1586d473a77065abf15fdae31aa6e878ac93))

## v0.199.0 (2025-04-16)

### Features

* feat: Bump Versions ([`65abdce`](https://github.com/vasilistotskas/grooveshop-django-api/commit/65abdcee612de114129b5263e1f5835b53cecdff))

## v0.198.1 (2025-04-12)

### Bug fixes

* fix: revert tailwind ([`4ab08a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ab08a8d33b1ce0b27e2305b87a497c1d25f5944))

## v0.198.0 (2025-04-12)

### Features

* feat: unfold ([`8b60df4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b60df42abe7d60de8a65b6440bbe4603d72fad6))

## v0.197.0 (2025-04-12)

### Chores

* chore: rename service to services ([`8a6c3a9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8a6c3a9114284d106b5887c6d30a7037bbc33189))

### Continuous integration

* ci: update pg defaults ([`bc4569a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bc4569a5713ebea28313ee249352469447466b01))

* ci: Added ruff hook at `pre-commit-config`, lint fixes and cleanup compose.yml ([`0a3f1bf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a3f1bf87b9cb1a1c1cbbe711141d028b5fb3aad))

### Features

* feat: Remove expand functionality ([`d859631`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d85963182925c9ab2183a87711b40913b0f7b344))

## v0.196.1 (2025-04-05)

### Bug fixes

* fix: static url ([`8b876d3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b876d39285870663858f7f186d35e46adda5885))

## v0.196.0 (2025-04-05)

### Bug fixes

* fix: pyproject.toml ([`8609e78`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8609e789c00889c86d2dac3901daea7f56acc278))

* fix: pyproject.toml ([`ca89963`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ca89963da0209dd8e9fac541a7fa8c3b325f0e9a))

### Features

* feat: Bump Versions

- Add AI related files to .dockerignore and .gitignore
- Update pyproject ([`c7a3e1e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c7a3e1e3a7df7de0d9b66bcdef6f0f403742097a))

## v0.195.1 (2025-03-31)

### Bug fixes

* fix: revert tailwind ([`6e54cab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6e54cabceeb1936bff422ea88d5818678c620802))

## v0.195.0 (2025-03-30)

### Bug fixes

* fix: poetry lock ([`adfff33`](https://github.com/vasilistotskas/grooveshop-django-api/commit/adfff33c045a294620902ef56133fff221b9221e))

### Features

* feat: Bump Versions, tailwind 4, notifications fixes ([`dad42cb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dad42cb64f082dfbd6024756a23e920b7aec8dff))

## v0.194.2 (2025-03-24)

### Bug fixes

* fix: update dockerfile ([`7c76d45`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7c76d45541f56c13ebdfefe051ca1448f8329892))

## v0.194.1 (2025-03-22)

### Bug fixes

* fix: update dockerfile ([`d5d9956`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5d99568629b2a3ec6ef357bb59bc3e87031cfba))

## v0.194.0 (2025-03-20)

### Bug fixes

* fix: Dockerfile staticfiles dir ([`bd8562b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bd8562b0f70026fc4ab0f0ffd71b54b78568a9df))

### Features

* feat: Bump Versions, fix Dockerfile staticfiles dir ([`8d94cee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d94cee97ac1df8cb083d469d114503dd4010edf))

## v0.193.1 (2025-03-15)

### Bug fixes

* fix: tests ([`4d67a9a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4d67a9ae9e0c7848303a85f5246ee1ac75290ca5))

* fix: get_or_create_cart, github docker ([`4ae26a6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ae26a62eabc49a9bc74095be1c99d938f3ec308))

## v0.193.0 (2025-03-15)

### Features

* feat: Bump Versions, docker updates, lint and type updates ([`f45a7fc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f45a7fc316c1e7d6828543e18702f035ffbed56c))

## v0.192.0 (2025-02-22)

### Features

* feat: Bump Versions ([`5bb3b89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5bb3b899db2aa7be2f4606966e877b36567ad785))

## v0.191.0 (2025-02-15)

### Bug fixes

* fix: revert celery app ([`40c998a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/40c998ade7a4ddaec5727d80973e484aacd50fc5))

* fix: ruff ([`2c1c671`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2c1c671a501e5129d2ff3a22d0eb20e0462fa838))

* fix: ACCOUNT_AUTHENTICATION_METHOD rename and celery import ([`73782c9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/73782c932a123f890eb7353318fec5773a4e9d38))

### Features

* feat: Bump Versions ([`868e1b0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/868e1b03fe705b7635fa4faa9dbed2942cf5a48f))

* feat: Bump Versions, celery init change ([`2f7637e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2f7637e0c8a1e295fa7ad76424dd082552fcbdf0))

## v0.190.0 (2025-02-11)

### Features

* feat: Bump Versions ([`70eee48`](https://github.com/vasilistotskas/grooveshop-django-api/commit/70eee4895a121f0caa7e6f44cb4f519579c5fc49))

## v0.189.0 (2025-01-21)

### Features

* feat: Bump Versions ([`b6bafa8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b6bafa8460e1562e64911580e07b41998a9df30d))

## v0.188.0 (2025-01-13)

### Features

* feat: Bump Versions ([`8d8a4ee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d8a4ee67dada41e284375c78da45c25ecb8de11))

## v0.187.2 (2025-01-07)

### Bug fixes

* fix: add missing lib ([`59ff76f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/59ff76fbfe38ece61cfa6ac314197e3af7c2f208))

## v0.187.1 (2025-01-07)

### Bug fixes

* fix: add missing lib ([`a086f70`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a086f7025cf65ef74393fd61e87b5a9ab5a962ac))

## v0.187.0 (2025-01-07)

### Features

* feat: Bump Versions ([`d737557`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d737557440345257851d8302641bb5e98c822c85))

## v0.186.0 (2024-12-31)

### Bug fixes

* fix: poetry lock ([`3a20708`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a207084eac12749f802e32b22fcb1237d1203a3))

### Features

* feat: Bump Versions, replace flake8,black and isort with ruff ([`43eaa58`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43eaa58c25de243b15221695cf023256444d1eee))

## v0.185.0 (2024-12-08)

### Bug fixes

* fix: session test patch env values ([`5bc874d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5bc874d4a78624c409c1479754eeb709cfb901c2))

* fix: remove qodana ([`2d06f0b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2d06f0bbfc98c86e9be67028af26371bb875e4a6))

### Features

* feat: Remove gzip compression asgi, Cart improved, Cache improved, Bump Version ([`8386bf0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8386bf04a1692fea902f38790550890dded0a7d0))

## v0.184.0 (2024-12-04)

### Features

* feat: Bump Versions ([`8ee134f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8ee134f01478b07456cb3eac436a2e34457d62e3))

## v0.183.0 (2024-11-29)

### Features

* feat: bump meili version ([`f735bff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f735bffa27266e16fcf7cc27eb2d0801a126221b))

## v0.182.0 (2024-11-28)

### Features

* feat: Bump Versions, Improve `cart` app, remove `compress-old-logs` task and meilisearch queryset improve ([`be3c247`](https://github.com/vasilistotskas/grooveshop-django-api/commit/be3c2472b7056f1e8a4bd7a73f03d78a8a73f2b4))

## v0.181.0 (2024-11-22)

### Features

* feat: Bump Versions, remove `run.py` file, update `.dockerignore` and entrypoints for dockerfile ([`5beefe6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5beefe61eeae99f6e7484c7f3dfd7b538e9d73d9))

## v0.180.0 (2024-11-20)

### Features

* feat: Bump Versions ([`3aa27c4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3aa27c49c7a212342dbb95a9c32fbc15c453b227))

### Refactoring

* refactor(cache): `CustomCache` class improved ([`d387782`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d387782c63ce5f9638e6443489e8a5b81a8d6b6d))

## v0.179.3 (2024-11-16)

### Bug fixes

* fix(Dockerfile): attempt ([`0782a70`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0782a704f3cd8bb8e1ca152145f050e76c971347))

## v0.179.2 (2024-11-16)

### Bug fixes

* fix(Dockerfile): attempt ([`fdea712`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fdea7121f1c1d065da7f2d8f580297666f368247))

## v0.179.1 (2024-11-16)

### Bug fixes

* fix(Dockerfile): attempt ([`45e32c3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45e32c3685c2ed48ce7f48554b5229768ae374d6))

## v0.179.0 (2024-11-16)

### Features

* feat(ci): Updaste python to 3.13 and use `slim-bookworm` image ([`1986853`](https://github.com/vasilistotskas/grooveshop-django-api/commit/19868533704237d6055693331cdff2388261f7fb))

## v0.178.0 (2024-11-16)

### Features

* feat: Bump Versions ([`3e01ca3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3e01ca36eb11defe3f294e3f9a1b573b17a4f1a2))

## v0.177.0 (2024-11-13)

### Features

* feat: Bump Versions ([`0cf3d5f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0cf3d5f7407f1ba7eca7a9e239e45c160947f7f7))

## v0.176.0 (2024-11-09)

### Features

* feat: csp policy package `django-csp` ([`568d986`](https://github.com/vasilistotskas/grooveshop-django-api/commit/568d98692fbf6b69745926f7f21a6d8acfbeda05))

## v0.175.0 (2024-11-09)

### Features

* feat: Bump Versions ([`2371029`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2371029a266d8551d89b4906a0902bc496966eaa))

## v0.174.0 (2024-11-08)

### Features

* feat: API schema fixes, NonceMiddleware implemented rosetta base template override and Bump Versions ([`521e3f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/521e3f026303cded368391e09f4388e9a653d954))

## v0.173.0 (2024-10-26)

### Features

* feat: Robots txt added ([`ffc560c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ffc560c418330928cfbdeb50fe1872454d31706e))

## v0.172.0 (2024-10-26)

### Bug fixes

* fix: remove useless test ([`0d7e6a4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d7e6a45369ae24b0d6f7c0494a7279a884b23fe))

### Features

* feat: remove `unicode` and improve related blog posts ([`00b34e7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/00b34e7aec4c2894502acb62f34e257a4ca61d82))

## v0.171.1 (2024-10-26)

### Bug fixes

* fix: revert setting ([`fa6b715`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fa6b715b7fdd34877557c4b16a61aae0e10db8dd))

* fix: use TranslationsForeignKey in Translation models ([`bbe0b75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bbe0b75f068ab63e4c9177b9024b9c7677e8e77c))

## v0.171.0 (2024-10-25)

### Bug fixes

* fix: remove useless test `tearDown` ([`60a4531`](https://github.com/vasilistotskas/grooveshop-django-api/commit/60a4531feeb84bb29d5ffed460672c91917d5e1a))

### Features

* feat: related blog posts ([`578ae0d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/578ae0dac6d869afbde83fb930c0138e9939ab88))

## v0.170.0 (2024-10-24)

### Bug fixes

* fix: Slider factory title max length ([`80bcc12`](https://github.com/vasilistotskas/grooveshop-django-api/commit/80bcc12ba01dfdda5acfbb2ef5b8b0717d3a2e15))

* fix: Slider factory title max length ([`f2214ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f2214ff8d0c30d1fd8333ba754702636e53ffb13))

* fix: Slider factory title max length ([`66f9471`](https://github.com/vasilistotskas/grooveshop-django-api/commit/66f9471dd7c57fb28d03dd5c4f3aa0ead5ef7638))

### Features

* feat: Implement `MaxLengthFaker` and usage in slider factory ([`9bba342`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9bba342be5db77ced8afd6e95ea6ee5ba69e4cbf))

* feat: Bump Versions, lint fixes and implement `get_or_create_instance` helper method ([`f45d2da`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f45d2dafe9aa515305dab095c0f3585a3fd8bad7))

## v0.169.0 (2024-10-16)

### Features

* feat: Bump Versions ([`bb853f6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bb853f65bc539db6a111e44aeb2f71c28351b27d))

## v0.168.0 (2024-10-10)

### Bug fixes

* fix: lint fix ([`5f84aa6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5f84aa6542825d144bb19c84dffdb670e0407e17))

### Features

* feat: Bump Versions and lint fixes ([`931ec0f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/931ec0f86d2d4f5ba4caa09a798800a686e27621))

### Unknown

* Add github workflow file ([`7576a20`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7576a20e4c88e0fd8d23e1b44b055c1148197be3))

* Add qodana.yaml file ([`5e1a6e6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5e1a6e6b8f744e7c00491ec04e4eee057e9542e8))

## v0.167.0 (2024-10-08)

### Features

* feat: Bump Versions ([`cc1b469`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cc1b4696569c285f46cf7bbbd71cee7338026549))

## v0.166.0 (2024-10-03)

### Features

* feat: Bump Versions ([`2856495`](https://github.com/vasilistotskas/grooveshop-django-api/commit/28564953ddba2ad4bf1241af7e257d29477d7f6c))

## v0.165.0 (2024-10-01)

### Features

* feat: Bump Versions ([`01d929f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/01d929f360876241a9872a2c588b0d9952938d15))

## v0.164.0 (2024-09-24)

### Features

* feat: Bump Versions and remove useless template overrides ([`788cff6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/788cff6e2368dd74b6ec0466872ea2e2f8ed19bb))

## v0.163.1 (2024-09-22)

### Bug fixes

* fix: Include missing account email templates ([`d9dd09d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d9dd09dc6ba33cef1e205347f339e133cf56214c))

## v0.163.0 (2024-09-22)

### Bug fixes

* fix: ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED true ([`2c45dfe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2c45dfe9083ec162a85c50712424a45d817d0d16))

### Features

* feat: Bump Versions ([`1af8e15`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1af8e15c08d6c3c5a4da56207601292a94c796c0))

## v0.162.2 (2024-09-21)

### Bug fixes

* fix: remove unused `django.contrib.humanize` and CSRF_COOKIE_SAMESITE set to `lax` ([`c814b1e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c814b1e87e745591e30068cb49ba7e8b4c6cebe4))

## v0.162.1 (2024-09-18)

### Bug fixes

* fix: upload_image ([`f4bda52`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f4bda525c0910bc7efc732d61bd579f179961698))

## v0.162.0 (2024-09-18)

### Features

* feat: SECURE_SSL_REDIRECT from env ([`76210fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/76210fb21e67749e932d2e97755a570caa8a3532))

## v0.161.6 (2024-09-18)

### Bug fixes

* fix: revert / in admin url ([`0425a4b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0425a4bd2f5c10f59ce13acb2eab08c55c9fc5d7))

## v0.161.5 (2024-09-18)

### Bug fixes

* fix: production STATIC_URL and MEDIA_URL fix ([`5e0c650`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5e0c6506e2ba79947a60af3d1c7504cf5dec927f))

## v0.161.4 (2024-09-18)

### Bug fixes

* fix: SECURE_SSL_REDIRECT to false for now ([`c4bf5fd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c4bf5fddfb9e682df8da1f5d5b93354ba7f48fa5))

### Unknown

* feat; static for production ([`f396c98`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f396c98c076ca6567e416496f48b0a2837eba46b))

## v0.161.3 (2024-09-17)

### Bug fixes

* fix: poetry lock ([`b6dfe5d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b6dfe5d35c17494358bd1273205600b510de5779))

* fix: remove compressor ([`cb1a7cd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cb1a7cdb2bc814f0f65cbd173af8b43c5992a25a))

## v0.161.2 (2024-09-17)

### Bug fixes

* fix: csrf http ([`761c71b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/761c71b0b956fa430af8c0d81594d318068733af))

## v0.161.1 (2024-09-17)

### Bug fixes

* fix: remove useless tests ([`7a44e3e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7a44e3e4ebc6c3e5d9aee030a76aa1dbceae3967))

* fix: remove useless tests ([`d4f7006`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d4f700637a85cfa773c148cbcbfd25dbe6d7d17f))

* fix: whitenoise ([`c874dbd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c874dbd7b003a90a00df9b22adb9fc76bff81553))

## v0.161.0 (2024-09-17)

### Bug fixes

* fix: whitenoise test ([`ee827e6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ee827e62392a33b34f53d843aa3ced52198bde6c))

### Features

* feat: whitenoise ([`add9ab1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/add9ab1f315f0a4f69cc2f7b20aa57c83b38e76c))

## v0.160.1 (2024-09-17)

### Bug fixes

* fix: url static and media path ([`28fc35c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/28fc35c318dd1bfaffc1114fa08990a77ba8b8cf))

## v0.160.0 (2024-09-17)

### Features

* feat: set CSRF_COOKIE_SAMESITE to `None` ([`bce89e0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bce89e011fd0cd87743819c64da651665958eafd))

## v0.159.0 (2024-09-17)

### Features

* feat: Custom admin enable and remove django_browser_reload ([`35666fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/35666fb56e1de8f485efb9c54c03a9919ecec386))

## v0.158.1 (2024-09-17)

### Bug fixes

* fix: revert admin ([`04f130e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/04f130e4542a84c001e1f5bb1cc64589fa2d24ff))

## v0.158.0 (2024-09-17)

### Bug fixes

* fix: temporary ([`2ed504a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2ed504a378015e2282d8f0b37b39553cd89f51b0))

### Features

* feat: cache improvements and admin clear cache, ([`0188cac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0188caca7b66f48a04b10ff4a11cb557b3d6b2b6))

## v0.157.0 (2024-09-13)

### Bug fixes

* fix: run poetry lock ([`c5ff0c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c5ff0c0f5baced846d0c68c09938844827fefdf6))

* fix: run poetry lock ([`7bd01b6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7bd01b65f91783f1af26697445a601111794a51b))

### Features

* feat: Include `httpx` dep ([`20838ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/20838efa9b76e966683286bf7663fcd16c8369c1))

* feat: Bump Versions, health check API endpoint and `account/provider/callback` view implemented ([`108a2f1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/108a2f1982e72b8fb7e881ebb937c02ff9e33301))

## v0.156.2 (2024-09-09)

### Bug fixes

* fix: include missing API serializer fields ([`e7f858d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e7f858d65619b4f831736ffb6e794c1d55e580b8))

## v0.156.1 (2024-09-08)

### Bug fixes

* fix: add missing `change_username` api endpoint ([`d5336ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5336ffd649412a6532f03768f27f606331990ff))

## v0.156.0 (2024-09-08)

### Features

* feat: Bump versions ([`ec774d6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ec774d638974ed70767df2120ab79af13e3800d8))

## v0.155.0 (2024-09-05)

### Features

* feat: Bump versions ([`a5cde62`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a5cde62aaf902ccb8612a6344bbddb304b99d068))

## v0.154.1 (2024-09-02)

### Bug fixes

* fix: AWS usage in `upload_image` ([`65ee35a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/65ee35a0e4cf74acf80a8e8731fbe16375446642))

## v0.154.0 (2024-09-02)

### Bug fixes

* fix: update meili ([`9a1d329`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9a1d3292d7faca0c02f71515be08e0d39c3b7757))

* fix: update meili ([`7864452`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7864452faf9da68ae1e0310b89d11937f9f1cf1b))

* fix: update meili ([`d14ef4d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d14ef4d7d7ccb80a41e25b6ec2691cdeabe2c943))

* fix: update meili ([`441877f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/441877fee70b12f6bbe58d527e9335deb4b066b8))

* fix: update meili client ([`25b0ca9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/25b0ca9a62a197cd5f4d1dcf3f54899549b9f709))

* fix: update env ([`20eb3f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/20eb3f8dc8d1c7e0ecbe2a00ae3721529b221b30))

* fix: update ci.yml ([`7b4529f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b4529f2effbe84efdaefccce00050ddc95d208d))

* fix: update ci.yml ([`d8dc58f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d8dc58f84b51816a31c9d290381c5e6aad0631c8))

* fix: update settings ([`678cbab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/678cbab3dd2b4f4dc8612e41a71ecffc49d3575f))

* fix: fix meili ci ([`78dbc0d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/78dbc0d273d91aa8188b095b7390c5c24ead0c4f))

* fix: update default meilisearch host ([`16b37f1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16b37f1d4bb3428be558ce4548e73baa20bc8d29))

### Features

* feat: update ci for meilisearch ([`a63b47d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a63b47d33000e953444c84bbef68006453e4a0f4))

* feat: Meilisearch, remove `main_image_absolute_url` and `main_image_filename` and create a single `main_image_path` ([`f19b88e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f19b88ea83dd65d0a7e52e3f9537318411108be9))

### Unknown

* Update ci.yml ([`b659300`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b65930052bc1994cc7fafdc05c7fb74349c04af0))

* Update ci.yml ([`54d74b7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/54d74b721e058315bd954acdcfd9f67dde2d6c99))

## v0.153.1 (2024-08-29)

### Bug fixes

* fix: use env `USE_AWS` in upload_image method for tinymce ([`7b7dc0a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b7dc0a8237161c9fbb82ad941116a48d7cec29e))

## v0.153.0 (2024-08-29)

### Features

* feat: Bump versions ([`6efc135`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6efc1358c23faecb764659c296a3e04490fe0692))

## v0.152.1 (2024-08-27)

### Bug fixes

* fix: remove `docker` usage from env var `SYSTEM_ENV` ([`b3523f7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b3523f7e891fc3cea8dc39c00b9712e860ebc203))

## v0.152.0 (2024-08-27)

### Bug fixes

* fix: AWS usage configurable ([`2013f18`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2013f181e498389399499f2d4a6d6f16440c3ce9))

### Features

* feat: remove aws s3 first attempt ([`7ecaf6d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7ecaf6d51b482e293441a3f2d1ec30acd18cde19))

## v0.151.0 (2024-08-25)

### Bug fixes

* fix: test fix after logger update ([`6ed8fff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6ed8ffffa617459bbef5dede89014b9fea5519a1))

### Features

* feat: logger improve ([`16fed8f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16fed8f47706221ebbbd79de884cebe352ea6f59))

## v0.150.0 (2024-08-25)

### Features

* feat: django compressor for static files ([`52740f3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/52740f3a19bf2e0b87c325dfbe908ab2899a4d1d))

## v0.149.2 (2024-08-25)

### Bug fixes

* fix: minify styles css ([`a2956de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a2956de6491db4005749038d36577a53521b909f))

## v0.149.1 (2024-08-25)

### Bug fixes

* fix: correct CSS link in `account/base.html` and test ([`92943ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/92943ef3934bc6eb0b6b88c8fae39514d8a13d70))

* fix: production static file location fix ([`40f15fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/40f15fb94ff7d80f344ac7a3caf7acb439b45c6a))

## v0.149.0 (2024-08-24)

### Features

* feat: Added expiry_date field in `Notification` model, task and command to clear expired notifications and model indexes improvements ([`1ba77c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1ba77c00546d0f24e2a96c1809ce3d37fdd4d9c0))

## v0.148.0 (2024-08-23)

### Features

* feat: ManageTOTPSvgView update ([`aad0796`](https://github.com/vasilistotskas/grooveshop-django-api/commit/aad07965daa37e0bf526760aef85ff36eb5016bf))

## v0.147.0 (2024-08-22)

### Features

* feat: Improve API homepage UI, MFAAdapter get_public_key_credential_rp_entity override to set id ([`b26d5ba`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b26d5babf303e1ddbe75d61e38a2f0d53a9dd378))

## v0.146.1 (2024-08-22)

### Bug fixes

* fix: debug fixed ([`327793d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/327793d234f298eb8217d458411165ff222e50c7))

## v0.146.0 (2024-08-22)

### Features

* feat: Bump Versions and debugging ([`8ee4b1f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8ee4b1f557c50b5400ce6e1ff54fcce19970893e))

## v0.145.0 (2024-08-22)

### Features

* feat: USE_X_FORWARDED_HOST setting ([`1d185f5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1d185f502b27be978a9ae5a0180c1145e9852ea3))

## v0.144.1 (2024-08-21)

### Bug fixes

* fix: liker_user info at `notify_comment_liked` ([`aa2cb76`](https://github.com/vasilistotskas/grooveshop-django-api/commit/aa2cb76052af033b25671de510963ac9e733b38d))

## v0.144.0 (2024-08-21)

### Features

* feat: tasks for clear history. Bump Versions ([`93e43e2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/93e43e281c6a4fb9802122a1963574330704ba63))

## v0.143.0 (2024-08-21)

### Bug fixes

* fix: remove signal tests for a while ([`d696c62`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d696c62454dcfabb36f3dc16a7a80e47461ffd97))

* fix: run poetry lock ([`08f5cc3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/08f5cc3f85cd2d45dcafdb82604450851141acfd))

### Features

* feat: notification improvements ([`dbd05b5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dbd05b5e17fb739c13746cb120b25fe11533c5cb))

* feat: add `simple_history` and implement notify_product_price_lowered and post_create_historical_record_callback signals, Bump Versions ([`7b4a559`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b4a559c7e221ec7af4cfd0e89e1c9dc1b34839b))

## v0.142.0 (2024-08-18)

### Features

* feat: factory improvements, useless tests remove and API serializer readonly fields ([`920b449`](https://github.com/vasilistotskas/grooveshop-django-api/commit/920b449b74c1d5de04be8e339e92095be152020c))

## v0.141.0 (2024-08-17)

### Features

* feat: Bump Versions and minor fixes ([`759dd18`](https://github.com/vasilistotskas/grooveshop-django-api/commit/759dd180ff0f9c443e218ee5407ccd85a0edb331))

## v0.140.0 (2024-08-10)

### Features

* feat: Bump Versions, config moved to settings and websocket notification for user implemented ([`26c7115`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26c7115d23be359e6384659bb4c3066c4fc5de2b))

## v0.139.0 (2024-07-28)

### Features

* feat: Tag and TaggedItem factories implement ([`8b03ed7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b03ed7f44e5ed7b309078ec32498db782110a2c))

## v0.138.0 (2024-07-28)

### Features

* feat: More at Description

- Factory for model improvements
- Factory seed command refactor, `sync` and `async` commands available
- Bump Versions ([`6d7fdb0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d7fdb0bebe1dae633b4c4ff0462fd0a90c8f28b))

## v0.137.0 (2024-07-26)

### Features

* feat: More at Description

- Factories remove `_create` classmethod override and use `@factory.post_generation`
- Implement generic tag model and tagged item model
- Product tag and get tags API endpoint implemented ([`2b9c891`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2b9c891d1d1b7eb715993272aceb161215814f25))

## v0.136.0 (2024-07-20)

### Features

* feat: Bump versions ([`8395b02`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8395b020375ed1b9732a14d613b8cbfdc40b113f))

## v0.135.0 (2024-07-19)

### Features

* feat: Bump versions ([`c2b90d9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c2b90d9f15dea18a26f0d6a973b44ebf5c28a4c8))

## v0.134.0 (2024-07-18)

### Features

* feat: Bump versions ([`04bc57c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/04bc57c0b3322c64de561487b128ff21bfb53955))

## v0.133.0 (2024-07-15)

### Bug fixes

* fix: remove unused inline ([`f1ff735`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f1ff7354bcaf46258d7a17e36896cde1b5d1abc0))

### Features

* feat: model `related_name` and factory improvements, Bump Versions ([`a8ac195`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a8ac19523af1cb1e56b72a7ae5fcc2571ec16e98))

## v0.132.0 (2024-07-06)

### Bug fixes

* fix: test fix ([`e4fed90`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e4fed901626f2c6ce439838d4e386af5d1b86c80))

* fix: test fix ([`ee1d76b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ee1d76b29d83f0be1c099242fc50fd771f52f9dc))

### Chores

* chore: format files for linting ([`4cf0b88`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4cf0b88b56608ee93dee1169f077da00f993cf55))

### Features

* feat: Use factory boy for seeding and testing, logging improvements, `README.md` updated, Bump Versions ([`c445853`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c44585370f8623dc326025098a357bb895ff6099))

## v0.131.0 (2024-06-17)

### Features

* feat: Bump versions ([`863ba39`](https://github.com/vasilistotskas/grooveshop-django-api/commit/863ba3935d9c72f0a20f931e5ddf8ecefe50ae08))

## v0.130.0 (2024-06-05)

### Features

* feat: Bump versions ([`e0d728e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e0d728e1484b6ac4bb13db995a239143b6489e56))

## v0.129.1 (2024-06-04)

### Bug fixes

* fix: Remove useless ordering option ([`a2d2d60`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a2d2d6035ccff8d3f0ee9a07ff838b2c5db61bc4))

### Chores

* chore: `ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "email_confirm"` added ([`75a7547`](https://github.com/vasilistotskas/grooveshop-django-api/commit/75a75478cdaabc9b60e8a4079d056805385c126a))

## v0.129.0 (2024-06-04)

### Features

* feat: new model field `ImageAndSvgField` and usage in blog category model ([`7242345`](https://github.com/vasilistotskas/grooveshop-django-api/commit/72423458559f2fa893f06fbc49d4e75322a36336))

## v0.128.2 (2024-06-01)

### Bug fixes

* fix: wrong param in user signals remove ([`a40f173`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a40f1736a4c8e089cd53938263a296d4bf6e6c41))

## v0.128.1 (2024-06-01)

### Bug fixes

* fix: wrong param in user signals remove ([`4ecb355`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ecb35592889869dc4c4518319ca8438dd947337))

## v0.128.0 (2024-06-01)

### Bug fixes

* fix: remove useless tests ([`132db14`](https://github.com/vasilistotskas/grooveshop-django-api/commit/132db143191cb4e9f4b12a8f4787f2023931f009))

* fix: Update `poetry.lock` * ([`fa63b40`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fa63b403f4f8119886e36e29775f088f60c37944))

* fix: Update `poetry.lock` and `README.md` ([`746a2ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/746a2ed40c18df75915bba04fc35fdc1d9dda13f))

### Features

* feat: Bump Versions and remove useless tests ([`98ec380`](https://github.com/vasilistotskas/grooveshop-django-api/commit/98ec3808fa9949880d132a968262f32e66a07b37))

* feat: Bump versions, improve settings for all auth, implement SocialAccountAdapter and user all auth signals. ([`b192235`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b1922354a71e004b228ff112cfa3ad02c589bd80))

* feat: Bump versions ([`f421809`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f42180964e91854626fff40b8c0fbb424db3e000))

* feat: add "django.contrib.humanize" and app improvements ([`691d196`](https://github.com/vasilistotskas/grooveshop-django-api/commit/691d196ba495559c678cc6d7c8c486d223dc67b5))

* feat: remove `dj-rest-auth` and `django-otp` to use `django-allauth` new headless api ([`e51ccf2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e51ccf2b084a039b980b9046d871b3f5d9226ae2))

### Unknown

* Update README.md ([`03894b0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/03894b0fe1074cd81d94fa5637536725d5e36ce4))

## v0.127.0 (2024-05-25)

### Features

* feat: READEME.md ([`30e5403`](https://github.com/vasilistotskas/grooveshop-django-api/commit/30e5403f0dcd11ca00cd9773c80e705e1b7b60ee))

## v0.126.0 (2024-05-24)

### Bug fixes

* fix: Update poetry.lock ([`9ed3378`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9ed3378413000ad0980addaaf8fa2675d6c656f5))

### Features

* feat: Bump Version ([`558c682`](https://github.com/vasilistotskas/grooveshop-django-api/commit/558c6826bcbc4dcb55cfb12f1be4c609c5fd94f9))

## v0.125.0 (2024-05-19)

### Features

* feat: Contact form ([`f8ba7e3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f8ba7e3cd2704fd7d6fc160c4cfcd3e46ecf94b1))

## v0.124.2 (2024-05-18)

### Bug fixes

* fix: order API `retrieve_by_uuid` method ([`5445ea5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5445ea51afc8e866fadfd12f6675c4e23ddd0298))

## v0.124.1 (2024-05-18)

### Bug fixes

* fix: add ordering_fields at blog category posts ([`92c20ce`](https://github.com/vasilistotskas/grooveshop-django-api/commit/92c20ce2e7cc55babceb886013c4cae7d5159055))

## v0.124.0 (2024-05-18)

### Features

* feat: Bump Versions ([`5c9e163`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5c9e16387af89abf3ca3487d9eefdb4b1781a951))

## v0.123.0 (2024-05-16)

### Bug fixes

* fix: tests include username ([`69e7bd1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/69e7bd13dc0686b52be2acb56ddefb5b707b208b))

### Features

* feat: Be able to log in via username ([`14c41bc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/14c41bca9b2e81dbde058a7767cd42752c743b9d))

## v0.122.0 (2024-05-14)

### Features

* feat: Add MFA_TOTP_ISSUER setting ([`620905b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/620905bb8c49cf570ddfa037bdb361f7538be259))

* feat: Bump Versions ([`d49c1c8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d49c1c824c41ede911b91303bdb328a7a54d5f30))

## v0.121.0 (2024-05-13)

### Bug fixes

* fix: update poetry ([`148f94c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/148f94cde41f2000ea5c356a5801b5c8db9dc011))

### Features

* feat: Remove settings from core and use new library `django-extra-settings` ([`685c99a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/685c99a26129976f5ab012ff48ec8b7147f445e7))

## v0.120.0 (2024-05-13)

### Features

* feat: Bump Versions ([`77d93ea`](https://github.com/vasilistotskas/grooveshop-django-api/commit/77d93ea43e388d3b5c5d9c3b99821a0f72308c49))

## v0.119.0 (2024-05-07)

### Features

* feat: Add username change API endpoint ([`0e3ee78`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0e3ee782903b32b6b4f0a5c4457ef43971529508))

## v0.118.0 (2024-05-07)

### Features

* feat: Bump Versions ([`75d024a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/75d024aa588c4d34b15f03eea556a7235616f882))

## v0.117.0 (2024-05-07)

### Features

* feat: Bump Versions ([`2522e11`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2522e11d66f212536c46001b163f782c93a1a295))

## v0.116.1 (2024-05-06)

### Bug fixes

* fix: update `Notification` app urls ([`871294c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/871294c96f167dd4ef6ff6f6c548fa54566ec03b))

## v0.116.0 (2024-05-06)

### Features

* feat: Bump Versions ([`bfcc249`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bfcc249195d9c51c7691c72cd73a3c1eeaabe539))

## v0.115.1 (2024-04-27)

### Bug fixes

* fix: correct mfa test ([`a447c5d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a447c5d0933a86527f3338e491e995cc6cf68be0))

## v0.115.0 (2024-04-27)

### Bug fixes

* fix: pass the celery test for updated method ([`45f4ec2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45f4ec22b811e3f9fbd71dd5de66e08ff827fe93))

### Features

* feat: Dev env improvements, core urls.py cleanup and Bump Versions ([`6532f5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6532f5e0eb56195efe5c6724b552afdbb87214fe))

## v0.114.0 (2024-04-26)

### Features

* feat: Remove `app` module, move `settings.py` in root ([`c773a3b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c773a3b21446a0ad0135fb89a59c31ecc30b292c))

## v0.113.0 (2024-04-26)

### Features

* feat: Add username for user account ([`456db5a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/456db5a32c7c8571e2e4a42a0ed73e43017ece2e))

## v0.112.2 (2024-04-25)

### Bug fixes

* fix: docker dev env pgadmin initial server works ([`82c0605`](https://github.com/vasilistotskas/grooveshop-django-api/commit/82c0605c8a17e9b0681122298a9cee52af6fe87b))

## v0.112.1 (2024-04-25)

### Bug fixes

* fix: minor admin improvements and set `INDEX_MAXIMUM_EXPR_COUNT ` to 8k ([`d1c702c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d1c702cffd48f31fac64735e8a571d1bcdff36d3))

## v0.112.0 (2024-04-25)

### Features

* feat: Add caching in search results API ([`0fe6179`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0fe61795e7fffa1da8226e4383d5c65fba3c5f11))

## v0.111.0 (2024-04-25)

### Features

* feat: Improve text preparing for search fields ([`7db90cd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7db90cd8f13fe561cf1ed4d8e6d09cc2fe30f394))

## v0.110.3 (2024-04-25)

### Bug fixes

* fix(prepare_translation_search_vector_value): remove existing html from fields ([`829862d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/829862d1baed889a7849331eadd330ae6fb334df))

## v0.110.2 (2024-04-25)

### Bug fixes

* fix: Add output_field in `FlatConcatSearchVector` ([`d0b204a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0b204a249cd1d7a5aa3fdd785f0aa7838dd86cb))

## v0.110.1 (2024-04-25)

### Bug fixes

* fix: `prepare_translation_search_document` and `remove_html_tags` fix ([`1f61bb9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1f61bb96e868d655c2faa00d1aeaca231b3124a5))

## v0.110.0 (2024-04-24)

### Features

* feat: Search vectors and docs add to blog post and improve whole search concept, Bump Versions ([`de952ad`](https://github.com/vasilistotskas/grooveshop-django-api/commit/de952ad31b029f090ea7c8414ad7fbcfb1a40e90))

## v0.109.0 (2024-04-23)

### Features

* feat: Bump Versions and add throttling in some API endpoints ([`fc49b91`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fc49b91773fc7356e6b8014ace09befd11c0e856))

## v0.108.0 (2024-04-22)

### Bug fixes

* fix: Remove useless model verbose name tests ([`7cae556`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7cae55669481245413339039c9404a5bb7d0c55d))

### Features

* feat: Default lang `el` by default ([`e492900`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e4929006cf0ef7626fc27ec27813c5ebd5c6153b))

## v0.107.0 (2024-04-21)

### Features

* feat: Add cache disable from env options ([`b26b7c6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b26b7c6397eb566086cc67edbba957f6438da3e0))

## v0.106.1 (2024-04-20)

### Bug fixes

* fix: title ([`d30fa30`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d30fa3066a8d8ee7ea786814c2c0899a06e5b54e))

## v0.106.0 (2024-04-20)

### Features

* feat: Task improvements new API endpoint `liked_blog_posts` and favicons replaced ([`7738aa1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7738aa105bef036439a2343f8f77134c17b55175))

## v0.105.0 (2024-04-18)

### Features

* feat: Bump Versions ([`578e178`](https://github.com/vasilistotskas/grooveshop-django-api/commit/578e178afcf0627e61b824d7b53c91d55f86379e))

## v0.104.1 (2024-04-17)

### Bug fixes

* fix: Remove useless validators from models ([`bb36840`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bb3684017eeea109c96f1fc87c480c611d26e055))

## v0.104.0 (2024-04-17)

### Features

* feat: Bump Versions ([`19e3f86`](https://github.com/vasilistotskas/grooveshop-django-api/commit/19e3f86c4ff1b897c9f944975be416247c96e21c))

* feat: Bump Versions ([`ae033d2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ae033d2789cd2e262d148e39617ebdbcfbf40a24))

## v0.103.0 (2024-04-16)

### Bug fixes

* fix: remove useless stuff and bump `dj-rest-auth` version ([`26639a1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26639a10e59098c57b1ff9178188f06b1ff09aff))

### Features

* feat: Hardcoded values from env ([`39dfbb2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/39dfbb249a46f68ea14a3d25393df5ca498e03d2))

## v0.102.1 (2024-04-15)

### Bug fixes

* fix: hide some urls in production ([`ed2628a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ed2628a9f3048c3a3eadc6fda7f6a4e2037fd98c))

## v0.102.0 (2024-04-15)

### Features

* feat: Bump Versions ([`48adc32`](https://github.com/vasilistotskas/grooveshop-django-api/commit/48adc32c2628a6fd74047892e6294587278bf4a1))

## v0.101.0 (2024-04-13)

### Bug fixes

* fix: Update test for lib update ([`b787ca1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b787ca1e276218075f368b2b79b48a11d2cbceca))

### Features

* feat: New API endpoints and improvements ([`a3deae2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3deae2d22e7cc94a9040794c787551c92c2c81c))

## v0.100.0 (2024-04-10)

### Features

* feat: Bump Versions ([`ebf9cb9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ebf9cb98c2cdbfc6fc1d53ae75d1d56a507e6c0e))

## v0.99.1 (2024-04-10)

### Bug fixes

* fix: update hardcoded configs ([`340bb2b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/340bb2bd35a961f4f597bb102ffc33e2d03b5726))

## v0.99.0 (2024-04-08)

### Features

* feat: Bump Versions ([`d788413`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d788413566e319205111d164b88eec85bf255a75))

## v0.98.0 (2024-04-07)

### Bug fixes

* fix: remove echo from ci.yml ([`e87f90d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e87f90d10ae06751b3133385ae3b6410835170ee))

* fix: Use pipx in ci.yml ([`0776184`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0776184c8c0c07e3a0531d0c682eef5b64994396))

* fix: Bump poetry version ([`2cb1741`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2cb17416c67c5a11f924b0080c0d438cf32a31ef))

### Features

* feat: Update schema.yml, make locales and fix `Echo` in ci.yml ([`801d787`](https://github.com/vasilistotskas/grooveshop-django-api/commit/801d787e49287b6d768b8af2315e807390feb1f6))

* feat: Bump github workflow ci.yml versions and poetry version bump ([`2d6ce52`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2d6ce5216902cdde8246fab21c3ab11c73fffc69))

## v0.97.0 (2024-04-06)

### Features

* feat: serializers expand `BaseExpandSerializer` and filter language ([`e361373`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e36137368b4bee9c6531ea52b0067c6b8ce2bf85))

## v0.96.0 (2024-04-04)

### Features

* feat: Blog post comments / replies and likes ([`7ed0be3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7ed0be33fd1fba35442c5eeaeeeede920b59b79f))

## v0.95.0 (2024-03-31)

### Features

* feat: Index fields to improve performance ([`3eb3ea1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3eb3ea1c36eb59e1b337ef3e6ad12f3a6624a096))

* feat: blog category posts API endpoint and code cleanup ([`c28727e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c28727e0f60ecc5a809cd1a6b5430fe495061f52))

## v0.94.1 (2024-03-29)

### Bug fixes

* fix: Product API view fix ([`fac6862`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fac6862e3753a847ed46b49cbf5034927caa8dd3))

## v0.94.0 (2024-03-29)

### Features

* feat: Paginators remove, now have option to ask for a pagination type from query string, useless extented api view methods removed ([`49bf512`](https://github.com/vasilistotskas/grooveshop-django-api/commit/49bf512825d8d1066a11cd7c6a4669961f84e38a))

## v0.93.1 (2024-03-27)

### Bug fixes

* fix: tests fixed ([`850b0ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/850b0ffdb296993c861f17757f47bb78bd5cc658))

* fix: Fix populate commands, search improve and fix model image_tag methods ([`b2ebfa7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b2ebfa70536420efc974ae59efe2b26e909b9afc))

## v0.93.0 (2024-03-25)

### Features

* feat: search improve and Bump Versions ([`8a7e2ad`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8a7e2adf7105e10dd6b87e2256d3204484b681d9))

## v0.92.0 (2024-03-25)

### Features

* feat: search improve ([`43976f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43976f0650aeee9f94497c21997c460c474cc687))

## v0.91.0 (2024-03-24)

### Bug fixes

* fix: update search test ([`21ad6f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/21ad6f0931275fbbf6dd9f6eb5a97aa7c0a9c4f7))

### Features

* feat: Bump Versions and search improve ([`85626f7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/85626f7103362d709ffcbfb7bb45ab7bc3c52027))

## v0.90.0 (2024-03-23)

### Features

* feat: Task updates and Bump Versions ([`e2387f5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e2387f5d6c87c0a5ebb5ef9699c4c7e7a6c950d5))

## v0.89.0 (2024-03-22)

### Features

* feat: Bump Versions ([`1058369`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1058369bd91b671a9e719fb75cff65dc6b2a9c89))

## v0.88.0 (2024-03-21)

### Features

* feat: Bump Versions ([`01f4fd0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/01f4fd03dbf02a514d6277bd5938400ccea63a4a))

## v0.87.0 (2024-03-19)

### Features

* feat: Bump Versions and remove timezone middleware ([`d44c9de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d44c9defb5b03f48607a02f0a82aad214d4fc31c))

## v0.86.0 (2024-03-17)

### Features

* feat: Cache some api views ([`81daec1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/81daec1ce5807bdc051357cbb1fe19fcc941e26b))

### Unknown

* fix:(registration.py): Not verified email case ([`76960cf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/76960cf22b1403454a126a3c36a0b0664fec9a4f))

## v0.85.1 (2024-03-16)

### Bug fixes

* fix(Dockerfile): remove ` postgresql-dev` lib ([`ce7c3e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ce7c3e8454106c4a9b757348e9780f851eda5bf9))

## v0.85.0 (2024-03-16)

### Bug fixes

* fix(test_view_search): fix ([`cbb7ef7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cbb7ef7bad71b28aea3ce969f1e5d995f2b76880))

### Features

* feat: New celery tasks ([`f8df788`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f8df788e43b2cc92c9cee357df8ad04d0e9cbb19))

## v0.84.0 (2024-03-11)

### Bug fixes

* fix: decode query for search after validation ([`b783bbb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b783bbb41b808517bc90c2bfbea795c5250279fb))

### Features

* feat: search improvements ([`f98d033`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f98d03391342a1fcde1a016680d7b56f50b07387))

## v0.83.0 (2024-03-09)

### Features

* feat: Search and Celery Task improvements ([`13f2fb3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/13f2fb35df9dfff0ab285896054f9cff7059d10e))

## v0.82.0 (2024-03-09)

### Bug fixes

* fix: `test_models` Database access not allowed fix ([`1db493b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1db493b1a2a15a06b83595687451a4f52860f32b))

### Features

* feat: Measurement implemented and Bump Versions ([`e1c7d79`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e1c7d79919c25b09ff0fc36d75660f9cb8aa40a3))

## v0.81.0 (2024-03-08)

### Features

* feat: New api endpoint to get product images ([`aa87bcf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/aa87bcf78dd8b578fdd0e8414a317f002eb83d49))

## v0.80.0 (2024-03-08)

### Features

* feat: New api endpoint to get product review and Bump Versions ([`78fc042`](https://github.com/vasilistotskas/grooveshop-django-api/commit/78fc042953ce86a5ec321c89d10f6d07b8f0b3ad))

## v0.79.0 (2024-03-07)

### Features

* feat: `blog_post_comments`, `blog_liked_posts` and `blog_liked_comments` added in user details API view, get blog article comments api endpoint and method names correction ([`7feb1d9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7feb1d90bfa69b9f33eefcd9c8d139e33c6fdada))

## v0.78.0 (2024-03-07)

### Features

* feat(Dockerfile): Bump python version to 3.12.2 ([`80d1a5f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/80d1a5f023c9818454a113f9b76a46d701276de2))

## v0.77.0 (2024-03-05)

### Bug fixes

* fix: remove comments from tests package ([`2da561a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2da561a096f4352a9eae1f80baa2b91c992199ce))

### Features

* feat: `clear_blacklisted_tokens_task` task, `user_to_product_review` rename to `user_product_review` and config package simplify ([`906f845`](https://github.com/vasilistotskas/grooveshop-django-api/commit/906f84537033ebc60e2d118efdafcc9682f4fac6))

## v0.76.0 (2024-03-03)

### Features

* feat: Bump Versions

- Django 5.0 and django-celery-beat 2.6.0 ([`069b838`](https://github.com/vasilistotskas/grooveshop-django-api/commit/069b8384376da556fea256f2adcad15921a4dc90))

## v0.75.0 (2024-03-01)

### Features

* feat: introduce `rest_framework_simplejwt.token_blacklist` ([`7653430`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7653430c95beced7d8875f35003b0ee87cb0715a))

## v0.74.0 (2024-03-01)

### Features

* feat: `user_had_reviewed` method change to `user_to_product_review` and change user addresses model ordering Meta field ([`a5ebf0e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a5ebf0eb77ed330f1d66a7cac2ad0e91b757de79))

## v0.73.0 (2024-02-26)

### Features

* feat: Bump Versions ([`12dfecd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/12dfecde77567f772388027f1026486e924dbf44))

## v0.72.0 (2024-02-24)

### Features

* feat: Auth user account API improved ([`faf8712`](https://github.com/vasilistotskas/grooveshop-django-api/commit/faf87122d228e72fe3e503da3d02bb2b46beff1a))

## v0.71.0 (2024-02-17)

### Features

* feat: Bump Versions ([`9600e92`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9600e92491efdfda93d192fd5f5869329841bba9))

## v0.70.0 (2024-02-16)

### Features

* feat: API serializers with `get_expand_fields` imports using `importlib`, BaseExpandSerializer logic improvement, BlogComment model use `MPTTModel`. ([`62d9591`](https://github.com/vasilistotskas/grooveshop-django-api/commit/62d9591600a1fe22dfe41e21ded1c96ecfc00a6a))

## v0.69.0 (2024-02-14)

### Features

* feat(upload_image): save img to S3 storage on production ([`d7a5207`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d7a520703c2ad535e3a6dacb0aeabac6e63a68ed))

## v0.68.0 (2024-02-14)

### Bug fixes

* fix: `test_model_product_category` absolute url test ([`af99e61`](https://github.com/vasilistotskas/grooveshop-django-api/commit/af99e611ae7a36751b4c20d0d104b87f8a7bc8c3))

* fix: `test_model_product_category` absolute url tests ([`664dcca`](https://github.com/vasilistotskas/grooveshop-django-api/commit/664dcca700a589f04d740554a8895740382f6a11))

### Features

* feat: `ProductFilter` method `filter_category` include descendants , `ProductCategory` absolute url fix ([`bb90d4f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bb90d4f4e1558ab3ab273352d6009621a9ca484c))

## v0.67.0 (2024-02-13)

### Features

* feat: Django tinymce be able to upload image in `HTMLField` tinymce ([`e2ed8d0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e2ed8d07ef86210e63f5f37d8f0e0385719db616))

## v0.66.0 (2024-02-12)

### Features

* feat: Settings application and Bump Versions

- New Settings application to set single key value setting for the application.
- Tests for new settings app implemented.
- Bump Versions ([`4501e8d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4501e8d7bd4d49afbb8ada133ad5b13be5ce57ab))

## v0.65.0 (2024-02-09)

### Features

* feat: Bump Versions ([`2bb5ecb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2bb5ecbca0d7bed2195151c82325d5182ffadb54))

## v0.64.0 (2024-02-06)

### Bug fixes

* fix: run `poetry lock` ([`d459749`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d459749bcb6eab543a7df02a7500fdb2e78c44ab))

### Features

* feat: Bump Versions and new features ([`16f5709`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16f5709ca55bff44fe9fa151f877974b58fc66c6))

## v0.63.0 (2024-01-23)

### Features

* feat: Bump Versions ([`695da1e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/695da1e14899b9f389aaa05bcc145dc8dc992fc6))

## v0.62.0 (2024-01-16)

### Features

* feat: Bump Versions ([`0befa3a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0befa3aa30721dfeb0f50914324d338d1d3d7a82))

## v0.61.0 (2024-01-08)

### Features

* feat: Bump Versions ([`54c8138`](https://github.com/vasilistotskas/grooveshop-django-api/commit/54c8138b22f9af93cfc4fb081a945f9a70e07adc))

## v0.60.0 (2024-01-03)

### Features

* feat: Bump Versions ([`b6623dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b6623dc58b21fb8453347fd1b88fa40c9f6c2130))

## v0.59.1 (2023-12-29)

### Bug fixes

* fix: `test_storage` fix to pass previous commit ([`c0cea9e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c0cea9e85bd965264dfdc4b6ded4a8d1f9f90ab2))

* fix(storage.py): change staticfiles class ([`152974e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/152974eb385c1e932d0e54204c23d2c31f1f8128))

## v0.59.0 (2023-12-28)

### Features

* feat: Bump Versions and add `absolute_url` in blog post ([`50bf02a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50bf02a69e01c5e7c25ab98bfc6bbc2ee62f07c1))

## v0.58.0 (2023-12-24)

### Features

* feat: improve `BaseExpandSerializer` and versions bump ([`7b4f13e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b4f13e9e6f465115af677284707a7b408496222))

## v0.57.1 (2023-12-23)

### Bug fixes

* fix(OrderCreateUpdateSerializer): validate and create methods ([`b11aa31`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b11aa3170476e0d512769476cf8861a5a7926efb))

## v0.57.0 (2023-12-19)

### Features

* feat: Bump Versions ([`dd8cbcd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dd8cbcde037d792291a3f19862a8376037a841b1))

## v0.56.0 (2023-12-15)

### Features

* feat: Add `multidict` ([`2dcd119`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2dcd11913f42fb9d00d0e5f68b763adeea3c21bc))

## v0.55.5 (2023-12-15)

### Bug fixes

* fix(Dockerfile): Add `build-essential` ([`a1fb346`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a1fb3466aacdaeb3d951aed0f002cb9142287e2d))

## v0.55.4 (2023-12-15)

### Bug fixes

* fix(Dockerfile): Remove ` libmusl-dev` ([`f419494`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f4194944f7da88270c85cb3f9297e41af577c038))

## v0.55.3 (2023-12-15)

### Bug fixes

* fix(Dockerfile): use `apt-get` ([`6a82c76`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6a82c76c51d1d0a64bb179e27d0454c18bb65d11))

## v0.55.2 (2023-12-15)

### Bug fixes

* fix(Dockerfile): `groupadd` fix ([`f0d52f4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f0d52f48776211917a84df780f9cb5045720dfaa))

## v0.55.1 (2023-12-15)

### Bug fixes

* fix(Dockerfile): use apt-get ([`2526e4b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2526e4bed4578af5e8aa0f408105e11d8210cdf6))

## v0.55.0 (2023-12-15)

### Bug fixes

* fix(Dockerfile): Use `python:3.12.1-slim` ([`4dd97ac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4dd97accbe0937c11a7dd9d7b52e3b4b3dfa423e))

### Features

* feat: Bump Versions ([`90f3c97`](https://github.com/vasilistotskas/grooveshop-django-api/commit/90f3c97b6fbc3324331cb5e92332a0dbad754ba9))

## v0.54.0 (2023-12-12)

### Features

* feat: Bump Versions ([`b1e0351`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b1e03519819423f968fc2c02eb5e3f2636e68218))

## v0.53.0 (2023-12-08)

### Features

* feat: Django 5 ([`ce3827a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ce3827ac91e491750f0a2a99857ab477576888e1))

## v0.52.1 (2023-12-08)

### Bug fixes

* fix(ci.yml): Remove hardcoded omit and pass the `.coveragerc` file ([`a67886b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a67886b4fd8db15f8f5c700c4f6bfd64817b2afd))

## v0.52.0 (2023-12-08)

### Features

* feat: Bump Versions ([`262ea57`](https://github.com/vasilistotskas/grooveshop-django-api/commit/262ea5726cda6d4d636dd51bd19408d4b0df318b))

## v0.51.0 (2023-12-07)

### Features

* feat: New tests and some config fixes ([`74fb54f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74fb54f0d79e4b68043e88078118515c9cecb624))

## v0.50.0 (2023-12-06)

### Bug fixes

* fix(config/logging.py): Create the directory if it does not exist ([`4c486bf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4c486bfbfe82de665e3571aa4f7eedd9ddb02cf7))

### Chores

* chore: Delete logs/celery.log ([`edab86e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/edab86e014733dc5f1b495d1678732babf310628))

### Features

* feat: Logger improve and task to remove old logs ([`b7db338`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7db338c052da4700178e8c9514b0b96195fcb9e))

## v0.49.1 (2023-12-06)

### Bug fixes

* fix: Reduce product pagination `default_limit` ([`04b1e9e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/04b1e9e2cb883edbd93d56aeba853853fba1599b))

## v0.49.0 (2023-12-05)

### Features

* feat: reduce `MoneyField` max_digits and decimal_places and tests fix ([`2ef6ef4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2ef6ef41bf61828be5ace62e547097940efec50b))

* feat: Bump Versions and remove `SessionAuthentication` from `DEFAULT_AUTHENTICATION_CLASSES` config. ([`5a579a0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5a579a0db717381c54b8ebaa9e3ed5bca2a544b5))

## v0.48.0 (2023-12-03)

### Features

* feat: `GITHUB_WORKFLOW` env `SYSTEM_ENV ` var rename to `ci`, remove session endpoints and versions bump ([`fb24b1b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fb24b1bcc0b16d92ec33e680d373c2e61429ae7d))

## v0.47.6 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix and new config env vars. ([`0415f6b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0415f6b2f4f81d260c5d47f0511734e489f0e94a))

## v0.47.5 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix. ([`1c94295`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c9429588266d5b74df689a25e0622baef348628))

## v0.47.4 (2023-11-24)

### Bug fixes

* fix: set TINYMCE_COMPRESSOR to false ([`79fdfa3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/79fdfa3231c3cc6a93d38f72cbf19f6d8187e437))

## v0.47.3 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix. ([`e30b08a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e30b08ab32fd564a2e10a92a57b3c843a93dc455))

## v0.47.2 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix. ([`fcf0b43`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fcf0b434515141adbd4a5bb0a2d173be261edece))

## v0.47.1 (2023-11-24)

### Bug fixes

* fix: Bump tailwind version and add `--minify` flag ([`b8d4f49`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b8d4f49a0ba39cd9911c25cbac32775a645e5397))

## v0.47.0 (2023-11-23)

### Features

* feat: Bump Versions ([`3534020`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3534020a1933e058fd3fee85aaa3dc439cc6fe69))

## v0.46.4 (2023-11-21)

### Bug fixes

* fix: increase DEFAULT_THROTTLE_RATES for production ([`e7ad5fd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e7ad5fd70eea439b5771785877354407e146e4ed))

## v0.46.3 (2023-11-21)

### Bug fixes

* fix: debug toolbar check ([`6585a76`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6585a761dfeff6fc09e4c0442f2d948aaeae587b))

## v0.46.2 (2023-11-21)

### Bug fixes

* fix: ALLOWED_HOSTS fix ([`8fb3a7b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8fb3a7b51d4eb2c579a80d6cf8c6635364c3bcc0))

## v0.46.1 (2023-11-21)

### Bug fixes

* fix: `APPEND_SLASH` default to false ([`bcfb5e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bcfb5e81284232aba1c1963873a0651f50aa201b))

## v0.46.0 (2023-11-21)

### Features

* feat: Remove trailing slash from all api end point urls ([`22d72de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/22d72de95c353249633e300d4da6eceac98f99dd))

## v0.45.5 (2023-11-21)

### Bug fixes

* fix: add`ALLOWED_HOSTS` ips ([`00611bb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/00611bbb053cc3fbb9d9371da2cae12379154a17))

## v0.45.4 (2023-11-21)

### Bug fixes

* fix: fix `ALLOWED_HOSTS` missing coma ([`2339a29`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2339a29d786bd84e58ad11e880d2eddf36be5a94))

## v0.45.3 (2023-11-21)

### Bug fixes

* fix: update config base.py ([`7550aac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7550aacfe9b3490feb3484c449dfbab30dcca3b6))

## v0.45.2 (2023-11-21)

### Bug fixes

* fix: celery import fix, ALLOWED_HOSTS fix. ([`91d6216`](https://github.com/vasilistotskas/grooveshop-django-api/commit/91d62169da19a6808d72e7c23e392f51b54a259c))

## v0.45.1 (2023-11-19)

### Bug fixes

* fix: config fix ([`c6cb058`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c6cb058b1e88de001d5bd204e3a7d9ee6db70d85))

## v0.45.0 (2023-11-19)

### Bug fixes

* fix: poetry update ([`629a97b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/629a97bf828138caa641b80d1c42c97f001bd156))

### Features

* feat: versions bump and update bucket ([`dea38a3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dea38a3eab011b78e5b1de5a0ca4c94e3a4476d5))

## v0.44.3 (2023-11-17)

### Bug fixes

* fix: config security fixes ([`fe64d98`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fe64d9814f90cc86ea1a52656d689512d4e60a31))

## v0.44.2 (2023-11-17)

### Bug fixes

* fix: remove `SessionTraceMiddleware` ([`476e9a3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/476e9a3c3c60269f80a37dd507bcd7d218ab7b04))

## v0.44.1 (2023-11-17)

### Bug fixes

* fix: add `websockets` lib ([`8f7064c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8f7064c42e5fa97b9a990679bc4071f64416905a))

### Chores

* chore: lint ([`456b35a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/456b35afd4c92fd263fa1166f355f9ae27accdab))

## v0.44.0 (2023-11-17)

### Features

* feat: clear all cache task and command, `ATOMIC_REQUESTS` set True, and run app with uvicorn. ([`becd1b5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/becd1b58630d58d586488dbea8e244e43aada4b8))

## v0.43.2 (2023-11-16)

### Bug fixes

* fix: move settings to db ([`bd3f6a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bd3f6a89c603840d7d5c6fe6b68e4cfec00eb5ea))

## v0.43.1 (2023-11-16)

### Bug fixes

* fix: default debug `True` revert for now ([`e4f0953`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e4f0953cba2605c4b4beeb207142dde4e590e5c5))

* fix: enable `ATOMIC_REQUESTS` and added `CONN_MAX_AGE` to 60 sec ([`da825ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da825ef0309e210c573ed0b2eb904b11a0e6eb6b))

* fix: debug default false ([`61a9aad`](https://github.com/vasilistotskas/grooveshop-django-api/commit/61a9aaddc66b08347519a1cccddd785dc3506c8e))

## v0.43.0 (2023-11-16)

### Bug fixes

* fix: remove useless test ([`7ffd294`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7ffd294d8002182e8f83ca3fe9e97e26c1d3c95d))

### Features

* feat: Versions bump and new tests ([`c075648`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c0756486d6cdba902045451582d10c0e86cd80fb))

## v0.42.0 (2023-11-14)

### Features

* feat: Bump Versions ([`94f7fa3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/94f7fa35e0d3cf7f70d162601d28516d028cb6f4))

## v0.41.0 (2023-11-13)

### Features

* feat: Versions bump ([`26b2332`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26b23323e4223645229d33ca7f5df0445640be2c))

## v0.40.5 (2023-11-12)

### Bug fixes

* fix: `cors.py` config origins ([`4320cda`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4320cda1ba45674b2d586562f421971852bcbc9f))

* fix: `csrf.py` config origins ([`98a1751`](https://github.com/vasilistotskas/grooveshop-django-api/commit/98a1751995818eb9489f3697cb4cb2885eac4bf2))

* fix: update config ([`2cb190f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2cb190fb17b4316574868fb1a60fe3aab0d562ba))

### Chores

* chore: Delete .idea directory ([`a29459b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a29459b5a0f42f87df6b09c6431e6b23cea7753b))

## v0.40.4 (2023-11-09)

### Bug fixes

* fix: redis url remove from env, versions bump, add new `ALLOWED_HOSTS`. ([`01aa977`](https://github.com/vasilistotskas/grooveshop-django-api/commit/01aa977284dea55cac8f9949f67bb0ece49dc0e3))

## v0.40.3 (2023-11-08)

### Bug fixes

* fix: config improve and remove useless stuf. ([`dd26ba5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dd26ba56a5e408639bbe340f0cb0003e04cb3c2b))

## v0.40.2 (2023-11-05)

### Bug fixes

* fix: session middleware `update_session` wrap with try catch and versions bump. ([`21d798e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/21d798eab75db750906debf59a6b099ed4f29e39))

## v0.40.1 (2023-11-04)

### Bug fixes

* fix: `.env.example` revert, `.gitignore` fix. ([`2a5200a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a5200a763b0c46812130087999a578b0e26fd5b))

* fix(.gitignore): Only include `.env.example` ([`f80ac08`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f80ac0871f9418d9f907a75717dd0cc12d79c647))

### Chores

* chore: remove env files. ([`fdccd2e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fdccd2ee1cc5cf7e45d4826fb78620ad90effffe))

## v0.40.0 (2023-11-03)

### Features

* feat(config): Added `NGINX_BASE_URL` . ([`cde1bcc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cde1bccc15ad5e814c02ac7f1f42a54629e8be85))

## v0.39.9 (2023-11-03)

### Bug fixes

* fix: Include `qrcode` library. ([`53750e0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/53750e03ad24aeb112a6f629954de73450505e52))

## v0.39.8 (2023-11-03)

### Bug fixes

* fix: Include `httptools` library. ([`addff50`](https://github.com/vasilistotskas/grooveshop-django-api/commit/addff5023825e3ee00b187954f0ddc7f3b9b5e44))

## v0.39.7 (2023-11-03)

### Bug fixes

* fix: Include `uvloop` library. ([`45290ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45290ffa6b0e81d5125fcd82b63581a9af559a8b))

## v0.39.6 (2023-11-03)

### Bug fixes

* fix: Added `phonenumbers` package. ([`e6eeb8e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e6eeb8eee0c682efaf5039c45aaa931cbf00e565))

## v0.39.5 (2023-11-03)

### Bug fixes

* fix(Dockerfile): trying to work with psycopg ([`b4c353d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b4c353d314e3743242762806f35d7523f5512de6))

## v0.39.4 (2023-11-03)

### Bug fixes

* fix(Dockerfile): fixed ([`45391b6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45391b6081c1515af9459b9a08b697f957220cb3))

## v0.39.3 (2023-11-03)

### Bug fixes

* fix(Dockerfile): Fixed. ([`f4b1d3f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f4b1d3f48a9ae36b0a55f787b9406c16984fa979))

## v0.39.2 (2023-11-03)

### Bug fixes

* fix: Dockerfile fix, djang0 fix to django ([`dedb407`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dedb40732aaf5e49180646d8f106a23f480685cf))

## v0.39.1 (2023-11-03)

### Bug fixes

* fix(Dockerfile): Add empty logs file. ([`7893a7b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7893a7b19e868108c509cadaf1fa20fdc752a407))

## v0.39.0 (2023-11-03)

### Features

* feat: More at `Description`

ci github actions test using pytest.
- asgi and wsgi refactor and improved.
- versions bump. ([`a761994`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a7619946e1487c6218fe285c1a876f08ff5c8512))

## v0.38.0 (2023-10-31)

### Bug fixes

* fix: update `poetry.lock` ([`98e509a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/98e509a1098758efe30ef1077c8225682fbe687d))

### Features

* feat: Versions bump, `asgi.py` router improved. ([`9090c28`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9090c28ec3babf9a60cd3845b3364d6d16e3fc45))

## v0.37.4 (2023-10-31)

### Bug fixes

* fix: celery settings update, caches.py update, Dockerfile remove lint step. ([`208471a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/208471a2b8fb2fcbb8976db383839db182585576))

## v0.37.3 (2023-10-31)

### Bug fixes

* fix(Dockerfile): fix addgroup ([`3159bb9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3159bb93d89cda65accf60a8aa037ea6ec529a76))

## v0.37.2 (2023-10-31)

### Bug fixes

* fix(Dockerfile): replace apt-get with apk ([`6d53af1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d53af12374f8521a63b332168cf4ec2179b6dba))

## v0.37.1 (2023-10-31)

### Bug fixes

* fix: docker file fixes for github actions ([`0d500c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d500c0bcf4db1dc27481a220d65f0c130929c09))

## v0.37.0 (2023-10-31)

### Features

* feat(docker.yml): add requirements for github actions cache. ([`4cbbc89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4cbbc898674d26704fd501c6e42b54839629dbe2))

## v0.36.3 (2023-10-31)

### Bug fixes

* fix(docker.yml): Update dockerfile path and add cache. ([`349cc26`](https://github.com/vasilistotskas/grooveshop-django-api/commit/349cc26b2c6b4a9e61af5f333fe0f8f00e9d1da1))

## v0.36.2 (2023-10-30)

## v0.36.1 (2023-10-30)

### Bug fixes

* fix(docker.yml): Update dockerfile path. ([`b856bb1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b856bb1b771d1d8bcad4594038f3fb17fe852c28))

* fix(cache.py): change redis host in github actions. ([`446e105`](https://github.com/vasilistotskas/grooveshop-django-api/commit/446e1058d0e5ede29c749f92519cfb6a59a930e9))

* fix: `cache.py` fixed. ([`240c3b3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/240c3b3c07efd697dce6065d9eacea8616d8fe66))

* fix: Update staticfiles and mediafiles ([`ba83b89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ba83b890ec3f76a7fa333cec3f682f33d746404e))

* fix: More at `Description`

- celery improvements , `celery_setup.py` rename to `celery.py`.
- cache and session improvements.
- locales generate, API `schema.yml` generate,. ([`16e0f6d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16e0f6d43157ed5bdec82553594bd3ada27d8049))

## v0.36.0 (2023-10-28)

### Bug fixes

* fix: ci.yml cleanup ([`50dbaf9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50dbaf97c5419ef316d377ba01c3153533f7135f))

* fix: Ci release step setup python added. ([`84b7f27`](https://github.com/vasilistotskas/grooveshop-django-api/commit/84b7f2739a3cf239b546be783e52d09df6844c53))

* fix: Update poetry lock ([`fd62504`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fd62504d4cc3482b4da58c7ba5fc84314c0db6c9))

### Chores

* chore: Move docker files under docker folder. ([`f02fc33`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f02fc336998d52c0e4fcf593fe1f245f035eedd6))

### Features

* feat(ci.yml): Add redis service ([`0458d56`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0458d56e9382becf18ad8aeab8c6f82ad375d8cb))

* feat: Preparing for docker setup. ([`6e2f142`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6e2f1426bf0af4b59b5815da282ccc645419a3bc))

## v0.35.0 (2023-10-20)

### Bug fixes

* fix: add `psycopg[binary]` in requirements.txt and .env example file cleanup. ([`49408ce`](https://github.com/vasilistotskas/grooveshop-django-api/commit/49408ce1e80fca4ae449c9994cf3664aed8e38ad))

* fix(config): `SITE_ID` default value fix ([`b40d7ba`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b40d7ba4f940c34626e6f8f6db6d2d1b72d6b473))

### Features

* feat: More at `Description`

- settings.py split in /config folder.
- `phonenumber_field` library and use of `PhoneNumberField` in models and serializers.
- `pyproject.toml` clean up.
- migrations update. ([`1c4d811`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c4d81115d7495ee7e6752793836aa0ec0841434))

## v0.34.0 (2023-10-19)

### Features

* feat(ci): Update package versions and use cache, compile locales ([`dee4d0f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dee4d0f9737f3a9c695062482a3112de0bc2a020))

## v0.33.1 (2023-10-18)

### Bug fixes

* fix(Dockerfile): copy python version 3.12 ([`1bae2ab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1bae2ab8e873697418b1de6a36c81cf779f1bfd6))

## v0.33.0 (2023-10-18)

### Features

* feat: More at `Description`

- python version bump to `3.12.0`
- new lib `djmoney` add and usage in models and serializers.
- versions bump.
- migrations and api schema generate. ([`06faa95`](https://github.com/vasilistotskas/grooveshop-django-api/commit/06faa9547dba35b3c02e65e6849d8aa1afa5822d))

## v0.32.0 (2023-10-17)

### Bug fixes

* fix: `settings.py` change `TIME_ZONE` default and add multilang at `test_serializers.py`. ([`d9628e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d9628e8992d8225dc88bc97ebd4455d0440c69af))

* fix(settings.py): update env defaults. ([`b371658`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b371658d6a5c5423bc8f5d15595805528edfab3c))

* fix(settings.py): languages update. ([`95b2aee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/95b2aeed53178f8b61bab06f01bb3ed5d6184fed))

* fix(settings.py): languages update. ([`9fc02fd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9fc02fd2b40960dad89831a63863ac0d54374056))

* fix(settings.py): `APPEND_SLASH` default true. ([`62e519a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/62e519acaa6f3461f79d66734f0d48b4f272c9c3))

* fix(settings.py): add defaults at env vars. ([`4bab314`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4bab314c9c4c346185168423060be3d8765e2900))

* fix(settings.py): local static path for `GITHUB_WORKFLOW` system env ([`b113b14`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b113b14bda905889bcba113c186f1f214a7c3a90))

* fix(settings.py): `ALLOWED_HOSTS` env variable default set. ([`d0b0d4a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0b0d4a5fae05ba7fece9d1b275dc22439ef8c95))

### Features

* feat: More at `Description`

- Env files updated.
- Replace `django-environ` lib with `python-dotenv`.
- Multi factor authentication API endpoints implemented.
- Settings and core/storages.py class for production S3 AWS storage.
- Versions bump.

More fixes and improvements. ([`8d4ff27`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d4ff27168131749ead41fc001092dc8ed5bb72d))

## v0.31.0 (2023-10-06)

### Bug fixes

* fix: session serializer camel case fixed. ([`18baf25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/18baf256d87e2d5c334b5f84dd59332714537ddb))

### Features

* feat: More at `Description`

- Authentication improvement. `signal` receive after social account signed up to populate account image.
- Authentication new API endpoint `is_user_registered` based on email.
- Authentication `AuthenticationAllAuthPasswordResetForm` method overriding `AllAuthPasswordResetForm` to change domain.
- postgres in `ci.yml` version bump from `13` to `16`.
- More lint and general fixes. ([`53cc219`](https://github.com/vasilistotskas/grooveshop-django-api/commit/53cc219b05856d48abc1f39cf8f733d51a20cfa6))

## v0.30.0 (2023-10-05)

### Features

* feat: More at `Description`

- `Versions bump`: Django, coverage, pylint, charset_normalizer, urllib3, rich, django-redis remove,
- `psycopg2-binary` remove and replace with fresher and recommended `psycopg[binary]`
- User auth account adapter override
- Model constraints to modern way
- Migrations update ([`e3d1703`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e3d170381c44c2570f027b9b6d48816d6d4add61))

## v0.29.1 (2023-10-03)

### Bug fixes

* fix: Cart service init fix, migrations update. ([`4d48e88`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4d48e88b104710b34f265af6446a8bb062529112))

### Unknown

* Chore: Versions bump, migrations update, API schema generate, notification app fix. ([`0553779`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0553779988f84ec92fad401dc31e1eadaadc5246))

## v0.29.0 (2023-10-03)

### Features

* feat: `settings.py` updated, Session auth app refactor, User model improve.

- Session: Remove method `ensure_cart_id` at session middleware, new API endpoints (`session-all`, `session-refresh`, `session-revoke`, `session-revoke-all`, `session-active-users-count`, `session-refresh-last-activity`), signal updated,
- Settings: Update CORS and Auth.
- User: Model new method `remove_session` and properties `get_all_sessions` and `role`. ([`a142372`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a142372c233207206f52e6aa8fe1c7a1d1c28977))

* feat: Cart service refactor and cleanup, tests and API view updated. ([`de29e5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/de29e5e208ac3382585a72b4fd9e47ea7aef9e25))

## v0.28.0 (2023-09-27)

### Bug fixes

* fix(session/middleware.py): `update_cache` method rename to `update_session` to also set session + cache. ([`27c6a5d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/27c6a5d4a24f1d3ed08a52a8c9afdbcbf3584042))

### Features

* feat: Auth module improve, __unicode__ and __str__ method fallback, session and cache rewrite. ([`cc21594`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cc215943984350530d6b11c3ee0f78fb6c06035b))

## v0.27.0 (2023-09-23)

### Features

* feat(Faker): Increase command run speed using bulk_create, time of execution for each command add.

- Run migrations ([`9f56577`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9f5657773827ff76158f8e63b73f9dba16589f7d))

## v0.26.2 (2023-09-23)

### Bug fixes

* fix(populate_user_address): Command fix check for main address. ([`770f6fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/770f6fbfe2c12ec13b1ce2d351edf4d14a07ec08))

## v0.26.1 (2023-09-20)

### Bug fixes

* fix(cart): Serializer create method fix using try catch. ([`1391aac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1391aac5730a6ef85de8bcc9f005cec81613acc9))

### Chores

* chore: Versions bump.

- pillow
- django-filter
- python-semantic-release
- dj-rest-auth
- urllib3
- pydantic
- rich ([`00f4489`](https://github.com/vasilistotskas/grooveshop-django-api/commit/00f448946aae902f94296f0e52693a6bce155bee))

## v0.26.0 (2023-09-18)

### Chores

* chore: Improvements.

- Env updated.
- Gitignore Update.
- Remove django-sql-explorer.
- README Cleanup.
- API schema generate.
- Locales update.
- .idea update. ([`4622f0c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4622f0cb02cec6364e46d04a9b4482c31be43d4f))

### Features

* feat(search): Search improvements.

- Added 001_pg_trigram_extension.py in core.
- Search functionality and performance improve.
- Run migrations. ([`2436d9a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2436d9a987b1f863f0785fec3a2d26f7615b995d))

* feat(search): Search improve.

- Pass weights on product_post_save signal.
- ProductQuerySet fixed, weights pass fixed.
- Api View return a lot of better reasults included `results`, `headlines`, `search_ranks`, `result_count` and `similarities`
- SearchProductSerializer and SearchProductResultSerializer implemented. ([`da8ee4b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da8ee4bed192ef23fa4dd1c4681108ff229b67bc))

* feat: Celery, uvicorn and Notifications.

- celery.py rename to celery_setup.py.
- Create uvicorn_server.py to be able to run with uvicorn.
- Notification feature.
- settings.py improvements ([`bbc5f45`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bbc5f45cc25b6f1881c752fb48ffafc90f79d5a0))

* feat: Changes to naming, cleanup, AddressModel change and migrations.

- App urls cleanup.
- UserAccount usage from import replaced with get_user_model().
- Model `related_name` usage improve for readability.
- UserAddress Model add contstraint.
- Run migrations. ([`5a3f590`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5a3f590c4e91a1abdeee65e187b46516fb08ed33))

## v0.25.0 (2023-09-13)

### Features

* feat: Versions bump, add allauth `AccountMiddleware` in settings.py. ([`4b42395`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4b423958945db60ccc83014ca0b1b73c1e447475))

## v0.24.0 (2023-09-10)

### Features

* feat: Auth api endpoints and views + packages bump. ([`edc306e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/edc306e74145203d68fdb8856149cd93686bb193))

## v0.23.0 (2023-09-04)

### Features

* feat: Fixes and versions bump.

- Remove `translation` fields from API filtering and ordering.
- Auth and Permissions add to user API view.
- `DEFAULT_PERMISSION_CLASSES` , `DEFAULT_THROTTLE_CLASSES` and `DEFAULT_THROTTLE_RATES` added. ([`3538ee6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3538ee6d4d856f83e86c1e48dc919edcbe199536))

## v0.22.0 (2023-09-02)

### Chores

* chore(schema.yml): Generate schema.yml ([`6500b80`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6500b80ab6bc00cd0b56a35497cc68a397c584c3))

### Features

* feat(Notifications): Notifications App implemented, Ui for home page prototype.

Change, CELERY_BROKER_URL and CELERY_RESULT_BACKEND envs.
Added CHANNEL_LAYERS in settings.py.
Celery New task debug_task_notification.
asgi ProtocolTypeRouter added for websocket.
websocket_urlpatterns with `ws/notifications/` path in urls.py.
NotificationConsumer implemented.
README.md updated.
Make new migration files. ([`833116f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/833116fd8646090e6b632a8bfe1b6b50146512ac))

## v0.21.1 (2023-09-01)

### Bug fixes

* fix: User address model integer enum choices fields from CharField to PositiveSmallIntegerField, added translated description for IntegerChoices. ([`4c7e390`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4c7e390bd064f93303b2b26135a2c6eb3550016a))

## v0.21.0 (2023-09-01)

### Features

* feat(enums): Replace enum and usage with django built in models.TextChoices and models.IntegerChoices

Versions bump.
Locales compile.
Run Migrations.
Api schema.yml updated. ([`1897e29`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1897e298d73183524847d00eb26f95c2cd8e915e))

## v0.20.0 (2023-08-29)

### Features

* feat: Search improved, versions bump, minor fixes/improvements.

1) Default db postgres, added DB_PORT new env var, and update name DB_PASS env var to DB_PASSWORD.
2) Country model translations name field max_length change.
3) Order model country and region field on delete set null.
4) populate_country command fixed.
5) Faker and python-semantic-release versions bump.
6) Product fields `discount_value`, `final_price`, `price_save_percent` calculation removed from save method and implement signal and method update_calculated_fields.
7) ProductQuerySet: new methods update_search_vector for search usage and update_calculated_fields.
8) Product Search prototype and tests implemented. ([`bff87a5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bff87a57d1851cd5b316a3b941ad59d99e067945))

## v0.19.0 (2023-08-29)

### Features

* feat(commands): Seed commands improvements.

1) total var name change based on options total name.
2) faker seed instance for each lang using different seed.
3) check for available_languages.
4) checks for models with unique together. ([`721bf6a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/721bf6a83cf54ca6ee485c8dc4f7ed067e71a600))

## v0.18.0 (2023-08-27)

### Bug fixes

* fix(search): Search improve. ([`1217337`](https://github.com/vasilistotskas/grooveshop-django-api/commit/12173371d76eb7042025235decbf4516e423d508))

### Chores

* chore(static): Added static folder ([`cd8359f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cd8359fbdbff35162248e1348f62594234eb83c8))

### Features

* feat(tests): Typing improvements and tearDown class added in tests. ([`bfee3c3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bfee3c3c3a00008e1ee3848d406fbca5c94adefe))

* feat(tests): Create tests for simple search and make_thumbnail method. ([`7c7fbe6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7c7fbe672e24bbf7c19c3b374d42ba16ec9a5436))

## v0.17.0 (2023-08-26)

### Features

* feat(settings): Security improved, minor type cast at populate_user_address command. ([`8f43775`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8f4377596401975ae521b3c2e24622e4eddf65c6))

## v0.16.0 (2023-08-25)

### Features

* feat(seed): Add new dump images for product image seeding, refactor method get_or_create_default_image to be also able to create image from non /media path. ([`71b9790`](https://github.com/vasilistotskas/grooveshop-django-api/commit/71b979021a5e0356d728d50e5de14355f08b03b1))

## v0.15.0 (2023-08-25)

### Chores

* chore: migration files and compile locales. ([`9889b75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9889b75fa27f9f1d856f4a51fa16cf096171b921))

### Features

* feat(enum): New enum for order document type and move pay way enum in pay way app. ([`6066e4a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6066e4a52313f5703979f6deaf16f78553ae5db4))

## v0.14.2 (2023-08-24)

### Bug fixes

* fix: Rename StatusEnum in order and review.

order status: StatusEnum -> OrderStatusEnum
review status: StatusEnum -> ReviewStatusEnum ([`1b623c8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1b623c88372d42e6c4ba1d9116060dd75ae93832))

## v0.14.1 (2023-08-24)

### Bug fixes

* fix(templates): Change template dir to core/templates. ([`3a57eb5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a57eb57e0abeafac98c68d6122a644874c06952))

## v0.14.0 (2023-08-24)

### Features

* feat(pagination): Cursor pagination fixed and tests implemented. ([`441078e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/441078eea389a82ce50183ccfe077cb397722122))

## v0.13.0 (2023-08-23)

### Bug fixes

* fix: Lint and PEP fixes and generate_random_password method improved ([`5d74052`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5d74052c62e7f8e663b97804cc76895c7ffa663b))

### Chores

* chore(order): Migrations for paid_amount field. ([`da8276c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da8276cff863e5e9dd294d46eb9e6f10c6f0bf0e))

* chore: requirements.txt and pyproject.toml update

Packages Updated:
django-allauth==0.54.0 to 0.55.0
click==8.1.6 to 8.1.7 ([`b4a61a3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b4a61a3da44ee44234b51fc0923b5898be40a265))

* chore: Remove useless folders ([`705c306`](https://github.com/vasilistotskas/grooveshop-django-api/commit/705c306b49b871e05baea087c08f032de1534f87))

### Features

* feat(testing): New unit tests.

Tests for generate_schema_multi_lang method implemented.
Tests for BaseExpandSerializer class implemented.
Tests for PascalSnakeCaseOrderingFilter class implemented. ([`355ae53`](https://github.com/vasilistotskas/grooveshop-django-api/commit/355ae533d4f5b2d9fdb368db10f7312af847292f))

## v0.12.0 (2023-08-17)

### Features

* feat: New Unit tests implemented and remove unused methods and files. ([`db41415`](https://github.com/vasilistotskas/grooveshop-django-api/commit/db414155306e2284952391e156291cee28dbf9a5))

## v0.11.4 (2023-08-17)

### Bug fixes

* fix(ci.yml): Publish package distributions to TestPyPI if released. ([`f11972b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f11972b8a50cc61d55febaa66ec2a06028b6f986))

### Chores

* chore(README.md): Added Coverage Status ([`0d30ec0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d30ec06ba4293a842a0ec3fc470e532f9b3f501))

## v0.11.3 (2023-08-17)

### Bug fixes

* fix(ci.yml): Remove upload-to-gh-release step ([`8540ef8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8540ef884dc60e9b63f3d5a4318cfcb67635c64e))

## v0.11.2 (2023-08-17)

### Bug fixes

* fix(ci.yml): Use GITHUB_TOKEN and add tag ([`d0f14ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0f14ef27d22f14c1aaf8ef89270353ae1786eed))

### Chores

* chore(app): Added static folder ([`6d6b287`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d6b28797336bcc0805f5857af2f2b3a9d1c6481))

## v0.11.1 (2023-08-17)

### Bug fixes

* fix(poetry): update python-semantic-release in requirements and pyproject. ([`4ca745d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ca745d011a4362f0d427784d65c3e78c0712533))

## v0.11.0 (2023-08-17)

### Bug fixes

* fix(tests): Testing fixes ([`50f75dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50f75dce38cd44bc845a7537f58bdc99a461467a))

* fix(caches): fix empty caches at settings.py ([`287d2ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/287d2edfa13be5aff41fc2630da3a8f6eb3fe690))

* fix(app): caches dont use fallback in github env, schema.yml generate and added serializer_class in order checkout api view ([`9abb939`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9abb93923050044ced20327b3c7e1f35417ba394))

### Features

* feat(tests): Rewrite Cart tests/service and session middleware code cleanup. ([`02effab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/02effabade8e9e389a56c66e0eca90d60fb1c92d))

### Refactoring

* refactor(app): Changes in Description

1) Tests implemented and improved for application.
2) Add name in all URL paths (reverse) usage.
3) Cache Refactor.
4) Multi language translations.
5) requirements.txt and pyproject.toml update. ([`da506b6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da506b696f4b08eae15ac020c72a560d8cb02c9e))

## v0.10.1 (2023-07-27)

### Bug fixes

* fix(ci.yml): Build the dist_build dir correctly ([`5977d77`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5977d779e5dcf841994dc3d877e91b711de9b597))

### Chores

* chore(requirements.txt): update requirements

python-semantic-release==8.0.4
drf-spectacular==0.26.4
pylint==2.17.5 ([`ac04533`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ac0453383e49a8448d5f4e33408eae3e8ac374ad))

* chore(tests): Blog test minor update ([`d0728e3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0728e38bc9c4b3d03c17c0c7fc05d9fc549b9c9))

## v0.10.0 (2023-07-26)

### Features

* feat(tests): Rewrite Blog tests ([`d8e682a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d8e682afa0855af633118b65aefd615d8fb6d5e6))

## v0.9.2 (2023-07-24)

### Bug fixes

* fix(templates/CHANGELOG.md): Remove auto generated template from semantic release changelog, meaby will be created again , trying to figure it out. ([`74312e6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74312e67403286537c386505f60d44e7a656bcca))

## v0.9.1 (2023-07-24)

### Bug fixes

* fix(templates/CHANGELOG.md.j2): Update file ([`fbb5dde`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fbb5dde10d51fab17ab0e0ee07a443e125265ce8))

## v0.9.0 (2023-07-24)

### Features

* feat(pyproject.toml): Added changelog settings and remove generated templates in project src ([`0f1ff84`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0f1ff8433c695f4110677edd7c1ba874e49bc603))

## v0.8.9 (2023-07-23)

### Bug fixes

* fix(pyproject.toml, setup.py): Update semantic release configuration ([`f49184a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f49184a2f3b4c14d44b7ebb2eab8669c29e0c37d))

### Chores

* chore: run poetry lock ([`03bf2ab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/03bf2ab87edb481ffd0560eef6570c3b72d0c638))

### Unknown

* Delete openid directory ([`912a198`](https://github.com/vasilistotskas/grooveshop-django-api/commit/912a1988b323aae4fd5a508851de0ef4dfe67b0a))

* Delete account directory ([`73e76aa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/73e76aac0ee7a6a7117a72414b67ca49780be2a1))

* Delete socialaccount directory ([`7353e25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7353e25afce8cdf025e75891b2ebd5733c3aa2be))

* Delete allauth_2fa directory ([`cdb0d0a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cdb0d0ab2239a732156f2531640c30d5bd4bb248))

## v0.8.8 (2023-07-22)

### Bug fixes

* fix(ci.yml): remove artifacts from upload-to-gh-release and remove parser options for pyproject.toml semantic_release ([`fffbdc4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fffbdc488562785b067bccc530fe9f07a3860e9f))

## v0.8.7 (2023-07-22)

### Chores

* chore(setup.py): version bump ([`16b0593`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16b0593f15c073392b2f3e83110daee1c8fa4065))

### Unknown

* 0.8.7

Automatically generated by python-semantic-release ([`b7ae230`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7ae230b5a4b941a6469102f2fd60d1f9c0e3725))

## v0.8.6 (2023-07-22)

### Bug fixes

* fix(requirements.txt): Remove poetry ([`31f7247`](https://github.com/vasilistotskas/grooveshop-django-api/commit/31f7247138bd71f282fc5b43b39adf984d7c9b4d))

* fix(ci.yml): Trying to build package distributions

Bump urllib3 version.
Bump python-semantic-release version.
Add with packages-dir: dist_build/ at "Publish distribution 📦 to PyPI" step.
Add with packages-dir: dist_build/ at "Publish package distributions 📦 to GitHub Releases" step. ([`3a3b778`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a3b778427279b37d7318720c0991957cb427a1e))

### Unknown

* 0.8.6

Automatically generated by python-semantic-release ([`b31c0f7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b31c0f7f1a825a3a3fd71cdc8887e7baf8f2c6bb))

## v0.8.5 (2023-07-22)

### Bug fixes

* fix(setup.py): Bump version ([`a0c84a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a0c84a8da65fb00e6a35e12eb7716623f83dc720))

### Unknown

* 0.8.5

Automatically generated by python-semantic-release ([`23ab7b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/23ab7b88e920d1a32d5942a3422b1904fed3c86b))

## v0.8.4 (2023-07-22)

### Bug fixes

* fix(ci.yml): Trying to build package distributions ([`f2591ea`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f2591eab8b0717bed08719a0b1f1e811db8165d5))

### Unknown

* 0.8.4

Automatically generated by python-semantic-release ([`ffacb98`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ffacb98ee054c2e76d41ae272e75901972172571))

## v0.8.3 (2023-07-22)

### Bug fixes

* fix(ci.yml): Trying to build package distributions ([`8b70270`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b70270e6469548a1812c0e1a5bc05de8b822c2f))

### Unknown

* 0.8.3

Automatically generated by python-semantic-release ([`b629ab9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b629ab951270e6b7dc9acc26feacc0cc9dd39924))

## v0.8.2 (2023-07-22)

### Bug fixes

* fix(ci.yml): Fix release to PyPI Upgrade to Trusted Publishing ([`48acc43`](https://github.com/vasilistotskas/grooveshop-django-api/commit/48acc43f244b301282ea51f1a68c79cbd35a0f8b))

### Unknown

* 0.8.2

Automatically generated by python-semantic-release ([`012549e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/012549e21037cda5da729df6f565f1881665cbc5))

## v0.8.1 (2023-07-21)

### Bug fixes

* fix(ci.yml): Update github token ([`b700849`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7008491df8a863918be938282d6b0bdb123195e))

### Unknown

* 0.8.1

Automatically generated by python-semantic-release ([`1e6cc5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1e6cc5e3e1d7a91b364e202e50356a2447d14da4))

## v0.8.0 (2023-07-21)

### Features

* feat(ci.yml): Update release to PyPI ([`9ddb846`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9ddb846dba01361e5f507f120aeb7c5f98c46751))

### Unknown

* 0.8.0

Automatically generated by python-semantic-release ([`5b4d383`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5b4d38386eae6bbb055bf7339d29e6b099614150))

## v0.7.0 (2023-07-21)

### Features

* feat(ci.yml): Update release to PyPI ([`baa010c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/baa010c943223ac4c2473d661e72fb1e224a1eb6))

### Unknown

* 0.7.0

Automatically generated by python-semantic-release ([`a127965`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a1279651c76a4189b7505654800e47b71aabdbef))

## v0.6.0 (2023-07-21)

### Features

* feat(ci.yml): Update release to PyPI ([`74956f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74956f8cdfe006873ca80c067fa8656b27534abe))

### Unknown

* 0.6.0

Automatically generated by python-semantic-release ([`3e766fe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3e766fed6168d13ba150ad067b4254d664af664a))

## v0.5.0 (2023-07-21)

### Features

* feat(ci.yml): Update release ([`1d8ed75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1d8ed75f10364996d1a4de70e25befda345a09cd))

### Unknown

* 0.5.0

Automatically generated by python-semantic-release ([`14e85c5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/14e85c56035c0c139e5cbcb5535f4839186ed487))

## v0.4.0 (2023-07-21)

### Features

* feat(app): 🚀 .pre-commit-config.yaml versions bump, implement .flake8 file and remove setup.cfg file ([`ec12e48`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ec12e489f9b29d8f1707aed9b1e30ba1e2ce9a28))

### Unknown

* 0.4.0

Automatically generated by python-semantic-release ([`3fa9bbf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3fa9bbfd14e91187321144f2f190ec663d0f75f3))

## v0.3.2 (2023-07-21)

### Bug fixes

* fix(semantic_release): Replace github token ([`53e68c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/53e68c0f30b4e3343ed54f3e8aa567771ad91755))

### Unknown

* 0.3.2

Automatically generated by python-semantic-release ([`94b0908`](https://github.com/vasilistotskas/grooveshop-django-api/commit/94b0908526e68cccf44dcae7d064934955908199))

## v0.3.1 (2023-07-21)

### Bug fixes

* fix(semantic_release): Trying to make v8 work. ([`1c7ba51`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c7ba515a27705f7b53970bf1c447d63ecc3ddbd))

* fix(app): Update ci.yml github workflow and remove useless folders.

ref: https://python-semantic-release.readthedocs.io/en/latest/migrating_from_v7.html ([`d65a84f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d65a84f3ffa5de34d7a399825b4b789af3539e66))

### Unknown

* 0.3.1

Automatically generated by python-semantic-release ([`159cd08`](https://github.com/vasilistotskas/grooveshop-django-api/commit/159cd084851b0dc61af5fc528968d10536e4bcf3))

## v0.3.0 (2023-07-21)

### Features

* feat(seed): Seeders refactor and some models split to different files

Plus minor fixes ([`5220477`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5220477ea35897953fb98476d60757800ea6a25c))

## v0.2.2 (2023-07-19)

### Bug fixes

* fix(test_model_blog_tag.py): Test fix and re run migrations ([`fd076a9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fd076a9eebdd5f3730e2021b24cf534232b01ea7))

* fix(poetry): Poetry lock, resolve package errors, lint fix and compile messages ([`7406da2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7406da2de4eb9c16c4213ea41aa4dd2b981705d5))

### Chores

* chore(LICENSE.md): Added ([`e532056`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e532056405ebd7703f8fa24fd3c464fa94a75381))

### Features

* feat(Localization): Implement multi language support in app, admin and API

* New libs
django-rosetta UI to update locales in path /rosetta.
django-parler/django-parler-rest for multi language models/serializers and API.
* Env update
* Commands for localization
django-admin makemessages -l <locale>
django-admin makemessages --all --ignore=env
django-admin compilemessages --ignore=env ([`e926e53`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e926e5310141e73799e09fbc6633537a4a2be8ec))

### Unknown

* 0.2.2

Automatically generated by python-semantic-release ([`c68403a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c68403a0aaf29441f3020dda1e9beaeb3cc5bff3))

## v0.2.1 (2023-07-07)

### Bug fixes

* fix(docker.yml): image fix ([`b8058de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b8058dee7d98f0ec3fd8a670330e259642a62fe2))

### Unknown

* 0.2.1

Automatically generated by python-semantic-release ([`5541270`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5541270faee5c961de5513f24bddee736dd0a42a))

## v0.2.0 (2023-07-07)

### Features

* feat(docker): Added docker file and more minor fixes

Remove BACKEND_BASE_URL and replace usage with APP_BASE_URL ([`f6706d8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f6706d852aa993a3dcf7d684e01439011382b7ad))

### Unknown

* 0.2.0

Automatically generated by python-semantic-release ([`0a74685`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a746856b2bbdee1bae8770863f325049ce9f0ff))

## v0.1.1 (2023-07-07)

### Bug fixes

* fix(build): pyproject.toml, setup.cfg and setup.py

Fixed ([`a7f6638`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a7f6638ba3ac685984daa0fa0d040c448d5e1899))

### Unknown

* 0.1.1

Automatically generated by python-semantic-release ([`a466a25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a466a259a385e6868584c1ec600d4aa84b170015))

## v0.1.0 (2023-07-07)

### Bug fixes

* fix(ci.yml): env github token fix for coveralls

replace DJANGO_GITHUB_TOKEN with GITHUB_TOKEN ([`06be142`](https://github.com/vasilistotskas/grooveshop-django-api/commit/06be142b166f74b951a95709fb6f43d19a260f2c))

* fix(workflows): Update githubtoken ([`42346f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/42346f82393625c2e0dfa9fb19033e1afb5ddae0))

* fix(branch): Set branch to master

At files: pyproject.toml and setup.cfg ([`a858284`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a858284abde2ff492667b13a01e202f4960f0d13))

* fix(ci.yml): change on push to main ([`4666ec7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4666ec72bba4d76cbd3748760a6a40a198343164))

### Chores

* chore(logs): Add missing files ([`36e03f3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/36e03f3b970c55967f2677e3675488af2a5fc336))

* chore(static): Remove generated static files ([`d5fba15`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5fba150e726d0a880328d6fc1b6db4e9cc984db))

### Features

* feat(docker.yml): Added new docker.yml file for github workflows

Push Docker image ([`2a039e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a039e8be63b991bdd6487ff99b49038a293f6d6))

### Unknown

* 0.1.0

Automatically generated by python-semantic-release ([`0763761`](https://github.com/vasilistotskas/grooveshop-django-api/commit/07637614df26aa3cf4339d4a4babc7289afed0ff))

* fix:(lint): lint fixed and versions set to 0 ([`de8f3b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/de8f3b8e015cb42ba01a35385312aa98ff8d6ba8))

* Initial Commit ([`399c796`](https://github.com/vasilistotskas/grooveshop-django-api/commit/399c796fb95248c8fb916708a6316d57b0e3fb40))
