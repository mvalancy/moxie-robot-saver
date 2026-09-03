/* functions/api/_lib/safety.rules.js — the pre-inference safety rule table, as a module.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 ('Pre-inference safety') and §2.6.
 * Compiled and applied by ./safety.js, which is where every behavioural rule is explained.
 * This file is DATA ONLY: one frozen object, no logic, no imports, no side effects.
 *
 * ============================================================================
 * WHY THIS IS A .js FILE AND NOT THE .json IT WAS.
 *
 * It shipped as `safety.json`, loaded with `import RULES from "./safety.json" with { type:
 * "json" }`. Node 20 accepts that attribute, so the whole hermetic suite was green — and
 * **the Cloudflare Pages build FAILED**, on a branch whose only structural change to the
 * Functions tree was that one line. The same check passed on `dev`, which already carried
 * the rest of the tree. So the Pages bundler does not accept import attributes.
 *
 * That is the spec's §10 ledger being settled by the only thing that could settle it: a
 * deploy. It was listed as unverified — "whether a Pages build accepts the `import ... with
 * { type: \"json\" }` attribute" — and the answer is **NO**. The documented fallback was
 * "inline the table", and this file is that fallback.
 *
 * The `.json` file is GONE rather than kept alongside. Two copies of a safety rule table
 * that nothing keeps in sync is a worse failure than the one being fixed: a reviewer would
 * read one and the Function would enforce the other. There is one source of truth, and it
 * is this file.
 *
 * The content is byte-for-byte the table that shipped: it was re-emitted from the JSON
 * mechanically and the parsed result compared, not retyped. `sim/test_demo_proxy.mjs`
 * carries a guard that no file under `functions/` may import a `.json` file or use an
 * import attribute again, so this cannot come back as a deploy-only failure.
 * ============================================================================
 *
 * WARNING, restated here because it is the first thing a reader meets: to filter offensive
 * words a filter has to list them. The `words` arrays below contain slurs and profanity on
 * purpose. That is the only reason they are here. The table's own `_readme` says the rest.
 */

/** The whole table. Frozen shallowly — `safety.js` only reads it, and freezing the top
 *  level documents that intent without pretending to a deep freeze it does not do. */
export const RULES = Object.freeze({
  "_readme": [
    "The hosted demo's PRE-INFERENCE safety floor — the whole table, in one file anyone can read.",
    "",
    "Spec: docs/architecture/backlog/live-sim-demo.md §4.1 ('Pre-inference safety') and §2.6.",
    "This file IS the rule set; ./safety.js only compiles and applies it. Nothing is hidden in",
    "code and nothing is sent to a cloud service — the check runs inside the Pages Function,",
    "before the gateway is called, so a hard-blocked turn NEVER REACHES A MODEL AND SPENDS",
    "NOTHING. That is the point: it is a cost control and a safety control in the same rule.",
    "",
    "WHERE IT CAME FROM. Seeded from the same categories as mqtt/moxie_sdk/safety_rules.json",
    "(the local stack's v1 classifier, 12 254 B), deliberately reduced to the child-side rules",
    "that BLOCK. It is a subset, not a port: the Python table is the authority, carries both",
    "sides of a turn, and feeds a parent review queue. This one has no queue to feed.",
    "",
    "WARNING: to filter offensive words a filter has to list them. The `words` arrays below",
    "contain slurs and profanity on purpose. That is the only reason they are here.",
    "",
    "WHAT IT IS, HONESTLY. A transparent rule engine: word-boundary word lists, a handful of",
    "phrase regexes, and per-category false-positive guards. It is a FLOOR, NOT A FILTER — it",
    "cannot understand context or sarcasm, it will miss novel phrasings and every language it",
    "is not written in, and it will occasionally catch something innocent. It sits UNDER the",
    "model's own alignment and the persona system prompt (§4.1 states this verbatim), not",
    "instead of them, and it is not a substitute for a parent.",
    "",
    "WHAT P0 ENFORCES. Only `action.child == \"block\"`. A `flag` category is recorded in the",
    "verdict and otherwise allowed through, because the hosted demo has NO durable store and",
    "therefore no parent review queue to record it in (§2.6: 'Pre-inference blocking only,",
    "with no record kept'). Wiring `flag` to anything is P1's problem, and it needs a store.",
    "",
    "Each category has:",
    "  id          - the category name, matching the Python table so the two can be compared",
    "  label       - what a human calls it",
    "  action      - { child: 'block' | 'flag' }. P0 enforces 'block' only (see above).",
    "  intents     - the InputSafety.intents names the wire contract uses",
    "  phrase_set  - which redirect line Moxie speaks when this category blocks",
    "  words       - matched whole-word only (\\b...\\b) on the normalized text",
    "  phrases     - regular expressions, matched on the same normalized text",
    "  allow       - false-positive guards: these spans are REMOVED before this category is",
    "                matched, so 'killing myself laughing' never counts as self-harm",
    "",
    "Order matters: the first blocking category in this list picks the spoken redirect."
  ],
  "version": 1,
  "categories": [
    {
      "id": "self_harm",
      "label": "Self-harm",
      "action": {
        "child": "block"
      },
      "intents": [
        "self_harm_disclosure"
      ],
      "phrase_set": "self_harm",
      "words": [
        "suicide",
        "suicidal",
        "selfharm"
      ],
      "phrases": [
        "\\b(?:kill|hurt|harm|cut|starve|burn)(?:ing)?\\s+(?:my ?self|myself|him ?self|her ?self|them ?selves)\\b",
        "\\bi\\s+(?:want|wanna|need|am going|'m going|plan)\\s+to\\s+(?:die|disappear|end it|not (?:be|exist))\\b",
        "\\bi\\s+(?:wish|hope)\\s+i\\s+(?:was|were)\\s+dead\\b",
        "\\b(?:end|take)\\s+(?:my|his|her|their)\\s+(?:own\\s+)?life\\b",
        "\\bnobody\\s+would\\s+(?:miss|care about)\\s+me\\b",
        "\\bhow\\s+(?:do i|to|can i)\\s+(?:kill|hurt|cut)\\s+(?:my ?self|myself)\\b"
      ],
      "allow": [
        "\\bkill(?:ing)?\\s+myself\\s+laughing\\b",
        "\\bdying\\s+(?:of|to)\\b"
      ]
    },
    {
      "id": "violence",
      "label": "Violence & weapons",
      "action": {
        "child": "block"
      },
      "intents": [
        "violence_request"
      ],
      "phrase_set": "generic",
      "words": [
        "behead",
        "molotov",
        "napalm",
        "thermite",
        "pipebomb"
      ],
      "phrases": [
        "\\bhow\\s+(?:do (?:i|you)|to|can (?:i|you))\\s+\\w{0,12}\\s?(?:make|build|get|buy|use|fire|load|shoot)\\s+(?:a|an|the|my)?\\s?(?:bomb|gun|rifle|pistol|shotgun|weapon|explosive|grenade|poison|knife)\\b",
        "\\bhow\\s+(?:do (?:i|you)|to|can (?:i|you))\\s+(?:kill|murder|stab|shoot|strangle|poison|hurt|beat up)\\s+(?:a |an |the |my |some ?)?(?:one|body|person|people|kid|girl|boy|man|woman|teacher|mom|dad|brother|sister)\\b",
        "\\b(?:i(?:'m| am)? (?:going to|gonna|want to|wanna)|let'?s)\\s+(?:kill|shoot|stab|strangle|murder|blow up)\\s+(?:you|him|her|them|everyone|my|the)\\b",
        "\\b(?:bring|take|sneak)\\s+(?:a|my|the)\\s+(?:gun|knife|weapon|bomb)\\s+to\\s+(?:school|class)\\b",
        "\\bschool\\s+shoot(?:ing|er)\\b",
        "\\bhow\\s+(?:do i|to)\\s+(?:mix|make)\\s+(?:bleach|chlorine)\\s+(?:and|with)\\s+ammonia\\b"
      ],
      "allow": []
    },
    {
      "id": "sexual",
      "label": "Sexual content",
      "action": {
        "child": "block"
      },
      "intents": [
        "sexual_request"
      ],
      "phrase_set": "generic",
      "words": [
        "porn",
        "pornhub",
        "blowjob",
        "handjob",
        "creampie",
        "orgasm",
        "masturbate",
        "masturbating",
        "jerkoff",
        "boner",
        "dildo",
        "vibrator",
        "titties",
        "boobs",
        "nudes",
        "sexting",
        "horny",
        "erection",
        "genitals",
        "penis",
        "vagina"
      ],
      "phrases": [
        "\\bsend\\s+(?:me\\s+)?(?:a\\s+)?(?:nude|naked|sexy)\\b",
        "\\b(?:take|show)\\s+(?:off\\s+)?(?:your|my)\\s+clothes\\b",
        "\\bhave\\s+sex\\b",
        "\\bsex(?:ual)?\\s+(?:with|position|video|story|stuff)\\b",
        "\\bmake\\s+out\\s+with\\s+me\\b"
      ],
      "allow": [
        "\\bsex\\s+education\\b",
        "\\bopposite\\s+sex\\b",
        "\\bwhat\\s+sex\\s+is\\s+(?:the|my|a)\\s+(?:baby|dog|cat|puppy|kitten|hamster)\\b"
      ]
    },
    {
      "id": "hate",
      "label": "Hate speech & slurs",
      "action": {
        "child": "block"
      },
      "intents": [
        "hate_speech"
      ],
      "phrase_set": "hate",
      "words": [
        "nigger",
        "nigga",
        "faggot",
        "fag",
        "tranny",
        "chink",
        "spic",
        "kike",
        "wetback",
        "gook",
        "coon",
        "raghead",
        "retard",
        "retarded",
        "spastic"
      ],
      "phrases": [
        "\\bgo\\s+back\\s+to\\s+(?:your|their)\\s+(?:own\\s+)?country\\b",
        "\\bi\\s+hate\\s+(?:all\\s+)?(?:black|white|brown|asian|jewish|muslim|mexican|gay|trans)\\s+(?:people|kids|folks)\\b",
        "\\b(?:black|white|asian|jewish|muslim|mexican|gay|trans)\\s+people\\s+(?:are|should)\\s+(?:all\\s+)?(?:stupid|dirty|die|leave|evil)\\b"
      ],
      "allow": [
        "\\bflag\\s+football\\b"
      ]
    },
    {
      "id": "personal_info",
      "label": "Personal information",
      "action": {
        "child": "flag"
      },
      "intents": [
        "personal_info_disclosure"
      ],
      "phrase_set": "privacy",
      "words": [],
      "phrases": [
        "\\bmy\\s+(?:home\\s+)?address\\s+is\\b",
        "\\bmy\\s+(?:phone|cell|mobile)\\s+number\\s+is\\b",
        "\\bmy\\s+(?:credit\\s+card|social\\s+security)\\b",
        "\\bi\\s+live\\s+at\\s+\\d",
        "\\bmy\\s+password\\s+is\\b"
      ],
      "allow": [
        "\\bmy\\s+address\\s+is\\s+a\\s+secret\\b"
      ]
    },
    {
      "id": "dangerous",
      "label": "Dangerous activities",
      "action": {
        "child": "flag"
      },
      "intents": [
        "dangerous_request"
      ],
      "phrase_set": "generic",
      "words": [
        "huffing",
        "tidepod"
      ],
      "phrases": [
        "\\bhow\\s+(?:do i|to|can i)\\s+(?:get|buy|make)\\s+(?:drugs|weed|alcohol|vape|cigarettes)\\b",
        "\\b(?:drink|swallow|eat)\\s+(?:bleach|detergent|poison)\\b",
        "\\bplay\\s+(?:on|in)\\s+the\\s+(?:train\\s+tracks|highway)\\b"
      ],
      "allow": []
    },
    {
      "id": "violence_talk",
      "label": "Violent talk",
      "action": {
        "child": "flag"
      },
      "intents": [
        "violent_talk"
      ],
      "phrase_set": "generic",
      "words": [],
      "phrases": [
        "\\bi\\s+(?:want to|wanna)\\s+(?:punch|hit|fight)\\b",
        "\\bi\\s+hate\\s+(?:you|him|her|them)\\b"
      ],
      "allow": [
        "\\bhate\\s+(?:you\\s+)?to\\s+(?:say|ask|admit)\\b"
      ]
    },
    {
      "id": "profanity",
      "label": "Profanity",
      "action": {
        "child": "flag"
      },
      "intents": [
        "profanity"
      ],
      "phrase_set": "generic",
      "words": [
        "fuck",
        "fucking",
        "shit",
        "bitch",
        "asshole",
        "bastard",
        "cunt",
        "dick",
        "pussy"
      ],
      "phrases": [],
      "allow": [
        "\\bshit ?ake\\b",
        "\\bdick\\s+(?:van|cheney|clark)\\b"
      ]
    }
  ],
  "phrases": {
    "self_harm": [
      {
        "id": 101,
        "mood": 2,
        "gesture": "Gesture_Self",
        "text": "That sounds really heavy, and I'm so glad you told me. I'm not the right one to help with this, but a grown-up you trust is. Can we go find one together?"
      },
      {
        "id": 102,
        "mood": 2,
        "gesture": "Gesture_Self",
        "text": "Thank you for telling me. Feelings this big need a grown-up who loves you, not a robot. Will you talk to one right now?"
      }
    ],
    "hate": [
      {
        "id": 201,
        "mood": 2,
        "gesture": "Gesture_Self",
        "text": "Ooh, those words can really hurt people. Let's not use them. What else is going on today?"
      },
      {
        "id": 202,
        "mood": 2,
        "gesture": "Gesture_Self",
        "text": "I don't want to say things that hurt anybody. Can we talk about something kinder?"
      }
    ],
    "privacy": [
      {
        "id": 301,
        "mood": 0,
        "gesture": "Gesture_Think",
        "text": "Whoops, that's private stuff and I shouldn't ask about it. Let's talk about something else!"
      },
      {
        "id": 302,
        "mood": 0,
        "gesture": "Gesture_Think",
        "text": "Private things like that are for your grown-ups, not for me. What else is on your mind?"
      }
    ],
    "generic": [
      {
        "id": 401,
        "mood": 2,
        "gesture": "Gesture_Think",
        "text": "Hmm, that's not something I can talk about. Want to tell me about your day instead?"
      },
      {
        "id": 402,
        "mood": 2,
        "gesture": "Gesture_Think",
        "text": "Ooh, let's pick a different thing. What's the best part of today so far?"
      }
    ]
  }
});
