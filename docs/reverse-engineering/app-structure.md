# 04 — Manifest, Components, SDKs & Package Inventory

**App:** Moxie Robot parent app
**Package:** `com.embo.embodied.parent`
**Application class:** `com.embo.embodied.parent.EmbodiedApplication`
**versionName:** 2.2.2 · **versionCode:** 249 · **minSdk:** 23 · **targetSdk:** 34 (compileSdk 34 / Android 14)
**BuildConfig:** `BUILD_TYPE=release`, `DEBUG=false` (no debug/staging build flags baked in — environment is switched at runtime, see §7).

Sources:
- Manifest: `<work>/apktool-out/AndroidManifest.xml`
- Java: `<decompiled>`
- apktool.yml confirms versionCode 249 / versionName 2.2.2 / minSdk 23 / targetSdk 34.

---

## 1. AndroidManifest

### 1.1 Permissions
| Permission | Notes |
|---|---|
| `android.permission.INTERNET` | network |
| `android.permission.ACCESS_NETWORK_STATE` | |
| `android.permission.ACCESS_WIFI_STATE` | used during Moxie Wi-Fi pairing |
| `android.permission.WAKE_LOCK` | |
| `android.permission.WRITE_SETTINGS` | (brightness control, see utils/Brightness) |
| `android.permission.FOREGROUND_SERVICE` | for NotificationService |
| `android.permission.FOREGROUND_SERVICE_DATA_SYNC` | Android 14 typed FGS |
| `android.permission.ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` | Wi-Fi scan / pairing (SSID) |
| `android.permission.VIBRATE` | |
| `android.permission.POST_NOTIFICATIONS` | Android 13+ notif runtime perm |
| `com.google.android.c2dm.permission.RECEIVE` | FCM push |
| `com.google.android.gms.permission.AD_ID` | advertising ID (GMS measurement) |
| `com.google.android.finsky.permission.BIND_GET_INSTALL_REFERRER_SERVICE` | Play install referrer |

No camera permission declared, but camera **features** are requested `required="false"` (front camera, autofocus, flash) — used by the ZXing QR scanner during pairing. Wi-Fi feature `required="false"`.

`<queries>`: a single intent query for `android.intent.action.SENDTO` with `data scheme="*"` — used to resolve an email client (support/contact "send email").

`android:allowBackup="false"`, `fullBackupContent="false"`, `extractNativeLibs="false"`, `largeHeap="true"`. Native libs are NOT extracted (loaded from APK) — relevant: libsodium/JNA/pdfium/exoplayer native `.so`.

### 1.2 Activities
| Activity | Exported | Launch mode | Notes |
|---|---|---|---|
| `LaunchActivity` | **true** | singleTask | **LAUNCHER / MAIN entry point.** Holds the deep-link filter (see §1.6). |
| `MainActivity` | false | singleTop | primary app UI host (post-login) |
| `pair_moxie.PairMoxieActivity` | false | singleTop | Moxie pairing flow |
| `child_info.EditChildInfoActivity` | false | singleTop | child profile editing |
| `pdf.PDFReaderActivity` | false | — | in-app PDF viewer (EULA, guides) |
| `video_player.VideoPlayerActivity` | false | singleTop | ExoPlayer video (onboarding/help) |
| `main.moxie.FullScreenOtaStatusActivity` | false | singleTop | OTA update status full-screen |
| `coppa.IncompleteAgreementActivity` | false | singleTop | COPPA consent gate |
| `com.journeyapps.barcodescanner.CaptureActivity` | (default false) | — | ZXing QR capture (library-provided) |
| `com.google.android.gms.common.api.GoogleApiActivity` | false | — | GMS internal |
| `com.google.android.play.core.common.PlayCoreDialogWrapperActivity` | false | — | Play Core in-app update dialog |

Only `LaunchActivity` is exported. `MainActivity` is NOT exported and has no launcher filter.

### 1.3 Services
| Service | Exported | Type | Notes |
|---|---|---|---|
| `notification.NotificationService` | (default) | **foregroundServiceType="dataSync"**, launchMode singleTask | app's own foreground service |
| `firebase.MessagingService` | false | FCM | app's own `FirebaseMessagingService` subclass; intent-filter `com.google.firebase.MESSAGING_EVENT` (default priority) |
| `com.google.firebase.messaging.FirebaseMessagingService` | false | FCM | library fallback, intent-filter priority `-500`, directBootAware |
| `com.google.firebase.components.ComponentDiscoveryService` | false | Firebase component registrar (see §3) | |
| `com.google.android.datatransport.runtime.backends.TransportBackendDiscovery` | false | datatransport CCT backend | |
| `com.google.android.datatransport.runtime.scheduling.jobscheduling.JobInfoSchedulerService` | false | BIND_JOB_SERVICE | |
| `com.google.android.gms.measurement.AppMeasurementService` | false | GA measurement | |
| `com.google.android.gms.measurement.AppMeasurementJobService` | false | BIND_JOB_SERVICE | GA measurement job |

### 1.4 Receivers
| Receiver | Exported | Notes |
|---|---|---|
| `com.google.firebase.iid.FirebaseInstanceIdReceiver` | **true** | permission `com.google.android.c2dm.permission.SEND`; action `com.google.android.c2dm.intent.RECEIVE` — standard FCM receiver |
| `com.google.android.datatransport.runtime.scheduling.jobscheduling.AlarmManagerSchedulerBroadcastReceiver` | false | |
| `com.google.android.gms.measurement.AppMeasurementReceiver` | false | GA measurement |

### 1.5 Providers
| Provider | Authority | Notes |
|---|---|---|
| `com.google.firebase.provider.FirebaseInitProvider` | `com.embo.embodied.parent.firebaseinitprovider` | Firebase bootstrap |
| `androidx.lifecycle.ProcessLifecycleOwnerInitializer` | `com.embo.embodied.parent.lifecycle-process` | multiprocess |

No app-owned `FileProvider` is declared in the manifest.

### 1.6 Intent filters — deep links / app links (IMPORTANT)
On **`LaunchActivity`** (exported, singleTask):
- Standard launcher: `MAIN` + `LAUNCHER`.
- App Link (`android:autoVerify="true"`): `VIEW` + `DEFAULT` + `BROWSABLE`, `scheme="https"` `host="embo.page.link"`.

**There is NO custom `moxie://` scheme.** The only deep-link surface is the **Firebase Dynamic Links** domain `https://embo.page.link` (verified App Link). Firebase Dynamic Links registrar is present (§3), and `BaseActivity` + `api/interfaces/DeepLinkCallback` handle the incoming link. This matters for interop: any email-verification / password-reset / invite links the backend generates flow through `embo.page.link` (a Firebase-hosted domain), then the app extracts a redirect target. Since Firebase Dynamic Links has been shut down by Google, these links are dead unless intercepted/replaced. The app also stores a `redirectUri` from a server `RedirectModel` response (RequestManager) — the deep-link/redirect handling is server-driven.

### 1.7 meta-data
- `com.google.firebase.messaging.default_notification_icon` → `@drawable/ic_notification`
- `com.google.firebase.messaging.default_notification_color` → `@color/colorPrimary`
- `com.google.android.gms.version` → `@integer/google_play_services_version`
- Firebase component registrars (under ComponentDiscoveryService) — see §3.
- datatransport backend `cct`.
- Play delivery meta: `com.android.vending.splits.required=true`, `com.android.vending.splits=@xml/splits0`, stamp source `https://play.google.com/store`, `derived.apk.id=2`. (This is a split/bundle APK — `isSplitRequired="true"`, requiredSplitTypes `base__abi,base__density`.)

**No `android:networkSecurityConfig` meta/attribute and no API-key meta-data in the manifest** (Google API key lives in resources, §3).

---

## 2. Network Security Config — CRITICAL for MITM / redirect

**There is NO `network_security_config.xml`.** Confirmed:
- No `res/xml/network_security_config.xml` (res/xml contains only `splits0.xml` and standalone_badge_*).
- No `android:networkSecurityConfig` attribute on `<application>`.
- No `android:usesCleartextTraffic` attribute anywhere.
- No `trust-anchors` / `pin-set` / `cleartextTrafficPermitted` strings anywhere in the app.

**Implication (default platform config for targetSdk 34):**
- **No certificate pinning** — confirmed also in code: `RequestManager.initRetrofit()` builds a plain `OkHttpClient.Builder()` with only read/connect timeouts and a single network interceptor that adds a `User-Agent` header. There is **no `CertificatePinner`, no `sslSocketFactory`, no custom `X509TrustManager`, no `hostnameVerifier`** anywhere under `com/embo/` — grep returned NONE.
- **Cleartext HTTP is blocked by default** (targetSdk ≥ 28 default `cleartextTrafficPermitted=false`). A local replacement server must serve **HTTPS**, or the app must be repointed and a config injected.
- **User-added CA certs are NOT trusted by default** on API 24+. To MITM without patching, you must either: (a) add your CA to the **system** trust store (rooted device), or (b) **repackage** the APK adding a `network_security_config.xml` with `<debug-overrides>`/user trust-anchors (allowed since there is no pinning to defeat), or (c) run on an emulator with an injectable system CA.

**Bottom line:** MITM/redirect is straightforward — no pinning, no custom TLS. The only real barrier is the standard "user CAs untrusted" default, solved by repackaging (trivial, no pin to strip) or a rooted/emulated device. The base URL is also **runtime-switchable via SharedPreferences** (see §7), so redirection may not even require MITM.

---

## 3. Firebase / Google config

`google-services.json` is compiled into `res/values/strings.xml` (no raw json file). Values:
- `google_app_id` = `1:376761969826:android:8a6885c32a08768ed57bfb`
- `project_id` = `parent-app-245020`
- `gcm_defaultSenderId` = `376761969826`
- `google_api_key` / `google_crash_reporting_api_key` = `AIzaSyBVog09a0czSc719JzThvSS6SZ0BkW-DKk`
- `default_web_client_id` = `376761969826-q1022eu5afi8eopvsp08kca84cvg23h3.apps.googleusercontent.com`
- `firebase_database_url` = `https://parent-app-245020.firebaseio.com`
- `com.crashlytics.android.build_id` = `00000000000000000000000000000000`

**Firebase services in use** (from ComponentDiscoveryService registrars + libs):
- **FCM (Cloud Messaging)** — push. App subclass `firebase.MessagingService` (`onMessageReceived`, builds notifications from `remoteMessage.getData()`; also registers device token via `mobile-devices` API). FirebaseMessagingRegistrar present.
- **Firebase Dynamic Links** — FirebaseDynamicLinkRegistrar; backs the `embo.page.link` App Link (§1.6). *Note: Google discontinued Dynamic Links — a dead dependency for interop.*
- **Crashlytics** — CrashlyticsRegistrar; `Config.java` calls `FirebaseCrashlytics`.
- **Analytics (GA4 / measurement)** — AnalyticsConnectorRegistrar + `play-services-measurement*`; wrapper `firebase/Analytics.java` uses `FirebaseAnalytics.setCurrentScreen()` for screen tracking. AD_ID permission present.
- **Installations** — FirebaseInstallationsRegistrar (FID).
- **DataTransport** — TransportRegistrar + CCT backend (telemetry/crash upload transport).
- **Realtime Database URL is configured** (`parent-app-245020.firebaseio.com`) but no RTDB client library appears under jadx sources — likely unused/legacy config; no RemoteConfig library present either.

There is **no Firebase Auth / Google Sign-In flow at runtime** despite `default_web_client_id` being present — auth is the app's own OAuth against embodied.com (`oauth/token`, password grant), see §7.

---

## 4. Third-party SDK inventory (bundled libraries)

Network-facing / telemetry items flagged.

| Package | Library | Role | Network? |
|---|---|---|---|
| `okhttp3`, `okio` | OkHttp | HTTP client (Retrofit transport) | **Yes** → embodied.com API |
| `retrofit2` | Retrofit | REST client to `client-service-*.embodied.com` | **Yes** |
| `com.google.gson` | Gson | JSON (de)serialization | — |
| `com.google.firebase.*` | Firebase | FCM, Crashlytics, Analytics, Dynamic Links, Installations, DataTransport | **Yes** → googleapis / crashlytics.com / fcm |
| `com.google.android.gms.*` | Google Play Services | measurement/analytics, ads-identifier, base/basement, cloud-messaging | **Yes** → GA/measurement endpoints |
| `com.google.android.datatransport` | DataTransport (CCT) | batches telemetry/crash events to Google | **Yes** |
| `com.google.android.play` | Play Core | in-app updates / split install | Yes (Play) |
| `com.google.android.exoplayer2` | ExoPlayer | video playback (onboarding/help videos) | Yes (media fetch) |
| `com.google.android.material`, `com.google.android.flexbox` | Material / Flexbox | UI | — |
| `com.google.common` | Guava | utilities | — |
| `com.google.crypto.tink` | Tink | crypto (used by androidx.security-crypto EncryptedSharedPreferences) | — |
| `com.google.zxing` + `com.journeyapps.barcodescanner` | ZXing + journeyapps embedded scanner | QR **scan** (pairing) and QR **generate** | — (camera) |
| `org.libsodium.jni` / `org.kaliumjni.lib` (Kalium) + `com.sun.jna` | libsodium (via Kalium JNI) + JNA | **NaCl crypto** — the app's E2E crypto (`api/crypto`: SealBox, SecretBox, KeyPair, RecoveryKey, NonceGenerator). Central to pairing & key exchange. | — |
| `com.github.barteksc.pdfviewer` + `com.shockwave.pdfium` | AndroidPdfViewer + Pdfium | in-app PDF rendering (EULA/guides) | — |
| `com.airbnb.lottie` | Lottie | vector animations | — |
| `com.bumptech.glide` | Glide | image loading/caching | **Yes** (image URLs) |
| `com.github.ybq.android` | ybq (Android-SpinKit likely) | loading spinners | — |
| `com.aigestudio.wheelpicker` | WheelPicker | picker UI (timezone/date) | — |
| `me.zhanghai.android` | zhanghai (MaterialProgressBar / material widgets) | UI | — |
| `se.emilsjolander.stickylistheaders` | StickyListHeaders | list UI | — |
| `rx.*` | RxJava 1 + RxAndroid | reactive (legacy, likely libsodium-android or scanner dep) | — |
| `javax.inject` | JSR-330 | DI annotations | — |
| `org.jetbrains`/`org.intellij`/`kotlin*` | Kotlin runtime + annotations | language | — |

**No dedicated 3rd-party analytics/telemetry SDKs** (no Amplitude, Segment, Braze, Intercom, Mixpanel, Adjust, AppsFlyer). Telemetry is **Firebase Analytics + Crashlytics only**, plus the app's own `analytics/*` REST endpoints on embodied.com (insights/pages). Endpoints worth knowing/blocking for a clean local run: `*.crashlytics.com`, `firebase-settings.crashlytics.com`, Google measurement/`app-measurement.com`, FCM. None are load-bearing for core function.

---

## 5. App's own code — package inventory (`com.embo.embodied.parent`)

| Package | Role |
|---|---|
| *(root)* | App/base infra: `EmbodiedApplication` (Application), `LaunchActivity` (launcher+deeplink), `MainActivity`, `BaseActivity/BaseFragment/BaseDialogFragment/BaseBottomSheetDialogFragment`, `ContextWrapper` (locale), `BuildConfig`, databinding mapper. |
| `api` | **Backend core.** `APIService` (Retrofit interface), `Config` (all endpoint paths + base-URL/build-mode switching), `RequestManager` (OkHttp/Retrofit setup, all API calls, token/redirect state), `DataManager`, `ResponseManager`, `CryptoManager`, `SecureSharedPreference`, `SharedKeys` (prefs keys). |
| `api/crypto` | **NaCl/libsodium crypto layer:** `KeyPair`, `PublicKey`, `SealBox`, `SecretBox`, `RecoveryKey`, `NonceGenerator`, `Encoder`, `CryptoHelper`, `ObjectSerializer`, `StringHelper`. E2E key exchange + recovery-key derivation. |
| `api/interfaces` | Callback interfaces (`ResponseCallback`, `TokenCallback`, `DeepLinkCallback`, `AUIDCallback`, `WakeUpMoxieCallback`, etc.). |
| `api/models` | Data models: `User`, `Child`, `Robot`, `MobileDevice`, `TokenResponseModel`, `IdentityVerification`, `RedirectModel`, `FAQModel`, plus nested `user/`, `robot/`, `assistant/`, `insights/`, `teletherapy/`, `network_tests/`, `help/` model subpackages. `UserAttributes` includes an **`iot-endpoint`** integer (see §7). |
| `login` | Auth UI: `LoginFragment`, `SignUpFragment`, `CheckEmailFragment`, `EmailVerificationDialogFragment`, `RegistrationCodeFragment`, `SignedInFragment`, `OrganizationDetailsFragment` (Pro), `LoginHelper`, consumer-vs-Pro warning dialogs. |
| `onboarding` | Splash + welcome/intro: `SplashScreenFragment`, `OnboardingWelcomeFragment`, `PageLayoutAdapter`; `setup_instructions/` (physical setup: `SetupSpaceFragment`). |
| `coppa` | COPPA/child-privacy consent: `CoppaFragment`, `IdentityCheckFragment`, `PrivacyPolicyFragment`, `PrivoWebViewFragment` (**Privo** identity/consent verification via WebView), `IncompleteAgreementActivity`. |
| `pair_moxie` | **Robot pairing:** `PairMoxieActivity`, Wi-Fi (`PairMoxieWifiFragment`, `WifiBand`), QR (`PairMoxieQrCodeFragment`), `ProtoPairing` + `JSONPairing` (QR payload builders), `PairingMode`, `MoxieConnectedFragment`/`Pro`, `RestoreMoxieFragment`, various error/help dialogs. |
| `child_info` | Child profile management: add/edit/list children, approval, mentor info, `MoxiePronunciation`. |
| `child_info/content_preferences` | Child content/personalization: interests, personality, eye color, accessibility features, activity prefs, family members, consent verification. |
| `child_info/customization` | Moxie face/reward customization (`CustomizationFragment`, rewards adapters/controllers). |
| `main` | Post-login shell: `BottomNavigator`, `FAQFragment`, `FetchUserTimer` (polls user state). |
| `main/account` | Account settings: info, change/verify email, deactivate, revoke consent, sign-out, export recovery key. |
| `main/activity` | Child "activities" list/feed. |
| `main/assistant` | Parent "assistant"/resources: details, switch mentors, resources, in-app `WebViewFragment`. |
| `main/insights` | Analytics/insights dashboards: charts, date selection, per-child reports. |
| `main/moxie` | Moxie device screens: status, settings, mentors, OTA status, offline info, troubleshooting, user guide, accessibility features, explainer video. |
| `messages` | In-app messages/inbox: list, details, swipe-to-archive. |
| `notification` | `NotificationService` (foreground svc), `NotificationsFragment`, `NotificationUtils`, `INotification`. |
| `recovery_key` | Encryption recovery-key enter/export flows. |
| `timezone` | Timezone search/selection & sync. |
| `pdf` | `PDFReaderActivity` / PDF viewing (EULA, guides). |
| `video_player` | ExoPlayer-based `VideoPlayerActivity`/`VideoPlayerFragment`. |
| `graphics` | Custom chart/graph views (ring graph, spark line, stacked-area/stack graph) for insights. |
| `firebase` | `MessagingService` (FCM) + `Analytics` (FirebaseAnalytics wrapper). |
| `viewmodel` | MVVM VMs: Activities, AnalyticsDetails, Assistant, ContentPreferences, Events, Insights, Notifications, RobotInfo, SubDetails, **Teletherapy**, UserInfo, `DataResources`. |
| `utils` | Misc helpers: dialogs, custom views (`CustomButton`/`CustomEditText`), image/animation helpers, `CodeVerification`, `AttemptCounter`, `ForcedLogoutReason`, `Brightness`, HTML/text helpers, `Log`, `Utils`, `RestorationValidator`. |
| `recycler` | Base RecyclerView adapter/holder. |
| `databinding` / `generated` / `generated/callback` | Auto-generated Android data-binding classes (~310 files). |

**Notable domain features:** teletherapy (therapist access to Moxie), Pro/organization accounts, COPPA + Privo identity verification, E2E-encrypted user data with a user recovery key, robot OTA management, and Moxie Wi-Fi/QR pairing.

---

## 6. Version / build info
- `versionName` **2.2.2**, `versionCode` **249** (confirmed manifest `platformBuildVersion*`=34, apktool.yml, and `BuildConfig`).
- `minSdkVersion` **23** (Android 6.0), `targetSdkVersion` **34** (Android 14), `compileSdkVersion` **34**.
- `BuildConfig`: `APPLICATION_ID=com.embo.embodied.parent`, `BUILD_TYPE=release`, `DEBUG=false`. No feature flags in BuildConfig — environment selection is a runtime SharedPreferences value, not a build variant.
- Distributed as an **Android App Bundle split APK** (`isSplitRequired=true`, requiredSplitTypes `base__abi,base__density`; Play stamp = distribution APK).

---

## 7. Hardcoded backend hostnames / URLs

**Primary API base URL is runtime-selectable** via `Config.getBaseUrl()` driven by an enum `BuildMode` persisted in SharedPreferences key `SharedKeys.BUILD_MODE` (default = `PRODUCTION`). Switching env = one prefs write via `Config.setBuildMode(...)` — no rebuild needed. Base URLs (path `api/` appended for API calls):

| BuildMode | Base URL |
|---|---|
| STAGING | `https://client-service-staging-api.embodied.com/` |
| **PRODUCTION (default)** | `https://client-service-api.embodied.com/` |
| DEVELOP | `https://client-service-develop-api.embodied.com/` |
| CHINA | `https://client-service-cn-api.embodied.com/` |
| HONG_KONG | `https://client-service-hk-api.embodied.com/` |

Retrofit is built in `RequestManager.initRetrofit()` with `baseUrl(Config.getBaseUrl(true))`, Gson converter, plain OkHttp + a User-Agent network interceptor. **No pinning** (see §2). Auth is app-owned OAuth: `oauth/token` (password grant), `login/start` `login/register` `login/finish`. Endpoint path constants are all in `Config.java` (e.g. `users/me`, `robots/{id}`, `robots/{id}/wakeup`, `robots/{id}/ota_status`, `mobile-devices`, `analytics/pages/*`, `teletherapy/*`, `grl/code`, `secret-key-collection`).

**All other hostnames found** (grep across embo Java, all smali, and resources):

| Host / URL | Where | Purpose |
|---|---|---|
| `client-service-{,staging-,develop-,cn-,hk-}api.embodied.com` | Config/smali | **backend API** (above) |
| `support.embodied.com` | Config | Zendesk-style help center + article attachments (FAQ, guides, EULA/mission-book PDFs) |
| `embodied.com` | Config | marketing blog (SEL learn-more) |
| `moxierobot.com` | Config | official product page |
| `storage.googleapis.com/asset-store-bucket-client-service-production-2315/...` | Config | **onboarding video + client-service asset bucket** (GCS). Bucket name `asset-store-bucket-client-service-production-2315` — likely also serves other client-service assets/media. |
| `parent-app-245020.firebaseio.com` | strings.xml | Firebase RTDB URL (configured, client lib not present — likely unused) |
| `firebase-settings.crashlytics.com` | smali | Crashlytics settings |
| `casel.org` | resources | external SEL org link (informational) |
| `journeyapps.com`, `github.com`, `schemas.android.com` | resources | library/attribution/xml-namespace strings (not runtime endpoints) |
| `play.google.com` | Config/manifest | Play store link / stamp |
| `embo.page.link` | manifest | **Firebase Dynamic Links** deep-link domain (§1.6) |

**No MQTT/AWS-IoT/PubNub/WebSocket hostnames are hardcoded in the app.** However `UserAttributes` carries a server-provided **`iot-endpoint`** field (`@SerializedName("iot-endpoint")`, an **Integer** index — not a URL). In `PairMoxieWifiFragment`, `ProtoPairing(...).toQRString(..., iotEndpoint)` embeds this integer into the pairing QR code the robot scans. So the robot's IoT/MQTT broker selection is chosen by an **integer code delivered from the backend to the app, then handed to the robot via QR** — the actual broker host lives in robot firmware / server config, not in this app. For interop, the replacement server must supply a valid `iot-endpoint` value (and the corresponding broker the robot firmware maps that index to). This is the key cross-subsystem hook: the parent app never talks to the robot's IoT broker directly; it only provisions Wi-Fi + an iot-endpoint index + crypto keys during pairing.

---

## Key takeaways for the interop/repair effort
1. **No cert pinning, no network security config, no custom TLS** → MITM/redirect is easy; only obstacle is the default "user CA untrusted," solved by repackaging (no pin to strip) or root/emulator.
2. **Backend base URL is a runtime SharedPreferences switch** (`SharedKeys.BUILD_MODE`) across 5 environments, all `client-service-*.embodied.com` → a local server can impersonate `client-service-api.embodied.com` (or add a 6th env by patching `Config`).
3. **Auth is app-owned OAuth** (`oauth/token` password grant) against embodied.com — not Firebase Auth. Must be reimplemented server-side.
4. **Deep links depend on Firebase Dynamic Links** (`embo.page.link`) which Google has shut down — email verification / redirect flows will need replacing.
5. **Pairing hands the robot: Wi-Fi creds + an `iot-endpoint` index + NaCl public keys via QR** (libsodium). The robot's actual MQTT/IoT broker is not in this APK — the server must provide a working `iot-endpoint` value the robot firmware recognizes.
6. **Telemetry is only Firebase (Analytics/Crashlytics) + Google DataTransport** — safe to block; no third-party trackers.
