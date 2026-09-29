# Moxie Parent App — Authentication & REST API Protocol Map

**Summary.** The parent app talks plain REST over HTTPS to `https://client-service-api.embodied.com/api/`
using Retrofit and OkHttp. There is no certificate pinning, no request signing, and no API keys. The only shared secrets are a
hardcoded OAuth `client_id`/`client_secret`. Sign-in is **passwordless**: `login/start` emails a 6-digit code
(and creates the account if it is new), and `login/finish` exchanges that code for a Doorkeeper-style OAuth token. Every
call after that sends `Authorization: Bearer <access_token>`. This page is the canonical list of endpoints, headers,
token shapes, and the minimum server surface. The clean-room contract we implement is
[`rest-api-contract.md`](../../architecture/rest-api-contract.md), served by [`server/`](../../../server/).

> Clean-room notes on the original app (`com.embo.embodied.parent` 2.2.2, versionCode 249,
> `BUILD_TYPE = "release"`). Paths such as `api/Config.java` are locations *inside that app*, not files in
> this repo. Key app files: `api/Config.java` (paths, base URLs, build mode, token storage),
> `api/APIService.java` (Retrofit interface), `api/RequestManager.java` (calls, client creds, auth
> header), `api/ResponseManager.java` (errors, 401→refresh), `api/DataManager.java` (JSON:API
> `included` resolver), `api/SecureSharedPreference.java` + `api/SharedKeys.java` (encrypted prefs),
> `login/*`, `api/models/{login/*,TokenResponseModel,RedirectModel}.java`.

Related: robot request/response bodies and the pairing sequence are in
[`pairing-and-robot.md`](pairing-and-robot.md). Keys and `secret-key-collection` are in
[`crypto-and-keys.md`](crypto-and-keys.md). Manifest, TLS config, and other hostnames are in
[`app-structure.md`](app-structure.md).

---

## 1. Transport, base URLs, headers, client credentials

### 1.1 Base URL and build mode

`Config.getBaseUrl(boolean withApiPrefix)` returns the host for the current `BuildMode` and appends `api/`
when `withApiPrefix` is true:

| BuildMode (ordinal) | Host | Retrofit baseUrl (`getBaseUrl(true)`) |
|---|---|---|
| `STAGING` (0) | `client-service-staging-api.embodied.com` | `https://client-service-staging-api.embodied.com/api/` |
| **`PRODUCTION` (1, default)** | `client-service-api.embodied.com` | `https://client-service-api.embodied.com/api/` |
| `DEVELOP` (2) | `client-service-develop-api.embodied.com` | `https://client-service-develop-api.embodied.com/api/` |
| `CHINA` (3) | `client-service-cn-api.embodied.com` | `https://client-service-cn-api.embodied.com/api/` |
| `HONG_KONG` (4) | `client-service-hk-api.embodied.com` | `https://client-service-hk-api.embodied.com/api/` |

**Every REST path below is relative to `<host>/api/`.** The one exception is the Privo webview,
`Config.getPrivoUrl()`, which builds `https://<host>/privo-verification?access_token=<access_token>` from `getBaseUrl(false)`.

The build mode is stored as an int ordinal in encrypted prefs under `SharedKeys.BUILD_MODE = "build_mode"`.
`Config.setBuildMode()` also calls `RequestManager.cleanApiService()`, which rebuilds Retrofit, and clears the cached
token model. **Hidden trigger** (`login/LoginFragment.onChangeUrlClick`): type `envchange` in the login email field and
long-press `changeUrlView`. A bottom sheet then offers Develop / Staging / Production / China / Hong Kong. You cannot enter an arbitrary
URL. To point the app at a self-hosted server you need a DNS/hosts override, a proxy, or a patched APK.

### 1.2 Client (`RequestManager.initRetrofit`)

```java
final String str = "EmbodiedParentApp/v2.2.2 android/" + Build.VERSION.RELEASE;
okHttpClient = new OkHttpClient.Builder()
    .readTimeout(60, SECONDS).connectTimeout(60, SECONDS)
    .addNetworkInterceptor(chain -> chain.proceed(
        chain.request().newBuilder().header(HttpHeaders.USER_AGENT, userAgent).build()))
    .build();
apiService = new Retrofit.Builder().baseUrl(Config.getBaseUrl(true))
    .addConverterFactory(GsonConverterFactory.create(gson)).client(okHttpClient).build().create(APIService.class);
```

Gson has **no** field-naming policy (`GsonHelper` is a plain `GsonBuilder()` plus a `byte[]`↔Base64 adapter), so JSON keys
are exactly the `@SerializedName` values quoted on this page.

### 1.3 Headers (complete list)

| Header | Value | Notes |
|---|---|---|
| `Authorization` | `"<token_type> <access_token>"`, where `token_type` defaults to `Bearer` | Set per call with `@Header("Authorization")`, not by an interceptor (`Config.HEADER_AUTHORIZATION`) |
| `User-Agent` | `EmbodiedParentApp/v2.2.2 android/<Android release>` (e.g. `android/13`) | Network interceptor; sent on **every** request, including login |
| `Content-Type` | `application/json; charset=UTF-8` for `@Body` calls; `application/x-www-form-urlencoded` on both `oauth/token` calls | explicit `@Headers` on the form calls |

**There are no API keys, HMACs, signatures, nonces, device attestation, or custom `X-` headers.**
`TokenResponseModel.getAuth()` returns `(tokenType ?: "Bearer") + ' ' + accessToken`. One call, `getRewards`, builds the header
inline and sends `"null <token>"` if the server omits `token_type`, so **always return `"token_type": "Bearer"`**.

### 1.4 Hardcoded OAuth client credentials (`RequestManager.getClientId/getClientSecret`)

| BuildMode | `client_id` | `client_secret` |
|---|---|---|
| STAGING | `GjnNt7QqHoiRMkciyDoTEAWug6vhpyV6LtaHn2m7hJyxNaXCduAc9Yk9CoMpKZLv` | `OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4` |
| **PRODUCTION** | `1tjzBncMMwsTl0K-ORtwUXcYV5GH-LZh7YGvQNsDAD4` | `OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4` |
| DEVELOP | `DeJ8ykK4pM8G6qVe3gFLJzrpH6QfbRW3CKjdCT499maesa8r8vNAgFWzkDcTeXGT` | `OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4` |
| CHINA / HONG_KONG | `AqHSIQcR_Mg0zL_L7VAdUMCXznaXCpRQT18szfGCp4w` | `qL_EeFcK6s2de6qcalegLMBmr0zKV1qZ2UgLAmJOjkw` |

The token shape (base64url, 43 characters in production) and the response fields (`oauth/token`, `expires_in`,
`created_at`, `scope`, refresh grant) match a **Doorkeeper (Rails) OAuth provider**.

---

## 2. Authentication flow

This is a passwordless email-code flow wrapped in an OAuth exchange. No user password exists anywhere.

### Step 1 — `POST login/start` (no auth)

`RequestManager.loginStart` clears `redirectUri` and then sends:

```json
{ "email": "user@example.com", "client_id": "1tjzBncMMwsTl0K-...", "client_secret": "OKJMOFpcI16R7Mv1..." }
```

The response is parsed as `RedirectModel` and stored with `RequestManager.setRedirectUri`, `User.setUserTypeFromLogin`:

```json
{ "redirect_uri": "<opaque, echoed back in login/finish>", "user_type": "clinician" }   // user_type optional
```

The server emails a **6-digit code** (`Config.DEFAULT_VERIFICATION_CODE_LENGTH = 6`), a deep link, or both. The same
request doubles as "send me a new code" (`EmailVerificationDialogFragment.onSendNewCodeClick`). For the deep link,
`getLoginCodeFromLink` requires the URL to contain `login-code` and takes **the last 6 characters** as the code
(for example `…/login-code/123456` or `…?login-code=123456`). The endpoint also creates the account (§4).

### Step 2 — `POST login/finish` (no auth; code → tokens)

The body is **JSON, not form-encoded** (`LoginFinishRequestModel`):

```json
{ "client_id": "1tjzBncMMwsTl0K-...", "client_secret": "OKJMOFpcI16R7Mv1...",
  "grant_type": "password", "code": "123456", "redirect_uri": "<from login/start, or \"\">" }
```

`grant_type` is the literal `"password"` (`Config.GRANT_TYPE_PASSWORD`), even though the credential is the emailed code.
The response is the **OAuth token**, which the app stores as the raw JSON string (§2.3) and parses as `TokenResponseModel`:

```json
{ "access_token": "...", "token_type": "Bearer", "expires_in": 7200, "refresh_token": "...",
  "scope": "...", "created_at": 1700000000, "user_type": "clinician" }
```

`access_token`, `refresh_token`, and `scope` are **non-null required** in the Kotlin model, and leaving any of them out crashes the
app. `token_type`, `created_at`, and `user_type` are nullable. `created_at` may be a Number (unix seconds) or an ISO-8601
string. On success, `EmailVerificationDialogFragment.onCheckVerificationCode` calls `Config.setAuthData(raw)` and then
`fetchUserInfo`.

### Step 3 — `GET users/me` (bootstrap)

`GET users/me?include=mobile-devices,robots.restore,robots.robot-setting,child,identity-verification`
(`RequestManager.USER_INCLUDE`). If `first-name` or `last-name` is empty, the app treats the account as new and routes to sign-up (§4).

### Step 4 — `POST login/register` (Pro/clinician only, auth)

Body `{"pro_registration_code": "ABC123"}` (may be `null`). The app calls this only when `Config.isMoxieProModeLocally` is set
(the user picked "Moxie Pro" on login) and the account is new. It upgrades the account to `user-type: clinician`, and the app then calls
`GET users/me` and shows the profile screens.

### Step 5 — Refresh: `POST oauth/token`

```
POST /api/oauth/token
Authorization: Bearer <current (possibly expired) access_token>
Content-Type: application/x-www-form-urlencoded

client_id=1tjzBncMMwsTl0K-...&grant_type=refresh_token&refresh_token=<refresh_token>
```

The refresh sends **no `client_secret`**. It does send the expired `Authorization` header, so a server must not reject the refresh because of it.
The response has the same `TokenResponseModel` shape and replaces the whole stored record, so the server may rotate the refresh token.

### Step 6 — Password grant (declared, never called)

`APIService.token(client_id, grant_type, username, password)` is a form `POST oauth/token` that nothing in the app calls. A
server can skip it, though it is a cheap way to mint test tokens.

### 2.1 Cold start (`LaunchActivity`)

If the app has stored auth data and `RestorationValidator.checkRecoveryKey()` passes, it runs `updateAccessTokenIfNeeded` and then
opens `MainActivity`. Otherwise it goes to login. `BaseActivity.updateAccessTokenIfNeeded` computes expiry **on the phone** as
`created_at + expires_in <= now`, and refreshes on failure with `117 → tryToLoginAgain()`. A missing or unparseable
`created_at` makes the app refresh on every launch, so return a numeric value.

### 2.2 401 handling (`ResponseManager.handleOnFailed`)

- Any status other than 200/201/204 goes to `handleOnFailed`.
- On `retryOnFail && 401`: if `tokenStatus == UPDATING`, the app polls **10 × 500 ms** for the refresh already in flight.
  Otherwise it calls `updateToken`. On success the caller receives the synthetic code **`111`**, which means "retry me" (for example,
  `UserInfoViewModel.fetchUserInfo` checks `if (code == 111) fetchUserInfo(...)`). On failure it receives **`117`**, which leads to
  `tryToLoginAgain()` and a forced logout.
- `RequestManager.TokenStatus` = `UPDATED | UPDATING | FAILED`.

| `Config` const | Value | Meaning |
|---|---|---|
| `STATUS_CODE_OK` / `CREATED` / `NO_CONTENT` | 200 / 201 / 204 | success |
| `STATUS_CODE_BAD_REQUEST` | 400 | |
| `STATUS_CODE_UNAUTHORIZED` | 401 | triggers refresh |
| `STATUS_CODE_NOT_FOUND` | 404 | also used for non-network exceptions |
| `STATUS_CODE_BAD_GATEWAY` | 502 | passed through, no logout |
| `STATUS_CODE_TOKEN_UPDATED` | 111 | client-internal: token refreshed, retry |
| `STATUS_CODE_AUID_NOT_FOUND` | 112 | client-internal |
| `STATUS_CODE_NO_DATA` | 113 | client-internal: request cancelled |
| `STATUS_CODE_KEY_UPDATE_ERROR` / `KEY_UPDATED` | 114 / 115 | client-internal (crypto keys) |
| `STATUS_CODE_NO_NETWORK` | 116 | client-internal |
| `STATUS_CODE_TOKEN_FAILED` | 117 | client-internal: refresh failed → logout |

Error body the app can parse (`api/models/error/StandardErrors.java`, `ErrorModel.java`):
`{ "errors": [ { "code": <any>, "title": "...", "detail": "...", "message": "..." } ] }`.

### 2.3 Token storage and prefs

`Config.setAuthData(raw)` parses the raw JSON into `TokenResponseModel` and stores the **raw string** under prefs key `"auth"`.
Prefs are `EncryptedSharedPreferences` (file **`EmbodiedApp`**, AES256-SIV keys and AES256-GCM values, Keystore master
key `MasterKey.DEFAULT_MASTER_KEY_ALIAS`). If crypto init fails they fall back to plaintext (`EmbodiedApplication.getPrefs`).
`SecureSharedPreference.migrateEncryptedSharedPreferences` migrates a legacy plaintext `EmbodiedApp` file once.

Logout (`Config.removeUserInfo`) clears `auth`, `client_public_key`, `user_data_cache`, `insights_data_cache`,
`assistant_data_cache`, and the in-memory models. It **does not** call a server revoke endpoint. The only related server call is
`DELETE mobile-devices/{id}` (`removeMobileDeviceRequest`). Other prefs keys: `ppcrk` (recovery-key code),
`client_public_key`, `last_used_email`, `build_mode`, `pairing_qr_mode`.

---

## 3. Full endpoint inventory

All paths are relative to `https://<host>/api/`. Auth = `Authorization: <token_type> <access_token>`. The `Config.API_*`
constant for each path is given in the Const column.

### 3.1 Auth / session

| Method | Path | Const | Auth | Request | Response |
|---|---|---|---|---|---|
| POST | `login/start` | `API_LOGIN_START` | no | JSON `{email, client_id, client_secret}` | `{redirect_uri, user_type?}`; emails a code |
| POST | `login/finish` | `API_LOGIN_FINISH` | no | JSON `{client_id, client_secret, grant_type:"password", code, redirect_uri}` | `TokenResponseModel` |
| POST | `login/register` | `API_LOGIN_REGISTER` | yes | JSON `{pro_registration_code}` | any 2xx; app re-fetches `users/me` |
| POST | `oauth/token` | `API_OAUTH_TOKEN` | yes (expired ok) | form `client_id, grant_type=refresh_token, refresh_token` | `TokenResponseModel` |
| POST | `oauth/token` | — | no | form `client_id, grant_type, username, password` | *declared, never called* |

Other constants: `GRANT_TYPE_PASSWORD = "password"`, `REFRESH_TOKEN = "refresh_token"`,
`HEADER_AUTHORIZATION = "Authorization"`.

### 3.2 User

| Method | Path | Const | Request | Response / notes |
|---|---|---|---|---|
| GET | `users/me?include=…` | `API_USERS_ME` | query `include=mobile-devices,robots.restore,robots.robot-setting,child,identity-verification` | JSON:API `UserDataModel` (§3.9) |
| PUT | `users/me` | `API_UPDATE_USER` | `{"user": {…UserAttributes…}}` (`UpdateUserModel`) | updated `UserDataModel` (`data` only) |
| DELETE | `users/me` | | — | account deletion |
| — | `users` | `API_CREATE_USER` | **dead constant**: no `APIService` method, no call site | accounts are created by `login/start` (§4) |
| POST | `users/me/change-email-request` | `API_CHANGE_EMAIL_REQUEST` | `{"new_email"}` | `{code, code_length, message}` (`ChangeEmailResponseModel`) |
| POST | `users/me/change-email` | `API_CHANGE_EMAIL` | `{"new_email", "code"}` | confirms the change |
| GET | `user-options` | `API_PRO_ORGANIZATION_INFO` | — | `{pro_positions:[], organization_state:[], organization_type:[]}` (`UserOptionsModel`) |
| PUT | `secret-key-collection` | `API_SECRET_KEY_COLLECTION` | `{"secret_key_collection": {"secret-keys-indexed-by-public-keys": {…}}}` | sealed-seed escrow, see [crypto §3](crypto-and-keys.md#3-secret-key-collection) |

All user endpoints require auth.

### 3.3 Children

| Method | Path | Const | Request | Notes |
|---|---|---|---|---|
| POST | `children` | `API_CREATE_CHILDREN` | `{"child": {…ChildrenModel…}}` (`ChildObject`) | |
| PUT | `children/{id}` | `API_UPDATE_CHILDREN` | `{"child": {…}}` | response parsed as `UpdateChildrenModel` |
| DELETE | `children/{id}` | | — | |
| GET | `children/{id}/pending-info` | `API_CHILDREN_PENDING_INFO` | — | `{consent_status, consent_url, parent_email}` (COPPA/Privo) |
| POST | `children/{id}/resend-email` | `API_CHILDREN_RESEND_EMAIL` | — | resends the Privo consent email |
| GET | `children/{id}/rewards` | `API_CHILDREN_REWARDS` | — | |
| GET | `children/{id}/sensitive-conversations/list` | `API_CHILDREN_SENSITIVE_CONVERSATIONS_LIST` | — | |
| POST | `children/{id}/sensitive-conversations/schedule` | `API_CHILDREN_SENSITIVE_CONVERSATION_SCHEDULE` | `{"module_id"}` | |
| POST | `children/{id}/sensitive-conversations/unschedule` | `API_CHILDREN_SENSITIVE_CONVERSATION_UNSCHEDULE` | `{"module_id"}` | |
| GET | `child-family-members` | `API_FAMILY_MEMBERS` | — | |
| GET | `content-preferences` | `API_CONTENT_PREFERENCES` | — | |

`ChildrenModel` keys (all `@SerializedName`). The `-encrypted` ones are SecretBox blobs, see
[crypto §5b](crypto-and-keys.md#5b-field-level-encryption-apimodelschildjava177-196-asdecrypteddata):
`first-name-encrypted`, `last-name-encrypted`, `nickname-encrypted`, `birthday-encrypted`, `gender-encrypted`,
`auid-encrypted`, `calendar-events-encrypted`, `likes-imaginative-play-encrypted`,
`self-regulation-tools-preferences-encrypted`, `therapy-needs-encrypted`, `volume-preference-encrypted`,
`child-first-name`, `email`, `content-preferences`, `family`, `grl-connect-enabled`, `holiday-events`, `holidays`,
`input-speed`, `is16`, `is-adult`, `latest-activity-at`, `eye-color`, `face-color`, `privo-status`, `rewards-choices`,
`scheduled-sensitive-conversation`, `unlimited-time`.

### 3.4 Robot / pairing

Request and response bodies are in [`pairing-and-robot.md` §5](pairing-and-robot.md#5-robot-control-api).

| Method | Path | Const | Request | Notes |
|---|---|---|---|---|
| POST | `pairing-info` | `API_PAIRING_INFO` | **query** (`@QueryMap`): `id`, `restore` (`"true"`/`"false"`), `user-id`, `child-id`; empty body | `id` = hex SHA-256 of the pairing seed |
| GET | `robots/{id}?include=restore,robot-setting` | `API_GET_ROBOT` | — | |
| PUT | `robots/{id}` | `API_UPDATE_ROBOT` | `{"robot": {…}}` or `{"robot-settings": {…}}` | two overloads, same path |
| DELETE | `robots/{id}` | `API_DELETE_ROBOT` | — | unpair |
| DELETE | `robots/{id}?rfs=1` | `API_DELETE_ROBOT_RESTORE` | — | unpair + factory reset |
| POST | `robots/{id}/restores` | `API_CREATE_RESTORE_ROBOT` | `{"restore": {"status": "initiated"\|"declined"}}` | |
| GET | `robots/{id}/ota_status` | `API_OTA_STATUS` | — | |
| POST | `robots/{id}/reboot` | `API_REBOOT_ROBOT` | — | |
| POST | `robots/{id}/wakeup` | `API_WAKE_UP_MOXIE` | — | |
| POST | `robots/{id}/set-language` | `API_ROBOT_SET_LANGUAGE` | `{input_language_id, output_language_id, output_voice_id}` | |
| POST | `grl/code` | `API_CREATE_GRL` | none, or `{first_name, nickname, birthday}` (`CreateGrlDataModel`) | Guest/Remote Login code |
| POST | `grl/revoke-all` | `API_REVOKE_GRL` | — | |

### 3.5 Mobile devices (push registration)

| Method | Path | Const | Request |
|---|---|---|---|
| POST | `mobile-devices` | `API_CREATE_MOBILE_DEVICE` | `{"mobile-device": {"mobile-device-id", "fcm-token", "apns-token"}}` |
| PUT | `mobile-devices/{id}` | `API_UPDATE_MOBILE_DEVICE` | same body |

`mobile-device-id` = `Config.getDeviceId()` = `UUID.nameUUIDFromBytes(ANDROID_ID + user_email)`.

### 3.6 Analytics / insights (auth)

| Method | Path | Const | Query |
|---|---|---|---|
| GET | `analytics/pages/{id}` | `API_ANALYTICS` | `auid`, `tz` (IANA), `window`, `tip=1`, `<ETime name>=<epoch>`, `activity_id?`, `child_id?` |
| GET | `analytics/pages/details` | `API_ANALYTICS_DETAILS` | `auid`, `tz`, `window`, `page`, `tip=1`, `<ETime>`, `child_id?` |
| GET | `analytics/pages/insights` | `API_ANALYTICS_INSIGHTS` | same as `analytics/pages/{id}` minus the path id |
| GET | `analytics/auid-encrypted` | `API_ANALYTICS_AUID_ENCRYPTED` | *declared (`APIService.auidEncrypted`), never called* |

The `window` and `ETime` vocabularies live in `main/insights/DateSelector`, which has not been mapped.

### 3.7 Notifications / content / help (auth)

| Method | Path | Const | Request / query |
|---|---|---|---|
| GET | `notifications` | `API_NOTIFICATIONS` | `next?`, `archived?` |
| GET | `notifications/{id}` | `API_NOTIFICATIONS_DETAILS` | — |
| POST | `notifications/{id}/{archive}` | `API_NOTIFICATIONS_ARCHIVE` | `{archive}` is literally `archive` or `unarchive` |
| GET | `calendar-holidays` | `API_CALENDAR_HOLIDAYS` | — |
| GET | `help` | `API_HELP` | — (returns `encrypted_auids` among others) |
| GET | `help/{path}` | `API_HELP_RES` | `path` ∈ `home`, `moxie-commands`, `moxie-activities`, `tips-for-success`, `language-support` (`API_PATH_HOME`, `API_PATH_MOXIE_COMMANDS`, `API_PATH_MOXIE_ACTIVITIES`, `API_PATH_TIPS_FOR_SUCCESS`, `API_PATH_LANGUAGE_SUPPORT`) |
| POST | `help/pronounce` | `API_HELP_PRONOUNCE` | `{"speech"}` → streaming audio |
| POST | `help/share-auid` | `API_HELP_SHARE_AUID` | `{"auids": [...], "mode": <EShareAuidMode>}` |
| GET / POST | `network-tests` | `API_NETWORK_TESTS` | POST `{"result": {…}}` (shapes in [pairing §5.8](pairing-and-robot.md#58-network-tests)) |

The bare `@GET`/`@POST @Url` calls (`downloadTest`, `uploadTest`) hit **absolute URLs taken from the `network-tests` response**
and send no auth header. They are used only for speed tests.

### 3.8 Teletherapy (auth)

| Method | Path | Const | Request |
|---|---|---|---|
| PUT | `teletherapy/patient-status` | `API_TELETHERAPY_PATIENT_STATUS` | `{"patient-id", "parental-consent", "verified", "settings"}` |
| POST | `teletherapy/therapists-list` | `API_TELETHERAPY_THERAPISTS_LIST` | `{"user-id"}` |
| POST | `teletherapy/request-access-moxie` | `API_TELETHERAPY_REQUEST_ACCESS_MOXIE` | `{"appt": "<appointmentId>"}` |

### 3.9 `users/me` response shape (JSON:API-style)

- `UserDataModel` = `{ "data": Data, "included": [IncludedModel] }`
- `Data` = `{ id, type, attributes: UserAttributes, relationships: UserRelationships }`
- `IncludedModel` = `{ id, type, attributes, relationships }`

`UserRelationships` has the keys `child`, `children`, `robots`, `mobile-devices`, and `identity-verification`. Each one is
`{ "data": {id,type} }` or `{ "data": [ … ] }`. `DataManager.updateData()` dispatches `included[].type` on `children`,
`mobile-devices`, `robots` (`Robot.TYPE`), robot settings (`Robot.SETTINGS_TYPE`), restores (`Robot.RESTORES_TYPE`), and
`identity-verification`.

`UserAttributes` (kebab-case `@SerializedName`):

```
active-child-id, battery-notifications-enabled, coppa-consent-status, email,
email-verified-at, first-name, grl-code-status, has-backups, iot-endpoint,
last-grl-code, last-name, last-restored-child-id, max-children,
mission-notifications-enabled, moxie-image-state, organization-city,
organization-name, organization-state, organization-type, pro-position,
public-key, share-anonymous-data-opt-in, share-email-with-marketing,
share-usage-data-opt-in, supports-eye-color, supports-face-color,
timezone-id, timezone-sync, unread-message-count, user-type
```

Parsed enums: `user-type: clinician`; `coppa-consent-status: unknown|granted|revoked`;
`grl-code-status: none|used|expired|unused`; `timezone-sync: initial|automatic|manual`.
`iot-endpoint` (Integer) is copied into pairing-QR field 8 ([`qr-format.md`](qr-format.md)). `public-key` is the
user's base64 X25519 key, which the app uses to validate recovery phrases ([crypto §4c](crypto-and-keys.md#4c-export-enter-and-silent-restore)).

---

## 4. Account creation

The client has **no "register user" endpoint** (`API_CREATE_USER` is dead). The flow runs
`LoginFragment` → `CheckEmailFragment` → `EmailVerificationDialogFragment` → `SignUpFragment`:

1. The user enters an email. The only check is `Utils.isValidEmail`.
2. `POST login/start`. **The server creates the account if the email is unknown.** Sign-in and sign-up send
   identical requests.
3. The user enters the 6-digit code (or opens the `login-code` link), then `POST login/finish` returns tokens.
4. `GET users/me`. If `User.getData()` is null or `first-name` or `last-name` is empty, `createNewAccount()` routes to
   `RegistrationCodeFragment` (Pro) or `SignUpFragment` (consumer).
5. Consumer: `SignUpFragment` collects first and last name (at least 2 characters each) and an "email me" checkbox, then sends
   `PUT users/me {"user":{"first-name":…,"last-name":…,"share-email-with-marketing":…}}` (plus `pro-position` if
   `Config.isProVersion()`). The email field is shown but disabled and never sent.
6. Pro: `POST login/register` → `GET users/me` → `SignUpFragment` → `OrganizationDetailsFragment`
   (organization-name/type/state/city, also sent with `PUT users/me`).

The emailed code *is* the email verification. There is no separate verify endpoint, and `email-verified-at` is
read-only. Registration codes exist only on the Pro path and are optional. Later, exporting the recovery key
(`recovery_key/ExportRecoveryKeyFragment.java:130`) sets `public-key` with `PUT users/me` and escrows the sealed
seed with `PUT secret-key-collection`.

---

## 5. Minimum server surface for a pairable session

This is what a replacement server must implement for the app to log in and appear to pair. The pairing sequence itself is
in [`pairing-and-robot.md`](pairing-and-robot.md#3-the-pairing-sequence).

1. `POST login/start` returns `{"redirect_uri":"x"}` (200) and accepts some code.
2. `POST login/finish` returns a `TokenResponseModel` with non-null `access_token`, `refresh_token`, and `scope`, plus
   `token_type:"Bearer"`, `expires_in`, and a numeric `created_at`.
3. `GET users/me?include=…` returns a JSON:API document with `data.id`, `data.type`, non-empty `first-name`/`last-name`
   (otherwise the app forces sign-up), `email`, `iot-endpoint`, `public-key`, `has-backups`, and
   **`relationships.child.data.id`**. `registerForPairing` dereferences the child id, and an NPE there aborts
   pairing. Once a robot binds, the response also needs `relationships.robots.data[]`.
4. `PUT users/me` (profile completion and `public-key`) and `POST children` (creates the child that pairing needs).
5. `PUT secret-key-collection` returns 200. This is the recovery-key flow; `LaunchActivity` gates on `checkRecoveryKey()`.
6. `POST pairing-info` accepts the request and records the hash, returning 200/201 (it may be a no-op).
   `GET robots/{id}?include=restore,robot-setting` returns the robot object.
7. `GET robots/{id}/ota_status` returns something like `{"status":"idle","percent":100,…}`, and `POST robots/{id}/restores` returns 201.
8. `POST oauth/token` handles refresh. Return 401 only when you actually want the app to refresh.
9. `POST mobile-devices` is called on login. Its failure is not fatal.

Everything else (`notifications`, `analytics/*`, `content-preferences`, `help/*`, `network-tests`, teletherapy, GRL)
is app-side polish and can be stubbed. Other values the app never checks: `redirect_uri` is an opaque string the app
echoes back (a server may use it as a login-session handle or ignore it), and `scope` can be any non-null string.

---
📖 [Phone-side index](README.md) · [Pairing & robot](pairing-and-robot.md) · [Crypto & keys](crypto-and-keys.md) · [Reverse-engineering index](../README.md)
