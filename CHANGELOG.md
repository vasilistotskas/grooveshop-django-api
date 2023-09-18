# CHANGELOG




## v0.26.0 (2023-09-18)

### Chore

* chore: Improvements.

- Env updated.
- Gitignore Update.
- Remove django-sql-explorer.
- README Cleanup.
- API schema generate.
- Locales update.
- .idea update. ([`4622f0c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4622f0cb02cec6364e46d04a9b4482c31be43d4f))

### Feature

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

### Chore

* chore(release): release version 0.25.0 [skip ci] ([`f21e4d9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f21e4d9cbb3bb8a2c45be48d3f1fd41f604306ca))

### Feature

* feat: Versions bump, add allauth `AccountMiddleware` in settings.py. ([`4b42395`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4b423958945db60ccc83014ca0b1b73c1e447475))

## v0.24.0 (2023-09-10)

### Chore

* chore(release): release version 0.24.0 [skip ci] ([`60d4ba7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/60d4ba7217888ca2e65857759c855077514697ad))

### Feature

* feat: Auth api endpoints and views + packages bump. ([`edc306e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/edc306e74145203d68fdb8856149cd93686bb193))

## v0.23.0 (2023-09-04)

### Chore

* chore(release): release version 0.23.0 [skip ci] ([`f9f07db`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f9f07db2c84a866a1324580742ab69330bb053e2))

### Feature

* feat: Fixes and versions bump.

- Remove `translation` fields from API filtering and ordering.
- Auth and Permissions add to user API view.
- `DEFAULT_PERMISSION_CLASSES` , `DEFAULT_THROTTLE_CLASSES` and `DEFAULT_THROTTLE_RATES` added. ([`3538ee6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3538ee6d4d856f83e86c1e48dc919edcbe199536))

## v0.22.0 (2023-09-02)

### Chore

* chore(release): release version 0.22.0 [skip ci] ([`386cd7c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/386cd7ce3ba781bfed41812e02a0a50db13a14f8))

* chore(schema.yml): Generate schema.yml ([`6500b80`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6500b80ab6bc00cd0b56a35497cc68a397c584c3))

### Feature

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

### Chore

* chore(release): release version 0.21.1 [skip ci] ([`26ce29e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26ce29e99cde9c9d93fb4e159a9cded42d4e238f))

### Fix

* fix: User address model integer enum choices fields from CharField to PositiveSmallIntegerField, added translated description for IntegerChoices. ([`4c7e390`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4c7e390bd064f93303b2b26135a2c6eb3550016a))

## v0.21.0 (2023-09-01)

### Chore

* chore(release): release version 0.21.0 [skip ci] ([`ac6ea4e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ac6ea4e98b7d0b4d757d6ea957ed609bb68df607))

### Feature

* feat(enums): Replace enum and usage with django built in models.TextChoices and models.IntegerChoices

Versions bump.
Locales compile.
Run Migrations.
Api schema.yml updated. ([`1897e29`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1897e298d73183524847d00eb26f95c2cd8e915e))

## v0.20.0 (2023-08-29)

### Chore

* chore(release): release version 0.20.0 [skip ci] ([`b56961e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b56961e91927e2f56e7ea7ab2f9954376605697e))

### Feature

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

### Chore

* chore(release): release version 0.19.0 [skip ci] ([`cf56421`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cf56421fe6c358f35784e2b42fee76ca1e00d1ab))

### Feature

* feat(commands): Seed commands improvements.

1) total var name change based on options total name.
2) faker seed instance for each lang using different seed.
3) check for available_languages.
4) checks for models with unique together. ([`721bf6a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/721bf6a83cf54ca6ee485c8dc4f7ed067e71a600))

## v0.18.0 (2023-08-27)

### Chore

* chore(release): release version 0.18.0 [skip ci] ([`1c5223d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c5223d71c192b7b1c5da3d96a377690da5b0e0d))

* chore(static): Added static folder ([`cd8359f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cd8359fbdbff35162248e1348f62594234eb83c8))

### Feature

* feat(tests): Typing improvements and tearDown class added in tests. ([`bfee3c3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bfee3c3c3a00008e1ee3848d406fbca5c94adefe))

* feat(tests): Create tests for simple search and make_thumbnail method. ([`7c7fbe6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7c7fbe672e24bbf7c19c3b374d42ba16ec9a5436))

### Fix

* fix(search): Search improve. ([`1217337`](https://github.com/vasilistotskas/grooveshop-django-api/commit/12173371d76eb7042025235decbf4516e423d508))

## v0.17.0 (2023-08-26)

### Chore

* chore(release): release version 0.17.0 [skip ci] ([`3d96c47`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3d96c4773ac5499477293b719be52bcc65d2832a))

### Feature

* feat(settings): Security improved, minor type cast at populate_user_address command. ([`8f43775`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8f4377596401975ae521b3c2e24622e4eddf65c6))

## v0.16.0 (2023-08-25)

### Chore

* chore(release): release version 0.16.0 [skip ci] ([`d5fd42d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5fd42d2a5442a7cfdded6803e27320767c2e8ee))

### Feature

* feat(seed): Add new dump images for product image seeding, refactor method get_or_create_default_image to be also able to create image from non /media path. ([`71b9790`](https://github.com/vasilistotskas/grooveshop-django-api/commit/71b979021a5e0356d728d50e5de14355f08b03b1))

## v0.15.0 (2023-08-25)

### Chore

* chore(release): release version 0.15.0 [skip ci] ([`1dca7cc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1dca7cc38e0819f04ed74a17220477fd2dd10e54))

* chore: migration files and compile locales. ([`9889b75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9889b75fa27f9f1d856f4a51fa16cf096171b921))

### Feature

* feat(enum): New enum for order document type and move pay way enum in pay way app. ([`6066e4a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6066e4a52313f5703979f6deaf16f78553ae5db4))

## v0.14.2 (2023-08-24)

### Chore

* chore(release): release version 0.14.2 [skip ci] ([`2b697b5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2b697b540d8f12b39bc2146fd33029b1aeb2f617))

### Fix

* fix: Rename StatusEnum in order and review.

order status: StatusEnum -&gt; OrderStatusEnum
review status: StatusEnum -&gt; ReviewStatusEnum ([`1b623c8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1b623c88372d42e6c4ba1d9116060dd75ae93832))

## v0.14.1 (2023-08-24)

### Chore

* chore(release): release version 0.14.1 [skip ci] ([`9e8f37e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9e8f37eb5b5526c507196c7b26b461e6ac16932a))

### Fix

* fix(templates): Change template dir to core/templates. ([`3a57eb5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a57eb57e0abeafac98c68d6122a644874c06952))

## v0.14.0 (2023-08-24)

### Chore

* chore(release): release version 0.14.0 [skip ci] ([`5ce3fa7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5ce3fa7a1b8e4feb239df2f843d2062a2413f50a))

### Feature

* feat(pagination): Cursor pagination fixed and tests implemented. ([`441078e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/441078eea389a82ce50183ccfe077cb397722122))

## v0.13.0 (2023-08-23)

### Chore

* chore(release): release version 0.13.0 [skip ci] ([`2b497c8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2b497c8076412a03ca44262c0af3b873a7e91964))

* chore(order): Migrations for paid_amount field. ([`da8276c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da8276cff863e5e9dd294d46eb9e6f10c6f0bf0e))

* chore: requirements.txt and pyproject.toml update

Packages Updated:
django-allauth==0.54.0 to 0.55.0
click==8.1.6 to 8.1.7 ([`b4a61a3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b4a61a3da44ee44234b51fc0923b5898be40a265))

* chore: Remove useless folders ([`705c306`](https://github.com/vasilistotskas/grooveshop-django-api/commit/705c306b49b871e05baea087c08f032de1534f87))

### Feature

* feat(testing): New unit tests.

Tests for generate_schema_multi_lang method implemented.
Tests for BaseExpandSerializer class implemented.
Tests for PascalSnakeCaseOrderingFilter class implemented. ([`355ae53`](https://github.com/vasilistotskas/grooveshop-django-api/commit/355ae533d4f5b2d9fdb368db10f7312af847292f))

### Fix

* fix: Lint and PEP fixes and generate_random_password method improved ([`5d74052`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5d74052c62e7f8e663b97804cc76895c7ffa663b))

## v0.12.0 (2023-08-17)

### Chore

* chore(release): release version 0.12.0 [skip ci] ([`99f2eec`](https://github.com/vasilistotskas/grooveshop-django-api/commit/99f2eec7ca7901de770ec0abd5e3fb6989741ce4))

### Feature

* feat: New Unit tests implemented and remove unused methods and files. ([`db41415`](https://github.com/vasilistotskas/grooveshop-django-api/commit/db414155306e2284952391e156291cee28dbf9a5))

## v0.11.4 (2023-08-17)

### Chore

* chore(release): release version 0.11.4 [skip ci] ([`c663567`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c663567f0d6312ab5b310e89cf4764efdb9f14fb))

* chore(README.md): Added Coverage Status ([`0d30ec0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d30ec06ba4293a842a0ec3fc470e532f9b3f501))

### Fix

* fix(ci.yml): Publish package distributions to TestPyPI if released. ([`f11972b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f11972b8a50cc61d55febaa66ec2a06028b6f986))

## v0.11.3 (2023-08-17)

### Chore

* chore(release): release version 0.11.3 [skip ci] ([`f86a244`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f86a244f23e47636222345c02a6b69209731b249))

### Fix

* fix(ci.yml): Remove upload-to-gh-release step ([`8540ef8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8540ef884dc60e9b63f3d5a4318cfcb67635c64e))

## v0.11.2 (2023-08-17)

### Chore

* chore(release): release version 0.11.2 [skip ci] ([`a8eace2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a8eace2e0288d8b8be650c92742d41c319586c44))

* chore(app): Added static folder ([`6d6b287`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d6b28797336bcc0805f5857af2f2b3a9d1c6481))

### Fix

* fix(ci.yml): Use GITHUB_TOKEN and add tag ([`d0f14ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0f14ef27d22f14c1aaf8ef89270353ae1786eed))

## v0.11.1 (2023-08-17)

### Chore

* chore(release): release version 0.11.1 [skip ci] ([`3f35a84`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3f35a84cae1f6ac3200c2d4fae7cef5137d84e0a))

### Fix

* fix(poetry): update python-semantic-release in requirements and pyproject. ([`4ca745d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ca745d011a4362f0d427784d65c3e78c0712533))

## v0.11.0 (2023-08-17)

### Chore

* chore(release): release version 0.11.0 [skip ci] ([`2538db1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2538db1cbf7a151f72f82cd3087bf64c96fc5891))

### Feature

* feat(tests): Rewrite Cart tests/service and session middleware code cleanup. ([`02effab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/02effabade8e9e389a56c66e0eca90d60fb1c92d))

### Fix

* fix(tests): Testing fixes ([`50f75dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50f75dce38cd44bc845a7537f58bdc99a461467a))

* fix(caches): fix empty caches at settings.py ([`287d2ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/287d2edfa13be5aff41fc2630da3a8f6eb3fe690))

* fix(app): caches dont use fallback in github env, schema.yml generate and added serializer_class in order checkout api view ([`9abb939`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9abb93923050044ced20327b3c7e1f35417ba394))

### Refactor

* refactor(app): Changes in Description

1) Tests implemented and improved for application.
2) Add name in all URL paths (reverse) usage.
3) Cache Refactor.
4) Multi language translations.
5) requirements.txt and pyproject.toml update. ([`da506b6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da506b696f4b08eae15ac020c72a560d8cb02c9e))

## v0.10.1 (2023-07-27)

### Chore

* chore(release): release version 0.10.1 [skip ci] ([`ed839b1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ed839b1706b9292c4edc813afcfa707c5c780ccf))

* chore(requirements.txt): update requirements

python-semantic-release==8.0.4
drf-spectacular==0.26.4
pylint==2.17.5 ([`ac04533`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ac0453383e49a8448d5f4e33408eae3e8ac374ad))

* chore(tests): Blog test minor update ([`d0728e3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0728e38bc9c4b3d03c17c0c7fc05d9fc549b9c9))

### Fix

* fix(ci.yml): Build the dist_build dir correctly ([`5977d77`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5977d779e5dcf841994dc3d877e91b711de9b597))

## v0.10.0 (2023-07-26)

### Chore

* chore(release): release version 0.10.0 [skip ci] ([`e5de424`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e5de42497b69e2552fdcf5d945fb7a5504b93763))

### Feature

* feat(tests): Rewrite Blog tests ([`d8e682a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d8e682afa0855af633118b65aefd615d8fb6d5e6))

## v0.9.2 (2023-07-24)

### Chore

* chore(release): release version 0.9.2 [skip ci] ([`8a87529`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8a8752943a40e9ae01d61f5d0b015d8df562ad79))

### Fix

* fix(templates/CHANGELOG.md): Remove auto generated template from semantic release changelog, meaby will be created again , trying to figure it out. ([`74312e6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74312e67403286537c386505f60d44e7a656bcca))

## v0.9.1 (2023-07-24)

### Chore

* chore(release): release version 0.9.1 [skip ci] ([`35fb0ce`](https://github.com/vasilistotskas/grooveshop-django-api/commit/35fb0ce20183396d8c22452dd8545f905dce18fd))

### Fix

* fix(templates/CHANGELOG.md.j2): Update file ([`fbb5dde`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fbb5dde10d51fab17ab0e0ee07a443e125265ce8))

## v0.9.0 (2023-07-24)

### Chore

* chore(release): release version 0.9.0 [skip ci] ([`35b1837`](https://github.com/vasilistotskas/grooveshop-django-api/commit/35b1837fc915eebaae1fce5565fad03f6234cca7))

### Feature

* feat(pyproject.toml): Added changelog settings and remove generated templates in project src ([`0f1ff84`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0f1ff8433c695f4110677edd7c1ba874e49bc603))

## v0.8.9 (2023-07-23)

### Chore

* chore(release): release version 0.8.9 [skip ci] ([`948f839`](https://github.com/vasilistotskas/grooveshop-django-api/commit/948f83924b1b97d75065b5110dd2155ddb3e5c7c))

* chore: run poetry lock ([`03bf2ab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/03bf2ab87edb481ffd0560eef6570c3b72d0c638))

### Fix

* fix(pyproject.toml, setup.py): Update semantic release configuration ([`f49184a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f49184a2f3b4c14d44b7ebb2eab8669c29e0c37d))

### Unknown

* Delete openid directory ([`912a198`](https://github.com/vasilistotskas/grooveshop-django-api/commit/912a1988b323aae4fd5a508851de0ef4dfe67b0a))

* Delete account directory ([`73e76aa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/73e76aac0ee7a6a7117a72414b67ca49780be2a1))

* Delete socialaccount directory ([`7353e25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7353e25afce8cdf025e75891b2ebd5733c3aa2be))

* Delete allauth_2fa directory ([`cdb0d0a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cdb0d0ab2239a732156f2531640c30d5bd4bb248))

## v0.8.8 (2023-07-22)

### Chore

* chore(release): release version 0.8.8 [skip ci] ([`e5cc8eb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e5cc8eb97aa7afa0a0ac3ca98d809bf6aea8de1b))

### Fix

* fix(ci.yml): remove artifacts from upload-to-gh-release and remove parser options for pyproject.toml semantic_release ([`fffbdc4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fffbdc488562785b067bccc530fe9f07a3860e9f))

## v0.8.7 (2023-07-22)

### Chore

* chore(setup.py): version bump ([`16b0593`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16b0593f15c073392b2f3e83110daee1c8fa4065))

### Unknown

* 0.8.7

Automatically generated by python-semantic-release ([`b7ae230`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7ae230b5a4b941a6469102f2fd60d1f9c0e3725))

## v0.8.6 (2023-07-22)

### Fix

* fix(requirements.txt): Remove poetry ([`31f7247`](https://github.com/vasilistotskas/grooveshop-django-api/commit/31f7247138bd71f282fc5b43b39adf984d7c9b4d))

* fix(ci.yml): Trying to build package distributions

Bump urllib3 version.
Bump python-semantic-release version.
Add with packages-dir: dist_build/ at &#34;Publish distribution 📦 to PyPI&#34; step.
Add with packages-dir: dist_build/ at &#34;Publish package distributions 📦 to GitHub Releases&#34; step. ([`3a3b778`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a3b778427279b37d7318720c0991957cb427a1e))

### Unknown

* 0.8.6

Automatically generated by python-semantic-release ([`b31c0f7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b31c0f7f1a825a3a3fd71cdc8887e7baf8f2c6bb))

## v0.8.5 (2023-07-22)

### Fix

* fix(setup.py): Bump version ([`a0c84a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a0c84a8da65fb00e6a35e12eb7716623f83dc720))

### Unknown

* 0.8.5

Automatically generated by python-semantic-release ([`23ab7b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/23ab7b88e920d1a32d5942a3422b1904fed3c86b))

## v0.8.4 (2023-07-22)

### Fix

* fix(ci.yml): Trying to build package distributions ([`f2591ea`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f2591eab8b0717bed08719a0b1f1e811db8165d5))

### Unknown

* 0.8.4

Automatically generated by python-semantic-release ([`ffacb98`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ffacb98ee054c2e76d41ae272e75901972172571))

## v0.8.3 (2023-07-22)

### Fix

* fix(ci.yml): Trying to build package distributions ([`8b70270`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b70270e6469548a1812c0e1a5bc05de8b822c2f))

### Unknown

* 0.8.3

Automatically generated by python-semantic-release ([`b629ab9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b629ab951270e6b7dc9acc26feacc0cc9dd39924))

## v0.8.2 (2023-07-22)

### Fix

* fix(ci.yml): Fix release to PyPI Upgrade to Trusted Publishing ([`48acc43`](https://github.com/vasilistotskas/grooveshop-django-api/commit/48acc43f244b301282ea51f1a68c79cbd35a0f8b))

### Unknown

* 0.8.2

Automatically generated by python-semantic-release ([`012549e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/012549e21037cda5da729df6f565f1881665cbc5))

## v0.8.1 (2023-07-21)

### Fix

* fix(ci.yml): Update github token ([`b700849`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7008491df8a863918be938282d6b0bdb123195e))

### Unknown

* 0.8.1

Automatically generated by python-semantic-release ([`1e6cc5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1e6cc5e3e1d7a91b364e202e50356a2447d14da4))

## v0.8.0 (2023-07-21)

### Feature

* feat(ci.yml): Update release to PyPI ([`9ddb846`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9ddb846dba01361e5f507f120aeb7c5f98c46751))

### Unknown

* 0.8.0

Automatically generated by python-semantic-release ([`5b4d383`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5b4d38386eae6bbb055bf7339d29e6b099614150))

## v0.7.0 (2023-07-21)

### Feature

* feat(ci.yml): Update release to PyPI ([`baa010c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/baa010c943223ac4c2473d661e72fb1e224a1eb6))

### Unknown

* 0.7.0

Automatically generated by python-semantic-release ([`a127965`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a1279651c76a4189b7505654800e47b71aabdbef))

## v0.6.0 (2023-07-21)

### Feature

* feat(ci.yml): Update release to PyPI ([`74956f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74956f8cdfe006873ca80c067fa8656b27534abe))

### Unknown

* 0.6.0

Automatically generated by python-semantic-release ([`3e766fe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3e766fed6168d13ba150ad067b4254d664af664a))

## v0.5.0 (2023-07-21)

### Feature

* feat(ci.yml): Update release ([`1d8ed75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1d8ed75f10364996d1a4de70e25befda345a09cd))

### Unknown

* 0.5.0

Automatically generated by python-semantic-release ([`14e85c5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/14e85c56035c0c139e5cbcb5535f4839186ed487))

## v0.4.0 (2023-07-21)

### Feature

* feat(app): 🚀 .pre-commit-config.yaml versions bump, implement .flake8 file and remove setup.cfg file ([`ec12e48`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ec12e489f9b29d8f1707aed9b1e30ba1e2ce9a28))

### Unknown

* 0.4.0

Automatically generated by python-semantic-release ([`3fa9bbf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3fa9bbfd14e91187321144f2f190ec663d0f75f3))

## v0.3.2 (2023-07-21)

### Fix

* fix(semantic_release): Replace github token ([`53e68c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/53e68c0f30b4e3343ed54f3e8aa567771ad91755))

### Unknown

* 0.3.2

Automatically generated by python-semantic-release ([`94b0908`](https://github.com/vasilistotskas/grooveshop-django-api/commit/94b0908526e68cccf44dcae7d064934955908199))

## v0.3.1 (2023-07-21)

### Fix

* fix(semantic_release): Trying to make v8 work. ([`1c7ba51`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c7ba515a27705f7b53970bf1c447d63ecc3ddbd))

* fix(app): Update ci.yml github workflow and remove useless folders.

ref: https://python-semantic-release.readthedocs.io/en/latest/migrating_from_v7.html ([`d65a84f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d65a84f3ffa5de34d7a399825b4b789af3539e66))

### Unknown

* 0.3.1

Automatically generated by python-semantic-release ([`159cd08`](https://github.com/vasilistotskas/grooveshop-django-api/commit/159cd084851b0dc61af5fc528968d10536e4bcf3))

## v0.3.0 (2023-07-21)

### Feature

* feat(seed): Seeders refactor and some models split to different files

Plus minor fixes ([`5220477`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5220477ea35897953fb98476d60757800ea6a25c))

## v0.2.2 (2023-07-19)

### Chore

* chore(LICENSE.md): Added ([`e532056`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e532056405ebd7703f8fa24fd3c464fa94a75381))

### Fix

* fix(test_model_blog_tag.py): Test fix and re run migrations ([`fd076a9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fd076a9eebdd5f3730e2021b24cf534232b01ea7))

* fix(poetry): Poetry lock, resolve package errors, lint fix and compile messages ([`7406da2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7406da2de4eb9c16c4213ea41aa4dd2b981705d5))

### Unknown

* 0.2.2

Automatically generated by python-semantic-release ([`c68403a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c68403a0aaf29441f3020dda1e9beaeb3cc5bff3))

*  feat(Localization): Implement multi language support in app, admin and API

* New libs
django-rosetta UI to update locales in path /rosetta.
django-parler/django-parler-rest for multi language models/serializers and API.
* Env update
* Commands for localization
django-admin makemessages -l &lt;locale&gt;
django-admin makemessages --all --ignore=env
django-admin compilemessages --ignore=env ([`e926e53`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e926e5310141e73799e09fbc6633537a4a2be8ec))

## v0.2.1 (2023-07-07)

### Fix

* fix(docker.yml): image fix ([`b8058de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b8058dee7d98f0ec3fd8a670330e259642a62fe2))

### Unknown

* 0.2.1

Automatically generated by python-semantic-release ([`5541270`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5541270faee5c961de5513f24bddee736dd0a42a))

## v0.2.0 (2023-07-07)

### Feature

* feat(docker): Added docker file and more minor fixes

Remove BACKEND_BASE_URL and replace usage with APP_BASE_URL ([`f6706d8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f6706d852aa993a3dcf7d684e01439011382b7ad))

### Unknown

* 0.2.0

Automatically generated by python-semantic-release ([`0a74685`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a746856b2bbdee1bae8770863f325049ce9f0ff))

## v0.1.1 (2023-07-07)

### Fix

* fix(build): pyproject.toml, setup.cfg and setup.py

Fixed ([`a7f6638`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a7f6638ba3ac685984daa0fa0d040c448d5e1899))

### Unknown

* 0.1.1

Automatically generated by python-semantic-release ([`a466a25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a466a259a385e6868584c1ec600d4aa84b170015))

## v0.1.0 (2023-07-07)

### Chore

* chore(logs): Add missing files ([`36e03f3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/36e03f3b970c55967f2677e3675488af2a5fc336))

* chore(static): Remove generated static files ([`d5fba15`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5fba150e726d0a880328d6fc1b6db4e9cc984db))

### Feature

* feat(docker.yml): Added new docker.yml file for github workflows

Push Docker image ([`2a039e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a039e8be63b991bdd6487ff99b49038a293f6d6))

### Fix

* fix(ci.yml): env github token fix for coveralls

replace DJANGO_GITHUB_TOKEN with GITHUB_TOKEN ([`06be142`](https://github.com/vasilistotskas/grooveshop-django-api/commit/06be142b166f74b951a95709fb6f43d19a260f2c))

* fix(workflows): Update githubtoken ([`42346f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/42346f82393625c2e0dfa9fb19033e1afb5ddae0))

* fix(branch): Set branch to master

At files: pyproject.toml and setup.cfg ([`a858284`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a858284abde2ff492667b13a01e202f4960f0d13))

* fix(ci.yml): change on push to main ([`4666ec7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4666ec72bba4d76cbd3748760a6a40a198343164))

### Unknown

* 0.1.0

Automatically generated by python-semantic-release ([`0763761`](https://github.com/vasilistotskas/grooveshop-django-api/commit/07637614df26aa3cf4339d4a4babc7289afed0ff))

* fix:(lint): lint fixed and versions set to 0 ([`de8f3b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/de8f3b8e015cb42ba01a35385312aa98ff8d6ba8))

* Initial Commit ([`399c796`](https://github.com/vasilistotskas/grooveshop-django-api/commit/399c796fb95248c8fb916708a6316d57b0e3fb40))
