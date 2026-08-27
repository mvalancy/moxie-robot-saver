# Moxie Parent App — Authentication & REST API Protocol Map

Source: decompiled `com.embo.embodied.parent` (app `2.2.2`, versionCode 249, `BUILD_TYPE = "release"`).
Root: `<decompiled>/com/embo/embodied/parent`

Primary files:
- `api/Config.java` — all endpoint path constants, base URLs, build mode, token storage
- `api/APIService.java` — Retrofit interface: HTTP verb + path + params for every call
- `api/RequestManager.java` — call construction, client_id/client_secret, auth header attachment
- `api/ResponseManager.java` — response/error handling, 401 → refresh-token retry loop
- `api/DataManager.java` — client-side JSON:API `included`/`relationships` resolver
- `api/SecureSharedPreference.java`, `api/SharedKeys.java` — encrypted prefs storage
- `login/*`, `api/models/login/*`, `api/models/TokenResponseModel.java`, `api/models/RedirectModel.java`

---

## 1. Transport, base URLs, build mode

### 1.1 Base URL selection (`Config.getBaseUrl(boolean withApiPrefix)`)

```java
public static String getBaseUrl(boolean z) {
    int i = ...[getBuildMode().ordinal()];
    String str = i != 1 ? i != 2 ? i != 3 ? i != 4 ? i != 5 ? null
        : "https://client-service-hk-api.embodied.com/"
        : "https://client-service-cn-api.embodied.com/"
        : "https://client-service-develop-api.embodied.com/"
        : "https://client-service-api.embodied.com/"
        : "https://client-service-staging-api.embodied.com/";
    return str + (z ? "api/" : "");
}
```

| BuildMode (enum ordinal) | Host | Retrofit baseUrl (`getBaseUrl(true)`) |
|---|---|---|
| `STAGING` (0) | `client-service-staging-api.embodied.com` | `https://client-service-staging-api.embodied.com/api/` |
| `PRODUCTION` (1) | `client-service-api.embodied.com` | `https://client-service-api.embodied.com/api/` |
| `DEVELOP` (2) | `client-service-develop-api.embodied.com` | `https://client-service-develop-api.embodied.com/api/` |
| `CHINA` (3) | `client-service-cn-api.embodied.com` | `https://client-service-cn-api.embodied.com/api/` |
| `HONG_KONG` (4) | `client-service-hk-api.embodied.com` | `https://client-service-hk-api.embodied.com/api/` |

**Every REST path in this document is relative to `<host>/api/`.**
The only known non-`/api/` path is the Privo webview:
```java
public static String getPrivoUrl() {
    return String.format("%sprivo-verification?access_token=%s", getBaseUrl(false), getAuthDataModel().getAccessToken());
}
```
→ `https://<host>/privo-verification?access_token=<access_token>`

### 1.2 How build mode is selected

Stored as an **int (enum ordinal)** in encrypted SharedPreferences under key `SharedKeys.BUILD_MODE = "build_mode"`, default `PRODUCTION`:

```java
public static void setBuildMode(BuildMode buildMode) {
    RequestManager.INSTANCE.cleanApiService();   // forces Retrofit rebuild with new baseUrl
    tokenModel = null;
    prefs.edit().putInt(SharedKeys.BUILD_MODE, buildMode.ordinal()).apply();
}
public static BuildMode getBuildMode() { ... default BuildMode.PRODUCTION ... }
```

**Hidden UI trigger** (`login/LoginFragment.java`): type `envchange` into the email field on the login screen and **long-press** the `changeUrlView`; this opens a bottom sheet offering Develop / Staging / Production / China / Hong Kong.

```java
private boolean onChangeUrlClick() {
    if (!this.mBinding.emailAddressLayout.getText().trim().equals("envchange")) return false;
    showSelectEnvironmentDialog();
    return true;
}
```

This is the hook a replacement server would use only if the app were repointed; there is **no arbitrary-URL entry** — the five hosts are hardcoded. Repointing to a self-hosted server requires DNS/hosts override, a proxy, or a patched APK.

### 1.3 Retrofit / OkHttp client (`RequestManager.initRetrofit`)

```java
final String str = "EmbodiedParentApp/v2.2.2 android/" + Build.VERSION.RELEASE;
okHttpClient = new OkHttpClient.Builder()
    .readTimeout(60, SECONDS).connectTimeout(60, SECONDS)
    .addNetworkInterceptor(chain -> chain.proceed(
        chain.request().newBuilder().header(HttpHeaders.USER_AGENT, userAgent).build()))
    .build();
apiService = new Retrofit.Builder()
    .baseUrl(Config.getBaseUrl(true))
    .addConverterFactory(GsonConverterFactory.create(gson))
    .client(okHttpClient).build().create(APIService.class);
```

### 1.4 Headers — complete list

| Header | Value | Notes |
|---|---|---|
| `Authorization` | `"<token_type> <access_token>"`, token_type defaults to `Bearer` if server omits it | Set per-call via `@Header("Authorization")`, **not** an interceptor. Constant `Config.HEADER_AUTHORIZATION = "Authorization"`. |
| `User-Agent` | `EmbodiedParentApp/v2.2.2 android/<Android release>` e.g. `EmbodiedParentApp/v2.2.2 android/13` | Network interceptor, on **every** request incl. login. |
| `Content-Type` | `application/json; charset=UTF-8` (Retrofit Gson default) for `@Body` calls; `application/x-www-form-urlencoded` explicitly on both `oauth/token` calls | `@Headers({"Content-Type: application/x-www-form-urlencoded"})` |

**There are no API keys, HMACs, request signatures, nonces, device-attestation, or custom `X-` headers anywhere.** The only shared secrets are the OAuth `client_id`/`client_secret` pair, which are hardcoded in the APK (below).

Auth header construction (`api/models/TokenResponseModel.java`):
```java
public final String getAuth() {
    String str = this.tokenType;
    if (str == null) str = "Bearer";
    return str + ' ' + this.accessToken;
}
```
(One call, `getRewards`, builds it inline as `authDataModel.getTokenType() + ' ' + authDataModel.getAccessToken()` — identical, but will emit literal `"null <token>"` if the server omits `token_type`. A replacement server **should always return `"token_type": "Bearer"`**.)

Gson uses **no** field-naming policy (`GsonHelper`: plain `GsonBuilder()` + a `byte[]`↔Base64 adapter), so JSON keys are exactly the `@SerializedName` values quoted below.

### 1.5 Hardcoded OAuth client credentials (`RequestManager`)

```java
private final String getClientId() {
    switch (Config.getBuildMode()) {
      case STAGING:    return "GjnNt7QqHoiRMkciyDoTEAWug6vhpyV6LtaHn2m7hJyxNaXCduAc9Yk9CoMpKZLv";
      case PRODUCTION: return "1tjzBncMMwsTl0K-ORtwUXcYV5GH-LZh7YGvQNsDAD4";
      case DEVELOP:    return "DeJ8ykK4pM8G6qVe3gFLJzrpH6QfbRW3CKjdCT499maesa8r8vNAgFWzkDcTeXGT";
      case CHINA: case HONG_KONG: return "AqHSIQcR_Mg0zL_L7VAdUMCXznaXCpRQT18szfGCp4w";
    }
}
private final String getClientSecret() {
    return (CHINA || HONG_KONG) ? "qL_EeFcK6s2de6qcalegLMBmr0zKV1qZ2UgLAmJOjkw"
                                : "OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4";
}
```

**Production values a replacement server must accept:**
- `client_id  = 1tjzBncMMwsTl0K-ORtwUXcYV5GH-LZh7YGvQNsDAD4`
- `client_secret = OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4`

The token-shape (`_`/`-` base64url, 43 chars for production) strongly suggests a **Doorkeeper (Rails) OAuth provider**; `oauth/token` + `expires_in` + `created_at` + `scope` + refresh-token grant matches Doorkeeper's response exactly.

---

## 2. Authentication flow (step by step)

The app uses a **passwordless email-code flow wrapped in an OAuth authorization-code exchange**. There is no user password anywhere.

### Step 1 — `POST login/start`

`APIService`:
```java
@POST(Config.API_LOGIN_START)   // "login/start"
Call<ResponseBody> loginStart(@Body LoginStartRequestModel model);   // NO auth header
```
`RequestManager.loginStart(email, cb)`:
```java
redirectUri = null;
apiService.loginStart(new LoginStartRequestModel(email, getClientId(), getClientSecret()))
```

Request body (`api/models/login/LoginStartRequestModel.java`):
```json
{ "email": "user@example.com",
  "client_id": "1tjzBncMMwsTl0K-...",
  "client_secret": "OKJMOFpcI16R7Mv1..." }
```

Response body — parsed into `api/models/RedirectModel.java`:
```json
{ "redirect_uri": "<opaque string, echoed back in login/finish>",
  "user_type": "clinician"        // optional; only value in the enum User.UserType
}
```
Client side-effects:
```java
RedirectModel m = gson.fromJson(result, RedirectModel.class);
RequestManager.INSTANCE.setRedirectUri(m.getRedirectUri());
User.INSTANCE.setUserTypeFromLogin(m.getUserType());
```
Server side-effect: **emails a 6-digit code** (`Config.DEFAULT_VERIFICATION_CODE_LENGTH = 6`) and/or a deep link. The same endpoint is reused verbatim for "send me a new code" (`EmailVerificationDialogFragment.onSendNewCodeClick`).

Deep link format: the email link's URL must contain the substring `login-code`, and the **last 6 characters of the URL are the code**:
```java
public static String getLoginCodeFromLink(String str) {
    if (TextUtils.isEmpty(str) || !str.contains("login-code")) return null;
    return str.substring(str.length() - 6);
}
```
e.g. `https://…/login-code/123456` or `…?login-code=123456`.

**This endpoint also implicitly creates the account** — see §4.

### Step 2 — `POST login/finish` (code → tokens)

```java
@POST(Config.API_LOGIN_FINISH)  // "login/finish"
Call<ResponseBody> loginFinish(@Body LoginFinishRequestModel model);   // NO auth header
```
```java
apiService.loginFinish(new LoginFinishRequestModel(
    getClientId(), getClientSecret(), Config.GRANT_TYPE_PASSWORD /* "password" */,
    code, redirectUri != null ? redirectUri : ""));
```

Request body (`LoginFinishRequestModel`) — **JSON, not form-encoded**:
```json
{ "client_id":     "1tjzBncMMwsTl0K-...",
  "client_secret": "OKJMOFpcI16R7Mv1...",
  "grant_type":    "password",
  "code":          "123456",
  "redirect_uri":  "<value from login/start, or \"\">" }
```
Note the quirk: `grant_type` is the literal string `"password"` (`Config.GRANT_TYPE_PASSWORD`) even though the credential is the emailed code carried in `code`.

Response body = **the OAuth token response**, stored verbatim (see §3). Parsed as `TokenResponseModel`:
```json
{ "access_token":  "...",
  "token_type":    "Bearer",
  "expires_in":    7200,
  "refresh_token": "...",
  "scope":         "...",
  "created_at":    1700000000,       // Number (unix seconds) OR ISO-8601 String — both accepted
  "user_type":     "clinician"       // optional
}
```
`accessToken`, `refreshToken`, `scope` are **non-null required** by the Kotlin model (`Intrinsics.checkNotNullParameter`) — omitting any of them will crash Gson-constructed instances. `token_type`, `created_at`, `user_type` are nullable.

Client on success (`EmailVerificationDialogFragment.onCheckVerificationCode`):
```java
isVerificationCodeSucceed = true;
Config.setAuthData(str2);            // stores the raw JSON string
Config.getAuthDataModel();
getBaseActivity().fetchUserInfo(...);   // → GET users/me
```

### Step 3 — `GET users/me` (bootstrap the session)

```java
private static final String USER_INCLUDE =
    "mobile-devices,robots.restore,robots.robot-setting,child,identity-verification";
apiService.fetchUser(authDataModel.getAuth(), USER_INCLUDE);
```
→ `GET /api/users/me?include=mobile-devices,robots.restore,robots.robot-setting,child,identity-verification`
with `Authorization: Bearer <access_token>`.

If the returned user has empty `first-name`/`last-name`, the app treats it as a **brand-new account** and routes to the sign-up (profile-completion) screen; otherwise straight into the app.

### Step 4 (Pro/clinician only) — `POST login/register`

```java
@POST(Config.API_LOGIN_REGISTER)   // "login/register"
Call<ResponseBody> loginRegister(@Header("Authorization") String authHeader, @Body LoginRegisterRequestModel model);
```
Body (`LoginRegisterRequestModel`):
```json
{ "pro_registration_code": "ABC123" }   // may be null
```
Requires a valid access token (already authenticated). Called only when `Config.isMoxieProModeLocally` is set (user picked "Moxie Pro" on the login screen) and the account is new. Upgrades the account to `user-type: clinician`. Followed by `GET users/me`, then the sign-up profile screen.

### Step 5 — Refresh: `POST oauth/token` (grant_type=refresh_token)

```java
@FormUrlEncoded
@Headers({"Content-Type: application/x-www-form-urlencoded"})
@POST(Config.API_OAUTH_TOKEN)   // "oauth/token"
Call<ResponseBody> updateToken(@Header("Authorization") String authHeader,
                               @Field("client_id") String clientId,
                               @Field("grant_type") String grantType,
                               @Field("refresh_token") String refreshToken);
```
```java
apiService.updateToken(authDataModel.getAuth(), getClientId(),
                       Config.REFRESH_TOKEN /* "refresh_token" */,
                       authDataModel.getRefreshToken());
```
Wire form:
```
POST /api/oauth/token
Authorization: Bearer <current (possibly expired) access_token>
Content-Type: application/x-www-form-urlencoded

client_id=1tjzBncMMwsTl0K-...&grant_type=refresh_token&refresh_token=<refresh_token>
```
**No `client_secret` is sent on refresh.** The (expired) Authorization header *is* sent — a replacement server must not reject the request on that basis.

Response: same `TokenResponseModel` shape; the raw body is stored via `Config.setAuthData(result)` (replacing the whole token record, so a new `refresh_token` may be rotated in).

### Step 6 — Unused / legacy endpoint: password grant

Declared in `APIService` but **never called anywhere in the app**:
```java
@FormUrlEncoded @Headers({"Content-Type: application/x-www-form-urlencoded"}) @POST("oauth/token")
Call<ResponseBody> token(@Field("client_id") String clientId, @Field("grant_type") String grantType,
                         @Field("username") String username, @Field("password") String password);
```
A replacement server need not implement it (but it's a cheap way to mint tokens for testing).

### 2.1 Session bootstrap on cold start (`LaunchActivity`)

```java
if (Config.getAuthDataModel() != null && RestorationValidator.INSTANCE.checkRecoveryKey()) {
    updateAccessTokenIfNeeded(() -> switchToMainActivity(extras));
    return;
}
// else → login / check-email / splash
```
`BaseActivity.updateAccessTokenIfNeeded` (line ~1811):
```java
Long createdAtTimestamp = Config.getAuthDataModel().createdAtTimestamp();
if (createdAtTimestamp == null
    || createdAtTimestamp + Config.getAuthDataModel().getExpiresIn() <= System.currentTimeMillis()/1000) {
    RequestManager.INSTANCE.updateToken(... onFail: if (code == 117) tryToLoginAgain() ...);
}
```
So expiry is computed **client-side** from `created_at + expires_in`. If `created_at` is missing/unparseable the app refreshes on every launch — harmless, but returning a numeric `created_at` avoids it.
`createdAtTimestamp()` accepts either a Number (unix seconds) or an ISO date String.

### 2.2 401 handling & refresh storm control (`ResponseManager.handleOnFailed`)

- Any response other than `200/201/204` goes to `handleOnFailed`.
- If `retryOnFail && code == 401`:
  - If `tokenStatus == UPDATING`, poll up to **10 attempts × 500 ms** waiting for the in-flight refresh.
  - Otherwise call `RequestManager.updateToken(...)`; on success the original callback receives the **synthetic code `111` (`STATUS_CODE_TOKEN_UPDATED`)**, which every caller interprets as "retry me" (see e.g. `UserInfoViewModel.fetchUserInfo` → `if (code == 111) fetchUserInfo(...)`).
  - On refresh failure the callback gets **`117` (`STATUS_CODE_TOKEN_FAILED`)** → `BaseActivity.tryToLoginAgain()` / forced logout.
- `RequestManager.TokenStatus` = `UPDATED | UPDATING | FAILED`.

Synthetic/internal status codes (`Config`), useful when emulating server behaviour:

| Const | Value | Meaning |
|---|---|---|
| `STATUS_CODE_OK` | 200 | success |
| `STATUS_CODE_CREATED` | 201 | success |
| `STATUS_CODE_NO_CONTENT` | 204 | success |
| `STATUS_CODE_BAD_REQUEST` | 400 | |
| `STATUS_CODE_UNAUTHORIZED` | 401 | triggers refresh |
| `STATUS_CODE_NOT_FOUND` | 404 | also used for non-network exceptions |
| `STATUS_CODE_BAD_GATEWAY` | 502 | passed through, no logout |
| `STATUS_CODE_TOKEN_UPDATED` | 111 | client-internal: "token refreshed, retry" |
| `STATUS_CODE_AUID_NOT_FOUND` | 112 | client-internal |
| `STATUS_CODE_NO_DATA` | 113 | client-internal: request cancelled |
| `STATUS_CODE_KEY_UPDATE_ERROR` / `KEY_UPDATED` | 114 / 115 | client-internal (crypto keys) |
| `STATUS_CODE_NO_NETWORK` | 116 | client-internal |
| `STATUS_CODE_TOKEN_FAILED` | 117 | client-internal: refresh failed → logout |

Error body shape the app can parse (`api/models/error/StandardErrors.java`, `ErrorModel.java`):
```json
{ "errors": [ { "code": <any>, "title": "...", "detail": "...", "message": "..." } ] }
```

### 2.3 Token storage

`Config.setAuthData(String rawJson)` stores the **raw JSON response string** under SharedPreferences key `"auth"`:
```java
public static void setAuthData(String str) {
    tokenModel = TextUtils.isEmpty(str) ? null : gson.fromJson(str, TokenResponseModel.class);
    prefs.edit().putString("auth", str).apply();
}
```
Prefs are `EncryptedSharedPreferences` (file name **`EmbodiedApp`**, AES256-SIV keys / AES256-GCM values, Android Keystore master key `MasterKey.DEFAULT_MASTER_KEY_ALIAS`), with a plaintext fallback if crypto init fails (`EmbodiedApplication.getPrefs`). `SecureSharedPreference.migrateEncryptedSharedPreferences` one-shot migrates a legacy plaintext `EmbodiedApp` file.

Logout (`Config.removeUserInfo`) clears: `auth`, `client_public_key`, `user_data_cache`, `insights_data_cache`, `assistant_data_cache`, in-memory models. It does **not** call any server revoke endpoint (only `DELETE mobile-devices/{id}` via `removeMobileDeviceRequest`).

Other auth-adjacent prefs keys: `ppcrk` (pass-phrase / recovery key code), `client_public_key`, `last_used_email`, `build_mode`, `pairing_qr_mode`.

---

## 3. Full endpoint inventory

All paths relative to `https://<host>/api/`. "Auth" = sends `Authorization: <token_type> <access_token>`.

### 3.1 Auth / session

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | `login/start` | no | JSON `{email, client_id, client_secret}` | `{redirect_uri, user_type?}` (`RedirectModel`); side effect: email 6-digit code |
| POST | `login/finish` | no | JSON `{client_id, client_secret, grant_type:"password", code, redirect_uri}` | `TokenResponseModel` (see §2 Step 2) |
| POST | `login/register` | **yes** | JSON `{pro_registration_code}` | any 2xx accepted; app then re-fetches `users/me` |
| POST | `oauth/token` | **yes** (expired token ok) | form `client_id, grant_type=refresh_token, refresh_token` | `TokenResponseModel` |
| POST | `oauth/token` | no | form `client_id, grant_type, username, password` | *declared, never called* |

### 3.2 User

| Method | Path | Auth | Request | Response / notes |
|---|---|---|---|---|
| GET | `users/me?include=…` | yes | query `include=mobile-devices,robots.restore,robots.robot-setting,child,identity-verification` | JSON:API `UserDataModel` (§3.9) |
| PUT | `users/me` | yes | JSON `{"user": {…UserAttributes…}}` (`UpdateUserModel`) | updated `UserDataModel` (`data` only) |
| DELETE | `users/me` | yes | — | account deletion |
| POST | `users` | — | **`Config.API_CREATE_USER = "users"` is declared but has NO `APIService` method and NO call site.** Account creation happens implicitly via `login/start` (§4). |
| POST | `users/me/change-email-request` | yes | `{"new_email": "..."}` | `{code, code_length, message}` (`ChangeEmailResponseModel`) |
| POST | `users/me/change-email` | yes | `{"new_email": "...", "code": "..."}` | confirm email change |
| GET | `user-options` | yes | — | `{pro_positions:[], organization_state:[], organization_type:[]}` (`UserOptionsModel`) |
| PUT | `secret-key-collection` | yes | `{"secret_key_collection": {"secret-keys-indexed-by-public-keys": { …JSON obj… }}}` | E2E symmetric key escrow (`CryptoManager.updateKeys`) |

### 3.3 Children

| Method | Path | Auth | Request | Notes |
|---|---|---|---|---|
| POST | `children` | yes | `{"child": {…ChildrenModel…}}` (`ChildObject`) | |
| PUT | `children/{id}` | yes | `{"child": {…}}` | response parsed as `UpdateChildrenModel` |
| DELETE | `children/{id}` | yes | — | |
| GET | `children/{id}/pending-info` | yes | — | `{consent_status, consent_url, parent_email}` (COPPA/Privo) |
| POST | `children/{id}/resend-email` | yes | — | resend Privo consent email |
| GET | `children/{id}/rewards` | yes | — | |
| GET | `children/{id}/sensitive-conversations/list` | yes | — | |
| POST | `children/{id}/sensitive-conversations/schedule` | yes | `{"module_id": "..."}` | |
| POST | `children/{id}/sensitive-conversations/unschedule` | yes | `{"module_id": "..."}` | |
| GET | `child-family-members` | yes | — | |
| GET | `content-preferences` | yes | — | |

`ChildrenModel` keys (all `@SerializedName`, note many `-encrypted` fields — client-side E2E encrypted blobs): `first-name-encrypted`, `last-name-encrypted`, `nickname-encrypted`, `birthday-encrypted`, `gender-encrypted`, `auid-encrypted`, `calendar-events-encrypted`, `likes-imaginative-play-encrypted`, `self-regulation-tools-preferences-encrypted`, `therapy-needs-encrypted`, `volume-preference-encrypted`, `child-first-name`, `email`, `content-preferences`, `family`, `grl-connect-enabled`, `holiday-events`, `holidays`, `input-speed`, `is16`, `is-adult`, `latest-activity-at`, `eye-color`, `face-color`, `privo-status`, `rewards-choices`, `scheduled-sensitive-conversation`, `unlimited-time`.

### 3.4 Robot / pairing

| Method | Path | Auth | Request | Notes |
|---|---|---|---|---|
| POST | `pairing-info` | yes | **query params** (`@QueryMap`): `id`, `restore` ("true"/"false"), `user-id`, `child-id` | `RequestManager.registerForPairing`; `id` = SHA-256 hex of the pairing signing key |
| GET | `robots/{id}?include=restore,robot-setting` | yes | — | |
| PUT | `robots/{id}` | yes | `{"robot": {…RobotAttributes…}}` or `{"robot-setting": {…}}` | two overloads on the same path |
| DELETE | `robots/{id}` | yes | — | unpair |
| DELETE | `robots/{id}?rfs=1` | yes | — | unpair + factory reset |
| POST | `robots/{id}/restores` | yes | `{"restore": {"status": "initiated"\|"declined"}}` | |
| GET | `robots/{id}/ota_status` | yes | — | |
| POST | `robots/{id}/reboot` | yes | — | |
| POST | `robots/{id}/wakeup` | yes | — | |
| POST | `robots/{id}/set-language` | yes | `{input_language_id, output_language_id, output_voice_id}` | |
| POST | `grl/code` | yes | none, or `{first_name, nickname, birthday}` (`CreateGrlDataModel`) | Guest/Remote Login code |
| POST | `grl/revoke-all` | yes | — | |

### 3.5 Mobile devices (push registration)

| Method | Path | Auth | Request |
|---|---|---|---|
| POST | `mobile-devices` | yes | `{"mobile-device": {"mobile-device-id": "...", "fcm-token": "...", "apns-token": "..."}}` |
| PUT | `mobile-devices/{id}` | yes | same body |

`mobile-device-id` comes from `Config.getDeviceId()` = `UUID.nameUUIDFromBytes(ANDROID_ID + user_email)`.

### 3.6 Analytics / insights (all auth)

| Method | Path | Query params |
|---|---|---|
| GET | `analytics/pages/{id}` | `auid`, `tz` (IANA TZ id), `window`, `tip=1`, `<ETime name>=<epoch>`, `activity_id?`, `child_id?` |
| GET | `analytics/pages/details` | `auid`, `tz`, `window`, `page`, `tip=1`, `<ETime>`, `child_id?` |
| GET | `analytics/pages/insights` | same as `analytics/pages/{id}` minus path id |
| GET | `analytics/auid-encrypted` | *declared in `APIService.auidEncrypted`, never called* |

### 3.7 Notifications / content / help (all auth)

| Method | Path | Request/Query |
|---|---|---|
| GET | `notifications` | query `next?`, `archived?` |
| GET | `notifications/{id}` | — |
| POST | `notifications/{id}/{archive}` | `{archive}` path segment is literally `archive` or `unarchive` |
| GET | `calendar-holidays` | — |
| GET | `help` | — |
| GET | `help/{path}` | `path` ∈ `home`, `moxie-commands`, `moxie-activities`, `tips-for-success`, `language-support` |
| POST | `help/pronounce` | `{"speech": "..."}` → streaming audio response |
| POST | `help/share-auid` | `{"auids": [...], "mode": <EShareAuidMode>}` |
| GET | `network-tests` | — |
| POST | `network-tests` | `{"result": {…SetNetworkTestModel…}}` |

Bare `@GET`/`@POST` with `@Url` (`downloadTest`, `uploadTest`) hit **absolute URLs supplied by the `network-tests` GET response** — used only for speed testing, no auth header.

### 3.8 Teletherapy (all auth)

| Method | Path | Request |
|---|---|---|
| PUT | `teletherapy/patient-status` | `{"patient-id", "parental-consent", "verified", "settings"}` |
| POST | `teletherapy/therapists-list` | `{"user-id": "..."}` |
| POST | `teletherapy/request-access-moxie` | `{"appt": "<appointmentId>"}` |

### 3.9 `users/me` response shape (JSON:API-style)

`UserDataModel` → `{ "data": Data, "included": [IncludedModel] }`
`Data` → `{ "id", "type", "attributes": UserAttributes, "relationships": UserRelationships }`
`IncludedModel` → `{ "id", "type", "attributes", "relationships" }`

`UserRelationships` keys: `child`, `children`, `robots`, `mobile-devices`, `identity-verification` (each `{ "data": {id,type} }` or `{ "data": [ {id,type} … ] }`).

`DataManager.updateData()` dispatches `included[].type` on exactly these strings: `children`, `mobile-devices`, `robots` (`Robot.TYPE`), robot-setting (`Robot.SETTINGS_TYPE`), restores (`Robot.RESTORES_TYPE`), identity-verification.

`UserAttributes` — full `@SerializedName` list (kebab-case):
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
Enum values the client parses: `user-type: "clinician"`; `coppa-consent-status: unknown|granted|revoked`; `grl-code-status: none|used|expired|unused`; `timezone-sync: initial|automatic|manual`.

`iot-endpoint` (Integer) is notable — it is embedded as the last byte of the **proto pairing QR** (§5) and presumably selects which MQTT/IoT endpoint the robot connects to.

---

## 4. Account creation

**There is no client-visible "register user" endpoint.** `Config.API_CREATE_USER = "users"` exists as a dead constant.

The real flow (traced from `LoginFragment` → `CheckEmailFragment` → `EmailVerificationDialogFragment` → `SignUpFragment`):

1. User enters an email on the login screen (only validation: `Utils.isValidEmail`).
2. `POST login/start` with that email. **The server creates the account row if the email is unknown** — the app makes no distinction between sign-in and sign-up here; the identical request is issued either way.
3. User enters the 6-digit emailed code (or opens the `login-code` deep link) → `POST login/finish` → tokens.
4. `GET users/me`. If `first-name` **or** `last-name` is empty ⇒ new account:
   ```java
   if (User.getData() == null || TextUtils.isEmpty(...getFirstName()) || TextUtils.isEmpty(...getLastName())) {
       createNewAccount();   // → RegistrationCodeFragment (pro) or SignUpFragment (consumer)
   }
   ```
5. Consumer: `SignUpFragment` collects first name, last name (min 2 chars each), and a "email me" marketing checkbox, then
   ```java
   UserAttributes a = new UserAttributes();
   a.setFirstName(fName); a.setLastName(lName);
   if (Config.isProVersion()) a.setProPosition(...);
   a.setShareEmailWithMarketing(checkbox);
   viewModel.updateUserRequest(a, ...);   // → PUT users/me  {"user":{"first-name":…,"last-name":…,"share-email-with-marketing":…}}
   ```
   The email field is displayed but **disabled** — it is never sent at signup.
6. Pro/clinician: first `RegistrationCodeFragment` → `POST login/register {"pro_registration_code": …}` → `GET users/me` → then the same `SignUpFragment`, then `OrganizationDetailsFragment` (organization-name/type/state/city, also via `PUT users/me`).

**Email verification:** the emailed 6-digit code *is* the verification — there is no separate verify endpoint. `email-verified-at` is a read-only attribute on `UserAttributes`. Registration codes are **only** for the Pro/clinician path and are optional (`code` may be `null`).

Later flows also write to the user record:
- Recovery key export (`recovery_key/ExportRecoveryKeyFragment.java:130`) sets `public-key` via `PUT users/me`, and the derived symmetric key is escrowed via `PUT secret-key-collection`.

---

## 5. Minimum viable session for pairing

### What the pairing QR contains

`pair_moxie/PairMoxieQrCodeFragment.generateQrCode`:
```java
String accessToken = Config.getAuthDataModel() != null ? Config.getAuthDataModel().getAccessToken() : "";
if (protoMode) {
    qr = new ProtoPairing(wifiInfo, CryptoHelper.getInstance().getSigningKey().toBytes())
             .toQRString(wifiOnly, User.getData().getAttributes().getIotEndpoint());
} else {
    qr = new JSONPairing(wifiInfo, wifiOnly ? null : new PairingInfo(accessToken)).toJsonString();
}
```

**Mode A — `PAIR_JSON_TOKEN` (`JSONPairing`)** — the QR is plain JSON:
```json
{ "wifi": { "ssid": "...", "password": "...", "is_hidden": false, "band_select": <WifiBand> },
  "pair": { "user_token": "<OAuth access_token verbatim>" } }
```
(`api/models/wifi/PairingModel.java`, `PairingInfo.java` → `@SerializedName("user_token")`, `WifiNetworkInfo.java`.)
⇒ **The `user_token` in the QR *is* the `access_token` from `login/finish`.** The robot then presents it to the backend as its bearer credential.

**Mode B — `PAIR_PROTO_KEY` (`ProtoPairing`, the shipped default)** — the QR is `"PA" + Base64(hand-rolled protobuf)` carrying ssid (field 1), password (field 2), a non-production flag (field 3), *either* `secret_key` bytes (field 4) *or* wifi-only flag (field 5), hidden (field 6), band (field 7), and `iot-endpoint` (field 8). The `secret_key` is `CryptoHelper.getSigningKey().toBytes()` — **no access token is in the QR in this mode.**

```java
public static PairQRMode getPairQRMode() {
    if (PairQRMode.PAIR_PROTO_KEY.ordinal() == prefs.getInt(SharedKeys.PAIRING_QR_MODE, PairQRMode.PAIR_PROTO_KEY.ordinal()))
        return PairQRMode.PAIR_PROTO_KEY;
    return PairQRMode.PAIR_PROTO_KEY;      // note: BOTH branches return PAIR_PROTO_KEY — JSON mode is dead code in 2.2.2
}
```
So in the shipped app the QR **always** uses proto/secret-key mode; the JSON `user_token` path is unreachable. The app instead binds the key to the account server-side:
```java
RequestManager.INSTANCE.registerForPairing(
    ProtoPairing.serectHashFromKey(CryptoHelper.getInstance().getSigningKey().toBytes()),  // SHA-256 hex
    Config.userWantsToRestoreFromBackup,
    User.INSTANCE.getData().getId(),
    User.INSTANCE.getData().getRelationships().getChild().getData().getId(),
    cb);
```
→ `POST /api/pairing-info?id=<sha256hex>&restore=<bool>&user-id=<uuid>&child-id=<uuid>` with the parent's bearer token. The robot later proves possession of the raw key whose SHA-256 is `id`.

### Minimum server surface to reach a pairable session

1. `POST login/start` → return `{"redirect_uri":"x"}` (200) and deliver/accept any code.
2. `POST login/finish` → return a `TokenResponseModel` with **non-null** `access_token`, `refresh_token`, `scope`, plus `token_type:"Bearer"`, `expires_in`, numeric `created_at`.
3. `GET users/me?include=…` → JSON:API document with `data.id`, `data.type`, `data.attributes.first-name`/`last-name` non-empty (else the app forces sign-up), `data.attributes.email`, `data.attributes.iot-endpoint`, and `data.relationships.child.data.id` populated (required by `registerForPairing` — a NPE there aborts pairing).
4. `PUT users/me` (profile completion + `public-key`) and `POST children` (creates the child whose id pairing needs).
5. `PUT secret-key-collection` (recovery-key flow; `LaunchActivity` gates entry on `RestorationValidator.checkRecoveryKey()`).
6. `POST pairing-info` (accept + record the key hash) and `GET robots/{id}?include=restore,robot-setting` (the app polls this to detect the paired robot).
7. `POST oauth/token` refresh, and 401 responses only when you actually want a refresh.
8. `POST mobile-devices` (non-fatal, but called on login).

---

## 6. Quick reference — every `Config.API_*` constant

```java
API_ANALYTICS                            = "analytics/pages/{id}"
API_ANALYTICS_AUID_ENCRYPTED             = "analytics/auid-encrypted"        (unused)
API_ANALYTICS_DETAILS                    = "analytics/pages/details"
API_ANALYTICS_INSIGHTS                   = "analytics/pages/insights"
API_CALENDAR_HOLIDAYS                    = "calendar-holidays"
API_CHANGE_EMAIL                         = "users/me/change-email"
API_CHANGE_EMAIL_REQUEST                 = "users/me/change-email-request"
API_CHILDREN_PENDING_INFO                = "children/{id}/pending-info"
API_CHILDREN_RESEND_EMAIL                = "children/{id}/resend-email"
API_CHILDREN_REWARDS                     = "children/{id}/rewards"
API_CHILDREN_SENSITIVE_CONVERSATIONS_LIST= "children/{id}/sensitive-conversations/list"
API_CHILDREN_SENSITIVE_CONVERSATION_SCHEDULE   = "children/{id}/sensitive-conversations/schedule"
API_CHILDREN_SENSITIVE_CONVERSATION_UNSCHEDULE = "children/{id}/sensitive-conversations/unschedule"
API_CONTENT_PREFERENCES                  = "content-preferences"
API_CREATE_CHILDREN                      = "children"
API_CREATE_GRL                           = "grl/code"
API_CREATE_MOBILE_DEVICE                 = "mobile-devices"
API_CREATE_RESTORE_ROBOT                 = "robots/{id}/restores"
API_CREATE_USER                          = "users"                          (DEAD constant)
API_DELETE_ROBOT                         = "robots/{id}"
API_DELETE_ROBOT_RESTORE                 = "robots/{id}?rfs=1"
API_FAMILY_MEMBERS                       = "child-family-members"
API_GET_ROBOT / API_UPDATE_ROBOT         = "robots/{id}"
API_HELP                                 = "help"
API_HELP_PRONOUNCE                       = "help/pronounce"
API_HELP_RES                             = "help/{path}"
API_HELP_SHARE_AUID                      = "help/share-auid"
API_LOGIN_FINISH                         = "login/finish"
API_LOGIN_REGISTER                       = "login/register"
API_LOGIN_START                          = "login/start"
API_NETWORK_TESTS                        = "network-tests"
API_NOTIFICATIONS                        = "notifications"
API_NOTIFICATIONS_ARCHIVE                = "notifications/{id}/{archive}"
API_NOTIFICATIONS_DETAILS                = "notifications/{id}"
API_OAUTH_TOKEN                          = "oauth/token"
API_OTA_STATUS                           = "robots/{id}/ota_status"
API_PAIRING_INFO                         = "pairing-info"
API_PATH_HOME                            = "home"            (via help/{path})
API_PATH_LANGUAGE_SUPPORT                = "language-support" (via help/{path})
API_PATH_MOXIE_ACTIVITIES                = "moxie-activities" (via help/{path})
API_PATH_MOXIE_COMMANDS                  = "moxie-commands"   (via help/{path})
API_PATH_TIPS_FOR_SUCCESS                = "tips-for-success" (via help/{path})
API_PRO_ORGANIZATION_INFO                = "user-options"
API_REBOOT_ROBOT                         = "robots/{id}/reboot"
API_REVOKE_GRL                           = "grl/revoke-all"
API_ROBOT_SET_LANGUAGE                   = "robots/{id}/set-language"
API_SECRET_KEY_COLLECTION                = "secret-key-collection"
API_TELETHERAPY_PATIENT_STATUS           = "teletherapy/patient-status"
API_TELETHERAPY_REQUEST_ACCESS_MOXIE     = "teletherapy/request-access-moxie"
API_TELETHERAPY_THERAPISTS_LIST          = "teletherapy/therapists-list"
API_UPDATE_CHILDREN                      = "children/{id}"
API_UPDATE_MOBILE_DEVICE                 = "mobile-devices/{id}"
API_UPDATE_USER / API_USERS_ME           = "users/me"
API_WAKE_UP_MOXIE                        = "robots/{id}/wakeup"

GRANT_TYPE_PASSWORD  = "password"
REFRESH_TOKEN        = "refresh_token"
HEADER_AUTHORIZATION = "Authorization"
```

## 7. Open questions / gaps for the clean-room server

- **`redirect_uri` semantics.** The client treats it as an opaque string echoed from `login/start` to `login/finish` (empty string if absent). A replacement server can ignore it or use it as a login-session handle.
- **Scope value** returned in the token response is never inspected by the app — any non-null string works.
- **`iot-endpoint` integer→endpoint mapping** is not derivable from this subsystem; it is only written into the pairing QR's last field. Cross-reference with the robot-side firmware.
- **Pairing key proof.** `pairing-info` receives only `SHA-256(signing_key)`; how the robot proves possession of the raw key lives on the robot/IoT side, not in this APK.
- **`analytics/*` `window` / `ETime` vocabularies** live in `main/insights/DateSelector` (out of scope here).
