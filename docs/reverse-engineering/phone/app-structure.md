# Parent App — Manifest, Components, SDKs & Package Inventory

**Summary.** `com.embo.embodied.parent` 2.2.2 (versionCode 249) is an ordinary Retrofit/OkHttp app with Firebase
push and analytics. For anyone replacing the backend, three facts matter most:

- **No certificate pinning and no network-security config.** The only barrier to MITM is the default rule that user-installed CAs are not trusted.
- **The backend is one of five hardcoded `client-service-*.embodied.com` hosts**, and a hidden prefs switch chooses between them.
- **Deep links depend on the defunct Firebase Dynamic Links** domain `embo.page.link`.

Auth is the app's own passwordless OAuth ([`rest-api.md`](rest-api.md)), not Firebase Auth.

> Clean-room notes on the original app. Class and package names are locations *inside that app*, not files in
> this repo. The values below come from the apktool manifest, `apktool.yml`, `BuildConfig`, and the decompiled sources.

## 1. Identity and build

| Item | Value |
|---|---|
| Package / Application class | `com.embo.embodied.parent` / `com.embo.embodied.parent.EmbodiedApplication` |
| versionName / versionCode | **2.2.2** / **249** (manifest `platformBuildVersion*`=34, `apktool.yml`, and `BuildConfig` agree) |
| minSdk / targetSdk / compileSdk | 23 (Android 6.0) / 34 (Android 14) / 34 |
| `BuildConfig` | `APPLICATION_ID=com.embo.embodied.parent`, `BUILD_TYPE=release`, `DEBUG=false`. It has no feature flags; the environment is chosen at runtime in prefs ([rest-api §1.1](rest-api.md#11-base-url-and-build-mode)). |
| Packaging | Android App Bundle split APK: `isSplitRequired="true"`, requiredSplitTypes `base__abi,base__density`, `com.android.vending.splits.required=true`, `splits=@xml/splits0`, Play stamp source `https://play.google.com/store`, `derived.apk.id=2` |

## 2. AndroidManifest

### 2.1 Permissions

| Permission | Notes |
|---|---|
| `INTERNET`, `ACCESS_NETWORK_STATE`, `WAKE_LOCK`, `VIBRATE` | |
| `ACCESS_WIFI_STATE` | Wi-Fi pairing |
| `WRITE_SETTINGS` | brightness control (`utils/Brightness`) |
| `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC` | `NotificationService` (Android 14 typed FGS) |
| `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` | reading the connected SSID during pairing |
| `POST_NOTIFICATIONS` | Android 13+ runtime notification permission |
| `com.google.android.c2dm.permission.RECEIVE` | FCM |
| `com.google.android.gms.permission.AD_ID` | advertising ID (GMS measurement) |
| `com.google.android.finsky.permission.BIND_GET_INSTALL_REFERRER_SERVICE` | Play install referrer |

The manifest declares no camera permission. It requests camera **features** (front camera, autofocus, flash) with `required="false"`,
for the bundled ZXing/journeyapps scanner. During pairing the phone only *displays* a QR. Wi-Fi is also a feature with `required="false"`.
`<queries>` holds a single `SENDTO` intent with `data scheme="*"`, used to find an email client. Application attributes:
`allowBackup="false"`, `fullBackupContent="false"`, `extractNativeLibs="false"` (native `.so` files for libsodium, JNA,
pdfium, and ExoPlayer load straight from the APK), and `largeHeap="true"`.

### 2.2 Components

| Kind | Component | Exported | Notes |
|---|---|---|---|
| Activity | `LaunchActivity` | **true** | singleTask; **MAIN/LAUNCHER** plus the deep-link filter (§2.3) |
| Activity | `MainActivity` | false | singleTop; the post-login UI host, with no launcher filter |
| Activity | `pair_moxie.PairMoxieActivity` | false | singleTop; pairing flow |
| Activity | `child_info.EditChildInfoActivity` | false | singleTop |
| Activity | `pdf.PDFReaderActivity` | false | EULA and guides |
| Activity | `video_player.VideoPlayerActivity` | false | singleTop; ExoPlayer |
| Activity | `main.moxie.FullScreenOtaStatusActivity` | false | singleTop |
| Activity | `coppa.IncompleteAgreementActivity` | false | singleTop; COPPA gate |
| Activity | `com.journeyapps.barcodescanner.CaptureActivity` | default | ZXing capture (from the library) |
| Activity | `GoogleApiActivity`, `PlayCoreDialogWrapperActivity` | false | GMS / Play Core in-app update |
| Service | `notification.NotificationService` | default | `foregroundServiceType="dataSync"`, singleTask |
| Service | `firebase.MessagingService` | false | the app's FCM subclass (`com.google.firebase.MESSAGING_EVENT`) |
| Service | `com.google.firebase.messaging.FirebaseMessagingService` | false | library fallback, priority `-500`, directBootAware |
| Service | `ComponentDiscoveryService`, `TransportBackendDiscovery`, `JobInfoSchedulerService`, `AppMeasurementService`, `AppMeasurementJobService` | false | Firebase registrars, DataTransport CCT, GA measurement |
| Receiver | `com.google.firebase.iid.FirebaseInstanceIdReceiver` | **true** | permission `c2dm.permission.SEND`, action `c2dm.intent.RECEIVE` |
| Receiver | `AlarmManagerSchedulerBroadcastReceiver`, `AppMeasurementReceiver` | false | |
| Provider | `FirebaseInitProvider` | — | authority `com.embo.embodied.parent.firebaseinitprovider` |
| Provider | `ProcessLifecycleOwnerInitializer` | — | authority `com.embo.embodied.parent.lifecycle-process` |

Only `LaunchActivity` and the FCM receiver are exported. The manifest declares no app-owned `FileProvider`.

### 2.3 Deep links

`LaunchActivity` has the standard launcher filter and an **App Link** (`autoVerify="true"`, `VIEW`/`DEFAULT`/`BROWSABLE`,
`https://embo.page.link`). **There is no custom `moxie://` scheme.** Incoming links go through Firebase Dynamic Links and are handled by
`BaseActivity` and `api/interfaces/DeepLinkCallback`. Google has shut down Dynamic Links, so any email or invite links on
`embo.page.link` no longer work unless something intercepts them. The login email's `login-code` link format is in
[rest-api §2](rest-api.md#2-authentication-flow).

### 2.4 meta-data

The manifest's meta-data entries are: `default_notification_icon` → `@drawable/ic_notification`, `default_notification_color` → `@color/colorPrimary`,
`com.google.android.gms.version`, the Firebase registrars, and the datatransport backend `cct`. There is **no `networkSecurityConfig` and no API-key
meta-data**. The Google API key lives in resources (§4).

## 3. Network security (MITM / redirect)

**There is no `network_security_config.xml`.** `res/xml` holds only `splits0.xml` and `standalone_badge_*`. `<application>` has
no `networkSecurityConfig` or `usesCleartextTraffic` attribute, and the strings `trust-anchors`, `pin-set`, and
`cleartextTrafficPermitted` appear nowhere. The code agrees: `RequestManager.initRetrofit()` builds a plain `OkHttpClient`, and nothing under
`com/embo/` uses `CertificatePinner`, `sslSocketFactory`, `X509TrustManager`, or `hostnameVerifier`.

With the platform defaults for targetSdk 34:

- **No pinning.**
- **Cleartext HTTP is blocked**, so a replacement server must serve HTTPS.
- **User-added CAs are untrusted** on API 24+. Workarounds: (a) put the CA in the system store on a rooted device, (b) repackage the APK with a
  `network_security_config.xml` that trusts user CAs (there is no pin to strip), or (c) use an emulator with an injectable system CA.
  A DNS override then redirects one of the five hardcoded hosts.

## 4. Firebase / Google config

`google-services.json` is compiled into `res/values/strings.xml`:

| Key | Value |
|---|---|
| `google_app_id` | `1:376761969826:android:8a6885c32a08768ed57bfb` |
| `project_id` | `parent-app-245020` |
| `gcm_defaultSenderId` | `376761969826` |
| `google_api_key` / `google_crash_reporting_api_key` | `AIzaSyBVog09a0czSc719JzThvSS6SZ0BkW-DKk` |
| `default_web_client_id` | `376761969826-q1022eu5afi8eopvsp08kca84cvg23h3.apps.googleusercontent.com` |
| `firebase_database_url` | `https://parent-app-245020.firebaseio.com` |
| `com.crashlytics.android.build_id` | `00000000000000000000000000000000` |

Firebase services in use:

- **FCM.** `firebase.MessagingService.onMessageReceived` builds notifications from `remoteMessage.getData()`, and the token is registered through
  `mobile-devices`.
- **Dynamic Links** (defunct, see §2.3).
- **Crashlytics** (`Config.java` calls `FirebaseCrashlytics`).
- **Analytics/GA4.** `firebase/Analytics.java` wraps `setCurrentScreen()`.
- **Installations** (FID).
- **DataTransport** (CCT).

The app configures an RTDB URL but ships **no RTDB client**, and it has no RemoteConfig either. **Firebase Auth and Google Sign-In are not used**, even though
`default_web_client_id` is set.

## 5. Third-party SDKs

| Package | Library | Role | Network? |
|---|---|---|---|
| `okhttp3`, `okio`, `retrofit2` | OkHttp, Retrofit | REST to `client-service-*.embodied.com` | **yes** |
| `com.google.gson` | Gson | JSON | — |
| `com.google.firebase.*`, `com.google.android.gms.*`, `com.google.android.datatransport` | Firebase, Play Services, DataTransport | FCM, Crashlytics, Analytics, Dynamic Links, Installations, telemetry batching | **yes** |
| `com.google.android.play` | Play Core | in-app updates, split install | yes |
| `com.google.android.exoplayer2` | ExoPlayer | onboarding and help video | yes (media) |
| `com.bumptech.glide` | Glide | image loading | yes (image URLs) |
| `com.google.android.material`, `com.google.android.flexbox`, `com.google.common` | Material, Flexbox, Guava | UI and utilities | — |
| `com.google.crypto.tink` | Tink | backs `EncryptedSharedPreferences` | — |
| `com.google.zxing`, `com.journeyapps.barcodescanner` | ZXing | QR **generation** (pairing) and a scanner | — |
| `org.libsodium.jni`, `org.kaliumjni.lib`, `com.sun.jna` | libsodium (Kalium JNI) + JNA | the app's E2E crypto ([`crypto-and-keys.md`](crypto-and-keys.md)) | — |
| `com.github.barteksc.pdfviewer`, `com.shockwave.pdfium` | AndroidPdfViewer, Pdfium | PDF rendering | — |
| `com.airbnb.lottie`, `com.github.ybq.android`, `com.aigestudio.wheelpicker`, `me.zhanghai.android`, `se.emilsjolander.stickylistheaders` | Lottie, SpinKit, WheelPicker, material widgets, StickyListHeaders | UI | — |
| `rx.*`, `javax.inject`, `kotlin*`/`org.jetbrains`/`org.intellij` | RxJava 1 + RxAndroid, JSR-330, Kotlin runtime | language and runtime | — |

The app bundles **no third-party analytics SDKs** (no Amplitude, Segment, Braze, Intercom, Mixpanel, Adjust, or AppsFlyer). Its telemetry is Firebase
Analytics plus Crashlytics, plus its own `analytics/*` REST endpoints. You can block `*.crashlytics.com`,
`firebase-settings.crashlytics.com`, `app-measurement.com`, and FCM without breaking anything core.

## 6. The app's own packages (`com.embo.embodied.parent`)

| Package | Role |
|---|---|
| *(root)* | `EmbodiedApplication`, `LaunchActivity`, `MainActivity`, `Base{Activity,Fragment,DialogFragment,BottomSheetDialogFragment}`, `ContextWrapper` (locale), `BuildConfig` |
| `api` | `APIService`, `Config`, `RequestManager`, `DataManager`, `ResponseManager`, `CryptoManager`, `SecureSharedPreference`, `SharedKeys` |
| `api/crypto` | `KeyPair`, `PublicKey`, `SealBox`, `SecretBox`, `RecoveryKey`, `NonceGenerator`, `Encoder`, `CryptoHelper`, `ObjectSerializer`, `StringHelper` |
| `api/interfaces` | callbacks (`ResponseCallback`, `TokenCallback`, `DeepLinkCallback`, `AUIDCallback`, `WakeUpMoxieCallback`, …) |
| `api/models` | `User`, `Child`, `Robot`, `MobileDevice`, `TokenResponseModel`, `IdentityVerification`, `RedirectModel`, `FAQModel`, plus the `user/`, `robot/`, `assistant/`, `insights/`, `teletherapy/`, `network_tests/`, and `help/` subpackages |
| `login` | `LoginFragment`, `SignUpFragment`, `CheckEmailFragment`, `EmailVerificationDialogFragment`, `RegistrationCodeFragment`, `SignedInFragment`, `OrganizationDetailsFragment`, `LoginHelper`, consumer/Pro warning dialogs |
| `onboarding` | `SplashScreenFragment`, `OnboardingWelcomeFragment`, `PageLayoutAdapter`; `setup_instructions/SetupSpaceFragment` |
| `coppa` | `CoppaFragment`, `IdentityCheckFragment`, `PrivacyPolicyFragment`, `PrivoWebViewFragment` (Privo verification), `IncompleteAgreementActivity` |
| `pair_moxie` | pairing ([`pairing-and-robot.md`](pairing-and-robot.md#9-file-map-inside-the-original-app)) |
| `child_info` (+ `content_preferences`, `customization`) | child profiles, approval, mentor info, `MoxiePronunciation`; interests, personality, eye color, accessibility, family; face and reward customization |
| `main` (+ `account`, `activity`, `assistant`, `insights`, `moxie`) | `BottomNavigator`, `FAQFragment`, `FetchUserTimer`; account settings (email change, deactivate, revoke consent, sign-out, export recovery key); activity feed; assistant resources + `WebViewFragment`; insights charts; Moxie status, settings, OTA, troubleshooting, user guide |
| `messages`, `notification` | inbox (swipe-to-archive); `NotificationService`, `NotificationsFragment`, `NotificationUtils`, `INotification` |
| `recovery_key`, `timezone`, `pdf`, `video_player`, `graphics` | recovery key enter/export; timezone sync; PDF; ExoPlayer; ring, spark-line, and stacked graphs |
| `firebase` | `MessagingService`, `Analytics` |
| `viewmodel` | Activities, AnalyticsDetails, Assistant, ContentPreferences, Events, Insights, Notifications, RobotInfo, SubDetails, Teletherapy, UserInfo, `DataResources` |
| `utils`, `recycler` | dialogs, `CustomButton`/`CustomEditText`, `CodeVerification`, `AttemptCounter`, `ForcedLogoutReason`, `Brightness`, `Log`, `Utils`, `RestorationValidator`; base RecyclerView adapter |
| `databinding`, `generated` | generated data-binding classes (~310 files) |

Domain features: teletherapy, Pro/organization accounts, COPPA + Privo, E2E-encrypted child data with a recovery key, OTA
management, and Wi-Fi/QR pairing.

## 7. Hostnames found (embo Java, smali, resources)

| Host / URL | Where | Purpose |
|---|---|---|
| `client-service-{,staging-,develop-,cn-,hk-}api.embodied.com` | Config | **backend API** ([rest-api §1.1](rest-api.md#11-base-url-and-build-mode)) |
| `support.embodied.com` | Config | help center and article attachments (FAQ, guides, EULA and mission-book PDFs) |
| `embodied.com` | Config | marketing blog (SEL learn-more) |
| `moxierobot.com` | Config | product page |
| `storage.googleapis.com/asset-store-bucket-client-service-production-2315/...` | Config | onboarding video and the client-service GCS asset bucket |
| `parent-app-245020.firebaseio.com` | strings.xml | RTDB URL (no client library, so probably unused) |
| `firebase-settings.crashlytics.com` | smali | Crashlytics settings |
| `casel.org` | resources | external SEL link |
| `journeyapps.com`, `github.com`, `schemas.android.com` | resources | attribution and namespace strings, not endpoints |
| `play.google.com` | Config, manifest | store link and stamp |
| `embo.page.link` | manifest | Firebase Dynamic Links (§2.3) |

**The app hardcodes no MQTT, AWS-IoT, PubNub, or WebSocket host.** The robot's cloud is chosen by the integer `iot-endpoint` on the user record,
which `PairMoxieQrCodeFragment` stamps into the pairing QR ([`pairing-and-robot.md` §2](pairing-and-robot.md#2-iot-endpoint)).
The app never talks to the robot's broker. During pairing it hands over only Wi-Fi credentials, that index, and the key **seed**.

---
📖 [Phone-side index](README.md) · [REST API](rest-api.md) · [Reverse-engineering index](../README.md)
