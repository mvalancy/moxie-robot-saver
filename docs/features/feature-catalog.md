# 10 — Exhaustive Feature Catalog (Moxie Parent App)

`com.embo.embodied.parent` v2.2.2 (versionCode 249). Decompiled Java under
`.../jadx-out/sources/com/embo/embodied/parent/`, resources under `.../apktool-out/`.

This catalog enumerates **every user-facing and hidden/dev feature** for the clean-room server
reimplementation. Auth transport, crypto, and the byte-level pairing handshake are covered in maps
`01`–`03`; this doc focuses on **screens, settings/toggles, endpoints per feature, and hidden/dev
surface**. Endpoint constants are resolved from `api/Config.java`; all paths are relative to the
env base URL (prod `https://client-service-api.embodied.com/api/`).

---

## 0. App shape & navigation map

### Entry / routing
- **`LaunchActivity`** decides first screen on cold start: logged-in + valid recovery key →
  `MainActivity`; pending restore email-verify → CheckEmail; forced-login/error → LoginFragment;
  else → SplashScreen. Forwards deep links to the current `BaseFragment.handleDeepLink()`.
- **`MainActivity`** hosts the signed-in app; **`BaseActivity`** holds ~all navigation helpers
  (`switchTo…`), forced-logout, FCM token registration, error dialogs.

### Bottom navigation (`main/BottomNavigator.java`, `res/menu/bottom_nav_menu.xml`) — 4 tabs
| Tab id | Title | Fragment | Area |
|---|---|---|---|
| `navigation_control_center` | Moxie | `main/moxie/MoxieFragment` | robot home/status (§7) |
| `navigation_assistant` | Resources | `main/assistant/ResourcesFragment` | **the "assistant" pkg = content/help hub** (§8/§13) |
| `navigation_insights` | Insights | `main/insights/InsightsFragment` | analytics (§9) |
| `navigation_settings` | Account | `main/account/AccountFragment` | account (§2) |
- The Resources tab shows an unread badge (`getOrCreateBadge(navigation_assistant)`), driven by
  `AssistantModel.messages_unread`.

### Consumer vs "Moxie Pro" (clinician) split
Almost every flow branches on `Config.isProVersion()` (== `Config.isClinicianUser()`): user is a
`User.UserType.clinician`, **or** the locally-chosen `isMoxieProModeLocally` flag (set on Splash /
Login). Pro swaps fragment variants (`…ProFragment`), wording ("mentor" vs "child"), step counts,
adds Organization/registration-code screens, teletherapy, multi-child, and the child-list search/sort.

---

## 1. Onboarding & setup

Screen order (fresh install, Consumer): **Splash (pick mode) → Onboarding welcome (3 wizard pages)
→ Login → CheckEmail → EmailVerification → SignUp → recovery-key → (COPPA / child info / Privo
identity) → pairing → SetupSpace rules → MoxieConnected → Main.** Pro swaps SignUp→RegistrationCode
→OrganizationDetails and Consumer fragments for Pro variants.

| Feature | Class | Notes / endpoint |
|---|---|---|
| Splash / mode chooser | `onboarding/SplashScreenFragment` | Two cards set `Config.isMoxieProModeLocally` (Consumer vs Pro), then → OnboardingWelcome. Sets `Config.isInitialSetup=true`. No API. |
| Welcome wizard | `onboarding/OnboardingWelcomeFragment` + `PageLayoutAdapter` | ViewPager2 of **exactly 3 pages** (`onboarding_meet_moxie`, `onboarding_track_progress`, `onboarding_getting_started`); descriptions swap Pro vs Consumer. "Sign Up" → LoginFragment; "purchase Moxie" → `https://moxierobot.com`. Sets pref `WIZARD_PAGE_DISPLAYED=true` (`Config.isWizardPageDisplayed`). |
| Setup-space rules | `onboarding/setup_instructions/SetupSpaceFragment` + `InstructionLayoutAdapter` | 3 tutorial pages (`rule_1..3`), animated images, hardware-back disabled. "Next" → `MoxieConnected(Pro)Fragment`. |
| Explainer video | `main/moxie/MoxieExplainerVideoFragment` | Plays `Config.ONBOARDING_VIDEO_URL` (`storage.googleapis.com/.../app-onboarding.mp4`). Shown once, gated by pref `EXPLAINER_VIDEO_ON_PAIRING` (`Config.isExplainerVideoDisplayedOnPairing`). |
| EULA | `pdf/PDFReaderActivity` via `BaseActivity.switchToEULAActivity()` | Reads bundled asset `eula_license.pdf`. Viewable (from Account), **not a forced accept-gate**. Analytics screen `EULA_Page`. |
| Privacy policy | `coppa/PrivacyPolicyFragment` | Loads bundled `file:///android_asset/privacy_policy.html` (or `_pro`). Doubles as the COPPA consent-grant screen (§4). |
| Permissions | `AndroidManifest.xml` | Declared: WiFi state, INTERNET, network state, WAKE_LOCK, **WRITE_SETTINGS**, FOREGROUND_SERVICE(+DATA_SYNC), **FINE/COARSE LOCATION**, VIBRATE, POST_NOTIFICATIONS, C2DM RECEIVE, AD_ID. Camera is `uses-feature` (QR). Location+camera prompted at pairing. |
| Notification opt-in | `notification/NotificationsFragment` | "Yes/No" push opt-in during onboarding (NOT the message list). "Yes" enables mission+battery notifications → `PUT users/me`, opens OS settings if disabled. |

---

## 2. Account

All profile edits, notification toggles, marketing consent, org details, consent-revocation funnel
through **`PUT users/me`** (`UserInfoViewModel.updateUserRequest`); deletion is **`DELETE users/me`**;
email change is a two-step `change-email-request` → `change-email`.

| Feature | Class | Endpoint / details |
|---|---|---|
| Account home | `main/account/AccountFragment` | Rows: Sign Out, License Agreement (EULA), Privacy Policy, Edit Account Info, Recovery-key (shown from local passphrase; long-press copies), Telehealth (conditional), version text. Toggles below. |
| Battery-notif toggle | AccountFragment `moxie_battery_switch` | `UserAttributes.batteryNotificationsEnabled` → `PUT users/me`. |
| Mission-notif toggle | AccountFragment `new_mission_switch` | `UserAttributes.missionNotificationsEnabled` → `PUT users/me`. |
| Edit profile | `main/account/AccountInfoFragment` | Edit first/last name (email read-only); Pro adds position + org details → `PUT users/me`. Sub-links to change-email, deactivate, revoke-consent, org details. |
| Change email | `main/account/ChangeEmailFragment` | New+confirm email → `POST users/me/change-email-request` (`changeEmailRequest`). |
| Change-email verify | `main/account/ChangeEmailVerificationFragment` | Code entry → `POST users/me/change-email` (`changeEmail`). Resend re-calls change-email-request. Handles `IN_USE`/`BAD_CODE`. |
| Delete/deactivate account | `main/account/DeactivateAccountFragment` | Must type the word `deactivate` to enable button → confirm → `DELETE users/me` (`deleteUser`) → logout. |
| Revoke consent | `main/account/RevokeConsentFragment` | Must type `revoke` → sets `UserAttributes.coppaConsentStatus=revoked` → `PUT users/me`. (COPPA data-processing consent, distinct from marketing opt-in.) |
| Marketing opt-in | SignUp `emailMeCheckbox` / EmailVerification re-prompt | Single bool `UserAttributes.shareEmailWithMarketing`. If false post-login, re-prompted via account-confirmation SignUp screen. (`shareAnonymousDataOptIn`/`shareUsageDataOptIn` fields exist on the model but have no dedicated toggle screen.) |
| Export recovery key | `main/account/ExportRecoveryKeyDialog` → `recovery_key/ExportRecoveryKeyFragment` | Local-only; key derived from passphrase/keypair. No API. |
| Sign out | `main/account/SignOutConfirmationDialog` | Reveals recovery key (tap=copy, long-press=share) before `BaseActivity.logOut()`. |

Account-level `UserAttributes` fields (from Kotlin metadata): `firstName, lastName, email,
emailVerifiedAt, publicKey, coppaConsentStatus, batteryNotificationsEnabled,
missionNotificationsEnabled, shareEmailWithMarketing, shareAnonymousDataOptIn, shareUsageDataOptIn,
hasBackups, timezoneSync, timezoneId, activeChildId, maxChildren, iotEndpoint, grlCodeStatus,
lastGrlCode, lastRestoredChildId, moxieImageState, organizationName/City/State/Type, proPosition,
supportsEyeColor, supportsFaceColor, unreadMessageCount, userType`.

---

## 3. Child management

- **Data model:** `Child` singleton (`children[]`, `data`=editing child, `maxCount` default 1) +
  `api/models/user/ChildrenModel` (the wire model). **Field-level PII encryption**: any
  `@SerializedName` ending in `-encrypted` (`first-name`, `last-name`, `nickname`, `birthday`,
  `gender`, `auid`, `likes-imaginative-play`, `self-regulation-tools-preferences`, `therapy-needs`,
  `volume-preference`, `calendar-events`) is encrypted client-side via `CryptoHelper`
  (`Child.asEncryptedData`/`asDecryptedData`). Always applied — not a dev toggle.
- **Fields:** first/last name, nickname (Moxie's spoken name), birthday, gender (there is **no
  separate pronoun field** — only `gender-encrypted`), plus derived `is16` / `is-adult`.

| Feature | Class | Endpoint / details |
|---|---|---|
| Create child (consumer) | `child_info/AddChildFragment` | first/last/nickname/birthday; sets `is16`(`Utils.is16AndMore`, ≥16) + `is-adult`(≥18). Save → `ChildInfoController`. |
| Create/edit (Pro / "add yourself") | `child_info/MentorInformationFragment` | Adds "Add yourself" checkbox (self-profile, birthday defaults 1970-01-01, name locked), inline play-prefs + family members. |
| Save controller | `child_info/ChildInfoController` | Encrypts model; new → `POST children` (`createChildren`), else `PUT children/{id}` (`updateChildren`). New child also syncs timezone; routes to identity-check/coppa/main. |
| Delete child | `child_info/ChildApprovalFragment.showDeleteChildDialog` | `deleteChildren` → `DELETE children/{id}`. Blocked with unpair warning if robot paired. |
| Child list | `child_info/ChildListFragment` (`…ProFragment` for clinician) | Shows `size/maxCount Added`; disables "add" at max. Pro list adds search + sort (`EFilterSpec{FIRST_NAME,LAST_NAME,BIRTHDAY}`, `EFilterClassification{all,approved,not_rejected}`). |
| Active-child switching | `child_info/ChildrenList` / `SwitchActiveChildFragment` | Sets `UserAttributes.activeChildId` → `PUT users/me`. `SwitchChildConfirmationDialog` confirms; `ConsentRequiredOnSwitchDialog` blocks switching to a pending/declined child. |
| Child hub | `child_info/ChildPersonalInfoFragment` | Moxie head preview + rows: info, customize, chat topics, activity prefs, SEL, accessibility, delete. |
| Pending info / resend | `child_info/ChildApprovalFragment` | `GET children/{id}/pending-info`; "resend email" (only when `pending`) → `POST children/{id}/resend-email`. |
| Name pronunciation | `child_info/MoxiePronunciation` | TTS preview → `POST help/pronounce` (`SpeechModel{speech}`); links to `MOXIE_PRONOUNCING_URL`. |

- **Age logic:** `EMentorAge{_15_AND_YOUNGER,_16_AND_17,_18_AND_OLDER}`. 16+ can skip Privo per-child;
  18+ (adult) takes self/adult path (skips child-consent-email, 2-step vs 3-step progress bar).
- **Max children:** `UserAttributes.maxChildren` (default 1) → `Child.maxCount` (via `DataManager`).
  `Config.userHasMultipleChildren()` (>1), `Config.isProAndMultiUserVersion()` (pro OR max>1).

`ChildrenModel` per-child fields (metadata): `auid, birthday, calendarEvents, childEmail,
childFirstName, contentPreferences, family, firstName/lastName/fullName, gender, grlConnectEnabled,
holidays/holidayEvents, inputSpeed, is16, isAdult, latestActivityAt, likesImaginativePlay,
moxieEyeColor, moxieFaceColor, nickname, privoStatus, rewardsChoices, scheduledSensitiveTopic,
selfRegulationTools, therapyNeeds, unlimitedTime, volumePreference`.

---

## 4. COPPA / Privo

Gating helpers in `Config`: `isNeedToProvideCOPPAConsent()`, `isNeedToProvideIdentityVerification()`,
`isClinicianUser()`, `getPrivoUrl()` = `"{base}privo-verification?access_token={token}"`.
Enums: `User.CoppaConsentStatus{unknown,granted,revoked}`;
`Child.PrivoStatus{none,pending,approved,declined,ok,adult}`;
`IdentityVerification.Status{initiated,succeeded,failed}`.

| Feature | Class | Endpoint / details |
|---|---|---|
| COPPA consent intro | `coppa/CoppaFragment` | "Grant" → PrivacyPolicy with `GRANT_CONSENT_PAGE` flag. Pro shows step "4 of 6". |
| Consent grant | `coppa/PrivacyPolicyFragment` | Agree → `coppaConsentStatus=granted` → `PUT users/me`. Decline → `logOut(coppaConsentRequired)`. |
| Identity-check intro | `coppa/IdentityCheckFragment` | "Next" → PrivoWebView. |
| Privo verification (WebView) | `coppa/PrivoWebViewFragment` | Loads `getPrivoUrl()`. On `/success`, polls `GET users/me` up to 30×/2s until `identityVerification` present → `onPass`; `/failure` → retry dialog. |
| Resume interrupted consent | `coppa/IncompleteAgreementActivity` | Chooses Coppa or Identity page to resume; back disabled. |
| Clinician per-child consent | `child_info/content_preferences/ConsentVerificationFragment` | Radio `Mode{CONSENT_SELECTION,CHILD_EMAIL,PARENT_EMAIL}`; sets `child-email` → save triggers Privo email to that address. |
| Per-child Privo email/status | `ChildApprovalFragment` | `POST children/{id}/resend-email`; `GET children/{id}/pending-info`. |

---

## 5. Recovery key / backups

- **Recovery key = diceware passphrase → Argon2id → 32-byte seed** (see map 02). The seed derives the
  Ed25519 signing key (pairing) and the SecretBox key (PII encryption). Persisted locally as
  `Config.getPassPhraseCode()` (pref `ppcrk`).
- **Export** — `recovery_key/ExportRecoveryKeyFragment` (via `ExportRecoveryKeyDialog`, and revealed
  in `SignOutConfirmationDialog`): displays the phrase, tap=copy, long-press=share. No API.
- **Enter/restore key** — `recovery_key/EnterRecoveryKeyFragment`: user re-enters the phrase to
  regenerate the signing key on a new device (validated by `RestorationValidator.checkRecoveryKey()`).
- **Backups (server)** — `UserAttributes.hasBackups`, `RobotAttributes.last-backup-at`. Cloud restore
  of a child's data is a **robot restore**: `POST robots/{id}/restores` (`RestoreRobotModel`), see §7
  and `pair_moxie/RestoreMoxieFragment`. `RestoreType{switch_child,new_child,restore,pairing}`;
  `RestoreStatus{initiated,declined,failed,succeeded}`. Pref `CHECK_RESTORE_FROM_BACKUP` flags the
  restore-from-backup intent during pairing.

---

## 6. Pairing & Wi-Fi

(Byte-level format fully decoded in map 03 — summarized here as features.)

- **Host:** `pair_moxie/PairMoxieActivity`; fragments `PairInstructionFragment` →
  `PairMoxieWifiFragment` → `PairMoxieQrCodeFragment` → `MoxieConnected(Pro)Fragment`.
- **PairingMode:** `WIFI_ONLY` vs full pairing. In `WIFI_ONLY` the QR carries only Wi-Fi (no signing
  key); full mode embeds the signing key + iot-endpoint.
- **QR modes (`Config.PairQRMode`):** `PAIR_JSON_TOKEN` (legacy JSON) and `PAIR_PROTO_KEY`
  ("PA"+Base64 protobuf, current). **`Config.getPairQRMode()` always returns `PAIR_PROTO_KEY`** — both
  branches return proto, so **JSON mode is dead code** and the `PAIRING_QR_MODE` pref is inert
  (see §15).
- **Registration handshake:** app registers `SHA-256(signingKey)` with server
  (`registerForPairing` → `pairing-info` / `secret-key-collection`), embeds the real key in the QR;
  robot scans, joins Wi-Fi, authenticates to cloud with the key, server matches the hash → binds robot.
- **Restore pairing:** `RestoreMoxieFragment` (cloud data restore) + `EnterRecoveryKeyFragment` (key
  restore). Restore differs by passing `userWantsToRestoreFromBackup` into `registerForPairing`.
- **Wi-Fi provisioning fields:** ssid, password, is_hidden, band (`WifiBand`: ANY/ONLY_50G/ONLY_24G).
- Support dialogs: `ConnectingTipsAndTricksDialog`, `PairingHavingTroubleDialog`, `PairingErrorDialog`,
  `PairingProhibitedWhilePendingDialog`. Troubleshooting link `URL_TROUBLESHOOTING_QR_PAIRING`.

---

## 7. Robot control & settings

**Robot state is split across 4 server resources:** `robots/{id}` attributes (`RobotAttributes`,
read-only status/firmware), `robots/{id}` robot-settings (`RobotSettingsAttributes`, tunables),
`children/{id}` (`ChildrenModel`, per-child Moxie personalization), `users/me` (account toggles).
Feature availability is gated by robot-advertised capability flags in
`RobotAttributes.device-settings.props` (`"1"` = enabled).

### Robot home / status — `main/moxie/MoxieFragment`
| Control | Endpoint |
|---|---|
| Wake up Moxie | `POST robots/{id}/wakeup` |
| Volume seekbar (10% steps) | `RobotSettingsAttributes.audioVolume` → `PUT robots/{id}` (robot-settings) |
| Face/eye color | display only here; edited in child-info (writes `children/{id}`) |
| Restore / cancel / retry | `POST robots/{id}/restores`; unpair via TroubleshootDialog |
- Status enum `MoxieStatus{UNPAIRED,PAIRED,RECONNECT,RESTORE_FAILED,RESTORE_IN_PROGRESS,OTA_IN_PROGRESS,OFFLINE}`;
  image state `Robot.ImageState{on,off,not_paired,wake_button,restoring_switch,restoring,restore_failed,not_paired_restore_failed,sleeping}`;
  battery thresholds min 0.19 / medium 0.39.

### Main settings — `main/moxie/MoxieSettingsFragment`
| Setting | Field / endpoint |
|---|---|
| Screen brightness | `screen-brightness` (0.1–1.0) → `PUT robots/{id}` |
| Audio-wake sensitivity | `audio-wake-set` = `AudioWakeSensitivity{off,low,high}`; gated `audio-wake` prop |
| Touch-wake / wake-button toggles | `touch-wake-enabled` / `wake-button-enabled`; gated `touch-wake`/`wake-button` |
| Set language | `POST robots/{id}/set-language` (`input_language_id`, `output_language_id`, `output_voice_id`); options via `GET help/language-support`; gated `app-language-support` |
| Time-zone sync | `UserAttributes.timezoneSync` (`TimeZoneSyncStatus{initial,automatic,manual}`) → `PUT users/me` |
| GRL connect | `POST grl/code` / `POST grl/revoke-all` (see §11) |
| OTA status polling | `GET robots/{id}/ota_status` |

### OTA update
- `main/moxie/MoxieOtaStatusFragment` (progress; `OtaStatusModel{code,percent,remaining,status,timestamp}`;
  skip only when `isInitialSetup`); `FullScreenOtaStatusActivity` (forced full-screen OTA, `SKIP_AVAILABLE`
  extra); `MoxieVersionWebDialog` (firmware/version web content). `OtaStatus{idle,pending,uploading,
  downloading,flashing,finalizing,complete}`.

### Reboot / factory reset / unpair
| Action | Endpoint |
|---|---|
| Reboot | `POST robots/{id}/reboot` |
| Unpair | `DELETE robots/{id}` |
| Unpair + factory reset | `DELETE robots/{id}?rfs=1` |
- Triggered from `TroubleshootDialog` / settings.

### Robot naming & appearance (per-child)
- `ChildrenModel.nickname` (Moxie's spoken name), `eye-color` (`EyeColor{green,blue,purple,brown,gold,teal}`),
  `face-color` (`FaceColor{blue,yellow,green,teal,pink,purple}`) → `PUT children/{id}`.
- Exact hex — Eyes: green=42D02B, blue=8491EF, purple=9437DE, brown=443319, gold=F4BF03, teal=38ADAE.
  Face: blue=BBCFE1, yellow=F0F055, green=9BDB9B, teal=7ED6DD, pink=E1A2A2, purple=C395D4.

### Accessibility (per-child) — §14
### Info dialogs
- `OfflineMoxieInfoDialog`, `TroubleshootDialog` (unpair), `UserGuideDialog` (PDF guides, one-time via
  `USER_GUIDE_DIALOG_DISPLAYED`), `MoxieExplainerVideoFragment`, `MoxieLetsPlayFragment`,
  `MentorsFragment` (approved child/mentor roster).

`RobotSettingsAttributes` full field list: `audio-volume, audio-wake-set, screen-brightness,
privacy-mode-enabled, touch-wake-enabled, wake-button-enabled, alarms(WakeAlarms),
weekday/weekend-bedtime-enabled/-starts-at/-ends-at`. `DeviceSettingsProps`: `app-language-support,
audio-wake, debug, playzone, rewards-support, schedule-sensitive, touch-wake, wake-alarms, wake-button`.

---

## 8. Content & activities

**The "assistant" package IS the Resources content/help hub** (`main/assistant/ResourcesFragment`).
Loaded from `GET help/home` (`AssistantModel{items[], messages[], messages_unread}`), cached
encrypted (`ASSISTANT_DATA_CACHE`). Hub tiles keyed by `EAssistantType`:

| EAssistantType | Route | Endpoint |
|---|---|---|
| `mission_set_guides` | DetailsListFragment (inline items) | — (from home) |
| `moxie_modules` | DetailsListFragment | `GET help/moxie-activities` (`MoxieActivityModel{activity,description,entrance_command,icon}`) |
| `global_commands` | DetailsListFragment | `GET help/moxie-commands` (`MoxieCommand{command,description,additional}`) |
| `tips_for_success` | TipsForSuccessFragment | `GET help/tips-for-success` (nested Panel→Piece→Subpiece/Tab; `ETipsPieceType{text,bold,tabs,bullet}`) |
| `assistant_training` | WebViewFragment (item.html) | — |
| `printables` | external browser (item.target) | — |
| `switch_mentors` | AssistantSwitchMentorsFragment (html) | — |
- Generic HTML tiles → `AssistantDetailsFragment`. List rows → `DetailsListFragment`/`DetailItemsAdapter`.
- `ResourcesExtrasFragment` = secondary extras list.

**Activity history (distinct, `main/activity/`)** — `ActivityFragment`: the child's activity timeline,
via `analytics/pages/{id}` family (`ActivityItemModel{approvalState(EApprovalState), duration,
startedAt/endedAt, type(EActivityType), readMore}`), with `DateSelector` windows.

**Missions / rewards / badges** — see §9. **Sensitive-conversations schedule** — see §12/scheduling.

---

## 9. Insights / analytics

Per-child dashboard; auto-refresh every 30s (`INSIGHTS_PAGE_UPDATE_TIME_MSEC`), response cached
(`INSIGHTS_DATA_CACHE`).

| Feature | Class | Endpoint |
|---|---|---|
| Dashboard | `main/insights/InsightsFragment` | `GET analytics/pages/insights` (params `auid, tz, window, tip=1, start/end, child_id`). If AUID empty → local placeholder (`showPlaceholder=true`, empty "hello" screen). |
| SEL categories | `main/insights/details/AnalyticsDetailsFragment` | `EAnalyticsType{cognitive,social,emotional}` → `GET analytics/pages/{cognitive|social|emotional}`. Detail has graph + panels (`EPanelType{_default,stack,suggestion}`). |
| Sub-details drill-down | `main/insights/subdetails/SubDetailsFragment` | `GET analytics/pages/details` (param `page`). |
| Missions & badges | `main/insights/missions_badges/MissionsBadgesFragment` | `EMissionType{MISSIONS,BADGES}` → `GET analytics/pages/{missions|badges}`. |
| Date/time-window selector | `main/insights/DateSelector` | `ETimeType{weekly,monthly,yearly,all}`; left/right steppers; theme label only for weekly. Selection persists statically, reset on logout. |
| Per-child report switch | `main/insights/SwitchChildReportDialog` | Bottom-sheet from `Child.getApprovedList()`; hidden when list empty. |
| Rewards / stars | `main/insights/rewards/StarProgressView` + `DialogRewardsInfo` | `GET children/{id}/rewards` (`starbits, starbitsPercent, lastLevelPoints, nextLevelPoints`); animated star ring. |
| AUID | `RequestManager.auid()` | Resolved **locally** from `child.auid` (no network); returns `"foo"` in demo mode. Encrypted variant `GET analytics/auid-encrypted`. |

**AUID sharing (help)** — `main/account/help/HelpFragment`: loads `GET help`
(`GetHelpModel{allow_share_auid, share_auid_*, share_auid_mode, encrypted_auids[]}`); "Share AUID"
decrypts each encrypted AUID client-side then `POST help/share-auid`
(`ShareAUIDModel{auids[], mode}`), `EShareAuidMode{temporary,permanent,revoke,revoke_all,none}`.
**Network test** — `HelpFragment`/`NetworkTest`: `GET network-tests` (spec) + `POST network-tests`
(results); requires Wi-Fi.

---

## 10. Messages / notifications

| Feature | Class | Endpoint |
|---|---|---|
| Messages list | `messages/MessagesFragment` | `GET notifications` (params `next`, `archived`); `NotificationsDataModel{messages[], next_token, has_archived, unread-message-count}`. Types `EMessagesType{app,moxie,urgent}`; read-status `EReadStatus{read,unread,archived}`; row types `EItemViewType{MESSAGE,LOADING_VIEW,ARCHIVED_VIEW,ASSISTANT_MESSAGE}`. |
| Swipe archive/unarchive | `messages/SwipeHelper` | `POST notifications/{id}/archive` \| `…/unarchive`. |
| Message details | `messages/MessageDetailsFragment` | `GET notifications/{id}`; marks read; action button routes via `NotificationData`. |
| In-app action routing | MessageDetailsFragment | `EActionView{view_activity,view_robot,robot,view_account,account,play_video,reboot,view_url,url,view_help,help,approve_therapist,moxie_access_for_therapist}`; `EPage{insights,activity,robot,events,settings,account,notification,reboot}`; `ESubpage{battery}`. `reboot` action → `POST robots/{id}/reboot`; therapist action → `POST teletherapy/request-access-moxie`. |
| FCM push | `firebase/MessagingService` | `onMessageReceived` → Intent to MainActivity, extra `notifications=fcm`, copies **all** data-map keys as deep-link extras (`SharedKeys.KEY_NOTIFICATION_*`: action, activity, body, notification_data, id, page, title). Channel `fcm_fallback_notification_channel` (id 112). |
| Push registration | `BaseActivity` | FCM token → `MobileDeviceAttributes{fcm-token, mobile-device-id}` → `POST mobile-devices` / `PUT mobile-devices/{id}`. On logout pushes `fcmToken="null"` to unregister. |
| Local notif service | `notification/NotificationService` | Foreground service, channel `analysis_notification`, id 111 (e.g. verification code). Not FCM. |

---

## 11. GRL (Grown-up/Guest Remote Login)

Lets a guest connect to a paired Moxie via a generated code. UI in `main/moxie/MoxieSettingsFragment`
(GRL section: `grlSwitch`, `grlCode`, `grlDescription`).
- **Create:** `POST grl/code` (`createGrl`; optional body `CreateGrlDataModel{birthday,first_name,nickname}`).
- **Revoke all:** `POST grl/revoke-all` (`revokeGrl`; also clears `UserAttributes.lastGrlCode`).
- **Per-child enable flag:** `ChildrenModel.grlConnectEnabled` → `PUT children/{id}`.
- Status enum `User.GRLCodeStatus{none,used,expired,unused}`.

---

## 12. Teletherapy / clinician (Moxie Pro)

Clinician-facing; gated by `Config.isClinicianUser()`/`isProVersion()` and
`RobotAttributes.telehealth-supported`.

| Feature | Class | Endpoint |
|---|---|---|
| Patients/therapists list | `main/account/teletherapy/TelehealthServiceFragment` + `TeletherapyViewModel` | `POST teletherapy/therapists-list` (`{user-id}`) → `TeletherapyPatientData{patient_name, settings, therapist_id/name, verified, parental_consent, created_at}`. |
| Patient status update | `TeletherapyViewModel` | `PUT teletherapy/patient-status` (`{parental-consent, patient-id, settings, verified}`; `PatientSettings{moxie_recording_enabled, zoom_recording_enabled}`). |
| Request Moxie access | `TeletherapyViewModel` / message action | `POST teletherapy/request-access-moxie` (`{appt}`). |
| Pro org info | login/account | `GET user-options` (positions, org types). |
- Consent enum `EParentalConsent{pending,approved,rejected,requested}`.
- Pro account flows: `login/RegistrationCodeFragment` (`POST login/register`),
  `OrganizationDetailsFragment`, `ReadyToGoProFragment`, `WarningMoxieConsumerAndProDialog`.

---

## 13. Assistant package — what it is

The `main/assistant/*` package is **not** an AI assistant — it is the **Resources tab** (content/help
hub) described in §8. `ResourcesFragment` is the tab root; `AssistantModel`/`AssistantViewModel` load
`help/home`; tiles fan out to activities, commands, tips, training, printables, switch-mentors.

---

## 14. Accessibility (per-child, not robot)

`main/moxie/AccessibilityFeaturesNew` (+ legacy `AccessibilityFeatures`) and
`child_info/content_preferences/AccessibilityFeaturesFragment` → `PUT children/{id}`.
- **Input speed / "pauses for input"** slider → `ChildrenModel.inputSpeed` (`input-speed`), default 0.5,
  max 1.0 (`Config.ACCESSIBILITY_INPUT_PAUSES_DEFAULT_VALUE`/`_MAX_VALUE`). Strings: "Moxie Response
  Time", "Shorter or Longer Pauses for Input".
- **Interaction preference toggles** → `ChildrenModel.volumePreference` (`volume-preference-encrypted`,
  JSON list of `Child.VolumePreference{nohud,nosoundfx,novisualfx,lessmotion,slowoutput,slowinput,unknown}`).

Also under content-preferences (`PUT children/{id}` via `updateContentPreferencesAPI`):
interests (`pos-tags`/`neg-tags`), activity prefs (`activity-preferences`), SEL weights (`sel-weights`),
learning focus (`learning-focus-topics/-text`), personality sliders (`shyness-weight`,
`structure-weight`), family members (`family`). Config catalog from `GET content-preferences` and
`GET child-family-members`. Eye/face color gated by `supports-eye-color`/`-face-color` (server flags);
rewards customization gated by `Robot.isRewardsEnabled()`.

**Scheduling (`main/moxie/scheduling/`):**
- Bedtime (`MoxieBedtimeFragment`): weekday/weekend windows → `RobotSettingsAttributes` → `PUT robots/{id}`.
- Playdates/playtime (`MoxiePlaydateFragment`/`EditPlaydateFragment`): `WakeAlarms`/`WakeEntry{days,enabled,time}`
  → `PUT robots/{id}`; gated `wake-alarms` prop; `BedtimeWarningDialog` on overlap.
- Sensitive conversations (`SensitiveConversationsFragment`): `GET children/{id}/sensitive-conversations/list`
  (`ConversationTopic{module_id, app_title, app_detail, last_done, scheduled}`, **server-driven topics**);
  `POST …/schedule` / `…/unschedule` (`{module_id}`); scheduled topic stored on child as
  `scheduled-sensitive-conversation`. Gated `schedule-sensitive` prop.
- Calendar events (`scheduling/events/`): `EEventType{holiday_religious,holiday_secular,birthday,school,
  appointment}`; birthdays (`CalendarBirthdayRelation`: sister/brother/mother/father/grandmother/
  grandfather/myself/other/friend), holidays (`CalendarHolidayType{religious,secular}`), appointments
  (`CalendarAppointmentType{doctor,dentist,therapist}`), school (`CalendarSchoolEventType{first_day,
  last_day,other}`), repetition (`CalendarRepetitionType{none,daily,weekly,two_weekly,monthly,yearly}`).
  Holiday catalog from `GET calendar-holidays`; per-child events serialized (encrypted) into
  `calendar-events-encrypted` → `PUT children/{id}`.

---

## 15. HIDDEN / DEVELOPER features

### 15.1 `envchange` — hidden build-environment switcher ⭐
`login/LoginFragment` (L192): type **`envchange`** into the email field (then trigger
`onChangeUrlClick`) → `showSelectEnvironmentDialog()` bottom sheet with 5 options →
`Config.setBuildMode(DEVELOP|STAGING|PRODUCTION|CHINA|HONG_KONG)` (pref `build_mode`, default
PRODUCTION). This retargets the entire REST base URL at runtime:
- PRODUCTION `client-service-api`, STAGING `…-staging-api`, DEVELOP `…-develop-api`,
  CHINA `…-cn-api`, HONG_KONG `…-hk-api` (`.embodied.com`). The current mode name is shown as the
  Account version-text tooltip (long-press) and as `environmentText` on Login.

### 15.2 "Use demo data for Insights and Rewards" toggle ⭐
Account screen switch `demo_data_switch` (`FragmentAccountBindingImpl`), **only visible/effective when
`!Config.isProductionMode()`** — `Config.isDemoDataForInsightsEnabled()` hard-returns false in
PRODUCTION regardless of pref `is_demo_data_enabled`. Effects when on:
- `RequestManager.auid()` returns hardcoded **`"foo"`** (so analytics run against a demo AUID).
- `BasicRewardsAdapter` sets **all reward assets to `unlocked`**.
- ViewModels carry hardcoded sample payloads (`InsightsViewModel.testJson` — weekly "Jun 15–21",
  missionCount 8, badgeCount 18, activityTime "2h18m"; `AnalyticsDetailsViewModel.setDummyData`,
  `SubDetailsViewModel.setDummyData`).

### 15.3 Hidden debug long-press ⭐
`main/moxie/MoxieFragment.setupHiddenButtonForDebugging` (L1581): **long-press the battery-percentage
view** on the Moxie home card. Returns immediately in production; otherwise performs a click on the
add/edit-profile button (dev shortcut into child editing).

### 15.4 Non-production "Skip" affordances
- **Skip pairing:** `FragmentPairQrCodeBindingImpl` shows `buttonSkipTop` only when
  `!isProductionMode()` — bypasses QR pairing entirely.
- **Skip OTA:** OTA status screens expose skip when `isInitialSetup` (`SKIP_AVAILABLE`).

### 15.5 Verbose non-prod error surfaces
In non-production, several screens show raw server messages / status codes instead of generic errors
(`MessagesItemsAdapter` appends `(code)`, `MoxieSettingsFragment` GRL failure shows raw `result`,
`BaseActivity` skips some error suppression). ProtoPairing adds a **dev=1 protobuf field (f3)** only in
non-PRODUCTION builds.

### 15.6 Staging-only behavior
`Robot.isRewardsEnabled()` **always returns true when `BuildMode==STAGING`** (bypasses the server
`rewards-support` capability flag).

### 15.7 Dead / inert code
- **JSON QR mode is unreachable:** `Config.getPairQRMode()` returns `PAIR_PROTO_KEY` on both branches,
  so `PairQRMode.PAIR_JSON_TOKEN` and the `PAIRING_QR_MODE` pref never take effect. `JSONPairing` is
  effectively dead in this build.
- **`RecoveryKey.test()`** — a self-test using hardcoded phrase `"test-recovery-key"`; only logs,
  never called in normal flow.
- `RobotInfoViewModel.initDummyData()` / `dummyWakeAlarm` — test playdate data.
- `DeviceSettingsProps.debug` — a robot-advertised `debug` capability flag (server-side gated).

### 15.8 `force_new_ui` flag
`SharedKeys.KEY_FORCE_SHOW_NEW_UI` — passed true only from `pair_moxie/MoxieConnectedFragment` (after
first pairing) to `EditChildInfoActivity`, forcing the "new" child-edit UI path. Not user-toggleable.

### 15.9 Deep links
Firebase Dynamic Links host **`embo.page.link`** (https, BROWSABLE) → `LaunchActivity`/`BaseFragment.
handleDeepLink`. Used for magic-link email verification (login code / restore email-verification,
`KEY_RESTORE_REDIRECT_URI`) and FCM notification routing (see §10).

---

## Appendix A — Full endpoint inventory (from `Config.java`)

Auth/session: `login/start`, `login/finish`, `login/register`, `oauth/token`.
User: `users` (create), `users/me` (get/update/delete), `users/me/change-email-request`,
`users/me/change-email`, `user-options`.
Children: `children`, `children/{id}`, `children/{id}/pending-info`, `children/{id}/resend-email`,
`children/{id}/rewards`, `children/{id}/sensitive-conversations/{list|schedule|unschedule}`.
Content prefs: `content-preferences`, `child-family-members`, `calendar-holidays`.
Robot: `robots/{id}` (get/update/delete), `robots/{id}?rfs=1`, `robots/{id}/wakeup`,
`robots/{id}/reboot`, `robots/{id}/ota_status`, `robots/{id}/set-language`, `robots/{id}/restores`,
`pairing-info`, `secret-key-collection`, `network-tests`.
Analytics: `analytics/pages/{id}` (id ∈ insights|cognitive|social|emotional|missions|badges),
`analytics/pages/details`, `analytics/pages/insights`, `analytics/auid-encrypted`.
Help/content: `help`, `help/{path}` (home, moxie-activities, moxie-commands, tips-for-success,
language-support), `help/pronounce`, `help/share-auid`.
Notifications: `notifications`, `notifications/{id}`, `notifications/{id}/{archive}`,
`mobile-devices`, `mobile-devices/{id}`.
GRL: `grl/code`, `grl/revoke-all`.
Teletherapy: `teletherapy/patient-status`, `teletherapy/request-access-moxie`,
`teletherapy/therapists-list`.
Non-REST browser flow: `{base}privo-verification?access_token=…`.

## Appendix B — Notable prefs (`SharedKeys` + Config)
`build_mode` (envchange), `is_demo_data_enabled`, `pairing_qr_mode` (inert), `pairing_mode`,
`ppcrk` (recovery passphrase), `WizardPageDisplayed`, `explainer_video_on_pairing`,
`user_guide_dialog_displayed`, `check_restore_from_backup`, `IsMoxiePaired`, `isChildInfoIntroduced`,
`grant_consent_page`, `force_new_ui`, `skip_available`, `last_used_email`, `app_language`,
`user_data_cache`/`insights_data_cache`/`assistant_data_cache` (encrypted response caches).
