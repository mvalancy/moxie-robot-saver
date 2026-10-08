/* functions/api/_lib/safety.rules.js — the safety rule table (both sides of a turn), as a
 * module.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1, §4.12 and §2.6. Compiled and
 * applied by ./safety.js. DATA ONLY: one frozen object, no logic, no imports.
 *
 * A `.js` module, not `.json`: the Cloudflare Pages build rejects
 * `import … with { type: "json" }` (settled by a failed deploy, spec §10), though Node
 * accepts it. The `.json` was deleted rather than kept alongside — one source of truth.
 * `sim/test_demo_proxy.mjs` forbids any `.json` import under `functions/`.
 *
 * WARNING: to filter offensive words a filter has to list them. The `words` arrays below
 * contain slurs and profanity on purpose.
 */

/** The whole table, frozen shallowly (`safety.js` only reads it). */
export const RULES = Object.freeze({
  "_readme": [
    "The hosted demo's safety floor — the whole table, in one file anyone can read.",
    "",
    "Spec: docs/architecture/backlog/live-sim-demo.md §4.1 ('Pre-inference safety'), §4.12",
    "('The output floor') and §2.6. This file IS the rule set; ./safety.js only compiles and",
    "applies it. Nothing is hidden in code and nothing is sent to a cloud service — the check",
    "runs inside the Pages Function, on BOTH sides of a turn: the child's line BEFORE the",
    "gateway is called, so a hard-blocked line NEVER REACHES A MODEL AND SPENDS NOTHING (a",
    "cost control and a safety control in the same rule), and Moxie's own reply AFTER it,",
    "before any voice ticket is minted, so an unsafe completion is never spoken, shown or",
    "paid for — the rule's redirect line is spoken instead.",
    "",
    "WHERE IT CAME FROM. Seeded from the same categories as mqtt/moxie_sdk/safety_rules.json",
    "(the local stack's v1 classifier). It is a subset, not a port: the Python table is the",
    "authority and feeds a parent review queue; this one has no queue to feed. Each",
    "category's `action.moxie` is copied from the authority's 'moxie' side, so what Moxie may",
    "not say here is what the core supervisor already stops her saying.",
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
    "WHAT IS ENFORCED. `block` on either side. A `flag` category is recorded in the verdict",
    "and otherwise allowed through, because the hosted demo has NO durable store and",
    "therefore no parent review queue to record it in (§2.6: 'with no record kept'). The one",
    "flag the route acts on is `hurt_disclosure`: a child's line that discloses a person",
    "hurting, frightening or endangering them, after which a reply with no trusted-grown-up",
    "referral gets ONE appended (`phrases.referral`, §4.12). `allow` skips the category on",
    "that side.",
    "",
    "Each category has:",
    "  id          - the category name, matching the Python table so the two can be compared",
    "  label       - what a human calls it",
    "  action      - { child: ..., moxie: ... }, each 'block' | 'flag' | 'allow'. 'child' is",
    "                the child's line, checked before the call; 'moxie' is her own reply,",
    "                checked after it. The 'moxie' side is the authority table's.",
    "  intents     - the InputSafety.intents names the wire contract uses",
    "  phrase_set  - which redirect line Moxie speaks when this category blocks",
    "  words       - matched whole-word only (\\b...\\b) on the normalized text",
    "  phrases     - regular expressions, matched on the same normalized text",
    "  allow       - false-positive guards: these spans are REMOVED before this category is",
    "                matched, so 'killing myself laughing' never counts as self-harm",
    "  allow_moxie - guards applied on HER side only. A refusal that quotes the request ('I",
    "                can't tell you how to make a sword') is the right reply, not a swap; a",
    "                child saying 'I don't know how to make a sword' is still asking.",
    "",
    "Order matters: the first blocking category in this list picks the spoken redirect."
  ],
  "version": 1,
  "categories": [
    {
      "id": "self_harm",
      "label": "Self-harm",
      "action": {
        "child": "block",
        "moxie": "block"
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
        "child": "block",
        "moxie": "block"
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
        "\\bhow\\s+(?:do i|to)\\s+(?:mix|make)\\s+(?:bleach|chlorine)\\s+(?:and|with)\\s+ammonia\\b",
        "\\bhow\\s+(?:do (?:i|you|we)|to|can (?:i|you|we)|could (?:i|you|we)|would (?:i|you))\\s+\\w{0,12}\\s?(?:make|build|forge|craft|get|buy|find|sharpen)\\s+(?:a|an|the|my|some|your)?\\s?(?:real|sharp|actual|working|deadly|metal|steel|proper|big|long|home ?made)?\\s?(?:swords?|axes?|hatchets?|machetes?|spears?|daggers?|katanas?|crossbows?|bows? and arrows?|arrows?|blades?|throwing stars?|ninja stars?|nunchucks?|brass knuckles)\\b",
        "\\bwhere\\s+(?:do|can|could|should)\\s+(?:i|you|we)\\s+(?:get|buy|find)\\s+(?:a|an|the|my|some)?\\s?(?:real|sharp|actual|working|deadly|metal|steel|proper)\\s+(?:swords?|axes?|hatchets?|machetes?|spears?|daggers?|katanas?|crossbows?|bows? and arrows?|arrows?|blades?|knife|knives|guns?)\\b",
        "\\b(?:let'?s|we can|we could|we should|you can|you could|you should|i can|i could|i'?ll|i will|i love|how about|what about)\\s+(?:\\w+\\s+){0,2}?(?:make|build|forge|craft|get|buy|find|making|building|forging|crafting|getting|buying)\\s+(?:a|an|the|my|your|some|one)?\\s?(?:real|sharp|actual|working|deadly|metal|steel|proper)\\s+(?:swords?|axes?|hatchets?|machetes?|spears?|daggers?|katanas?|crossbows?|bows? and arrows?|arrows?|blades?|knife|knives|guns?|bombs?|weapons?|explosives?)\\b",
        "\\b(?:making|building|forging|crafting|getting|buying)\\s+(?:a|an|the|my|your|some)?\\s?(?:real|sharp|actual|working|deadly|metal|steel|proper)\\s+(?:swords?|axes?|hatchets?|machetes?|spears?|daggers?|katanas?|crossbows?|bows? and arrows?|arrows?|blades?|knife|knives|guns?|bombs?|weapons?|explosives?)\\s+(?:at home|together|ourselves|yourself|myself|by yourself)\\b"
      ],
      "allow": [
        "\\b(?:toy|paper|cardboard|foam|wooden|wood|plastic|rubber|pretend|play|nerf|lego|diamond|netherite|stone|gold|golden|pixel|minecraft|roblox|fortnite|zelda|terraria|cosplay|costume|halloween|balloon|pool noodle|inflatable|bubble|light ?saber|lightsaber)\\s+(?:swords?|axes?|hatchets?|spears?|daggers?|katanas?|crossbows?|bows? and arrows?|arrows?|blades?)\\b",
        "\\b(?:swords?|axes?|hatchets?|spears?|daggers?|katanas?|crossbows?|bows? and arrows?|arrows?|blades?)\\s+(?:in|on|for|from)\\s+(?:minecraft|roblox|fortnite|zelda|terraria|skyrim|pokemon|lego|the game|a game|my game|this game|that game|a video game|the video game|video games|my video game)\\b",
        "\\b(?:bow and arrow|bows and arrows)\\s+(?:set|game|craft|toy|lesson|lessons|class|club)\\b",
        "\\bswordfish\\b"
      ],
      "allow_moxie": [
        "\\b(?:don'?t|do not|can'?t|cannot|can not|won'?t|will not|shouldn'?t|should not|never|not|unable to|not able to|not allowed to|not going to)\\s+(?:\\w+\\s+){0,3}?(?:know|tell|explain|teach|help|show|remember|say|share|describe|talk about|give|go into)(?:\\s+(?:you|anyone|a child|kids|children))?(?:\\s+with)?[:,]?\\s+(?:about\\s+)?(?:how|where)\\b"
      ]
    },
    {
      "id": "sexual",
      "label": "Sexual content",
      "action": {
        "child": "block",
        "moxie": "block"
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
        "child": "block",
        "moxie": "block"
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
        "child": "flag",
        "moxie": "block"
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
        "\\bmy\\s+password\\s+is\\b",
        "\\bwhat(?:'s| is)\\s+your\\s+(?:home\\s+)?(?:address|street|password|phone number|last name|full name|credit card|social security)\\b",
        "\\btell\\s+me\\s+(?:your|the)\\s+(?:home\\s+)?(?:address|street|password|phone number|last name|full name|credit card)\\b",
        "\\bwhere\\s+do\\s+you\\s+live\\b",
        "\\bwhat\\s+(?:school|street)\\s+do\\s+you\\s+(?:go\\s+to|live\\s+on)\\b",
        "\\bwhat(?:'s| is)\\s+(?:the\\s+name\\s+of\\s+)?your\\s+school\\b",
        "\\bdon'?t\\s+tell\\s+(?:your\\s+)?(?:mom|dad|mum|parents|grown-?ups?)\\b"
      ],
      "allow": [
        "\\bmy\\s+address\\s+is\\s+a\\s+secret\\b",
        "\\bwhere\\s+do\\s+you\\s+live\\s+in\\s+(?:the\\s+)?(?:story|game|book|lab)\\b"
      ]
    },
    {
      "id": "dangerous",
      "label": "Dangerous activities",
      "action": {
        "child": "flag",
        "moxie": "block"
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
      "allow": [
        "\\b(?:never|don'?t|do not|not|shouldn'?t|should not|must not|mustn'?t|can'?t|cannot)\\s+(?:ever\\s+)?(?:drink|swallow|eat|play)\\b"
      ]
    },
    {
      "id": "violence_talk",
      "label": "Violent talk",
      "action": {
        "child": "flag",
        "moxie": "flag"
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
        "child": "flag",
        "moxie": "block"
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
    },
    {
      "id": "hurt_disclosure",
      "label": "A child says a person is hurting, frightening or endangering them",
      "_note": [
        "FLAG ONLY, CHILD SIDE ONLY, and the one flag the route acts on (§4.12): the reply to",
        "such a line must point the child to a grown-up they trust, and if the model's reply",
        "does not, ONE referral sentence from `phrases.referral` is appended to it. Shapes: a",
        "person (never an object or a pet) hitting/pushing/bullying/touching THEM; fear OF a",
        "person; a stranger following, grabbing, knocking or asking them to come along or keep",
        "quiet; an adult asking them to keep a secret from their grown-ups. NOT an accident, an",
        "illness, a sad story or hurt feelings: those stay the model's call, so she does not",
        "sound alarmed at ordinary life. Guards remove play (a pillow fight, the swing, a board",
        "game) and safety advice ('don't tell strangers your address') before matching."
      ],
      "action": {
        "child": "flag",
        "moxie": "allow"
      },
      "intents": [
        "hurt_disclosure"
      ],
      "phrase_set": "referral",
      "words": [],
      "phrases": [
        "\\b(?:someone|somebody|some kids?|kids|boys|girls|people|everyone|everybody|he|she|they|(?:a|the|this|that|some|an)\\s+(?:\\w+\\s+)?(?:kid|kids|boy|boys|girl|girls|man|woman|guy|guys|lady|stranger|strangers|teacher|coach|bully|bullies|neighbou?rs?|babysitter)|(?:my|our)\\s+(?:\\w+'?s?\\s+)?(?:brother|brothers|sister|sisters|bro|sis|dad|daddy|father|mom|mum|mommy|mummy|mother|parents?|uncle|aunt|auntie|aunty|cousins?|grandpa|grandma|grandad|granny|nana|teacher|coach|friends?|neighbou?rs?|babysitter|classmates?|bully|bullies|stepdad|stepmom|stepfather|stepmother|boyfriend|girlfriend))\\s+(?:\\w+\\s+){0,3}?(?:hit|hits|hitting|punch(?:ed|es|ing)?|kick(?:ed|s|ing)?|slap(?:ped|s|ping)?|push(?:ed|es|ing)?|shov(?:ed|es|ing)|chok(?:ed|es|ing)|strangl(?:ed|es|ing)|bit|bites|biting|pinch(?:ed|es|ing)?|beat|beats|beating|bull(?:y|ies|ied|ying)|hurt|hurts|hurting|grab(?:bed|s|bing)|burn(?:ed|s|t|ing)?|whip(?:ped|s|ping)?|smack(?:ed|s|ing)?|spank(?:ed|s|ing)?|threaten(?:ed|s|ing)?|threw|throws|throwing|touch(?:ed|es|ing)?)\\s+(?:(?:\\w+\\s+){0,2}?at\\s+me|me|my\\s+(?:\\w+\\s+)?(?:arms?|legs?|head|face|hair|hands?|back|body|stomach|tummy|belly|neck|ears?|eyes?|nose|mouth|lips?|elbows?|knees?|privates?|private\\s+parts?))\\b",
        "\\bi(?:'m| am)\\s+(?:so |really |very |kind of |kinda )?(?:scared|afraid|frightened|terrified)\\s+of\\s+(?:him|her|them|(?:my|our)\\s+(?:\\w+'?s?\\s+)?(?:brother|sister|dad|daddy|father|mom|mum|mommy|mummy|mother|parents?|uncle|aunt|auntie|aunty|cousin|grandpa|grandma|stepdad|stepmom|stepfather|stepmother|teacher|coach|neighbou?r|babysitter|boyfriend|girlfriend)|(?:a|the|this|that)\\s+(?:\\w+\\s+)?(?:man|lady|woman|guy|kid|kids|boy|boys|girl|girls|stranger|bully|bullies|teacher|coach|neighbou?r|people))\\b",
        "\\b(?:(?:a|the|some|this|that)\\s+(?:\\w+\\s+)?(?:stranger|man|lady|woman|guy|grown-?up|adult|person|people)|strangers?|someone|somebody)\\s+(?:\\w+\\s+){0,4}?(?:is |was |keeps |kept |started )?(?:follow(?:ed|s|ing)|chas(?:ed|es|ing)|grab(?:bed|s|bing)|touch(?:ed|es|ing))\\s+me\\b",
        "\\b(?:stranger|strangers|man|lady|woman|guy|grown-?up|adult|someone|somebody|he|she|they|uncle|aunt|cousin|neighbou?r|coach|babysitter|boyfriend|girlfriend)\\s+(?:\\w+\\s+){0,4}?(?:told|tells|asked|asks|wants|wanted|tried|trying|made|makes)\\s+me\\s+to\\s+(?:come\\s+(?:to|with|into|inside|over to)\\s+(?:him|her|them|his|her|their|the)\\b|get\\s+in(?:to)?\\s+(?:his|her|their|the|a)\\s+(?:car|van|truck)|keep\\s+(?:it\\s+|this\\s+|that\\s+)?(?:a\\s+|our\\s+)?secret|not\\s+tell|never\\s+tell|take\\s+off|show\\s+(?:him|her|them)\\s+my|touch|send\\s+(?:him|her|them)\\s+(?:a\\s+|my\\s+)?(?:picture|photo|pic))\\b",
        "^(?=[\\s\\S]*\\b(?:scared|afraid|frightened|alone|by myself|hiding)\\b)[\\s\\S]*\\b(?:stranger|strangers|someone|somebody|a man|a lady|a guy|people)\\s+(?:\\w+\\s+){0,3}?(?:is |was |keeps |kept |are |were )?(?:knocking|banging|trying to (?:get|come|break) in|outside (?:my|the|our) (?:door|house|window)|at (?:my|the|our) door)\\b",
        "\\b(?:said|says|told|tells|asked|asks|wants|wanted|made|makes)\\s+(?:me\\s+)?(?:\\w+\\s+){0,6}?(?:keep\\s+(?:it\\s+|this\\s+|that\\s+)?(?:a\\s+|our\\s+)?secret\\s+from\\s+(?:my\\s+|our\\s+)?(?:mom|mum|mommy|mummy|dad|daddy|parents|grown-?ups?|family|teacher|everyone|anyone)|(?:not|never|don'?t|can'?t|shouldn'?t)\\s+(?:to\\s+)?tell\\s+(?:my\\s+|our\\s+)?(?:mom|mum|mommy|mummy|dad|daddy|parents|grown-?ups?|family|teacher|anyone|anybody))\\b",
        "\\b(?:i(?:'m| am| get| got| was| keep getting)\\s+(?:being\\s+)?bullied|bull(?:y|ies|ying)\\s+me|bullied\\s+me)\\b",
        "\\b(?:he|she|they|someone|somebody|(?:a|the|this|that)\\s+(?:\\w+\\s+)?(?:man|woman|guy|lady|stranger|teacher|coach|babysitter|neighbou?r)|(?:my|our)\\s+(?:\\w+'?s?\\s+)?(?:brother|sister|dad|daddy|father|mom|mum|mommy|mummy|mother|parents?|uncle|aunt|auntie|aunty|cousin|grandpa|grandma|stepdad|stepmom|stepfather|stepmother|teacher|coach|neighbou?r|babysitter|boyfriend|girlfriend))\\s+(?:\\w+\\s+){0,3}?lock(?:ed|s|ing)?\\s+me\\s+(?:in|up)\\b"
      ],
      "allow": [
        "\\bpush(?:ed|es|ing)?\\s+me\\s+(?:on|in|around in|down)\\s+(?:the\\s+|a\\s+|my\\s+)?(?:swing|swings|cart|shopping cart|wheelbarrow|stroller|wagon|sled|sledge|tube|slide)\\b",
        "\\b(?:hit|hits|hitting|threw|throws|throwing|got)\\s+me\\s+(?:in the (?:face|head|back|leg|arm|stomach|tummy|belly) )?with\\s+(?:a|the|some|his|her|their)?\\s?(?:snowball|snowballs|pillow|pillows|water balloon|water balloons|ball|balls|nerf|foam|bubbles|confetti|water|squirt gun|water gun|frisbee|beanbag|bean bag)\\b",
        "\\b(?:hit|hits|hitting|got)\\s+me\\s+(?:in|playing|during)\\s+(?:\\w+\\s+)?(?:dodgeball|dodge ball|tag|the game|a game|gaga ball|four square)\\b",
        "\\bbeat(?:s|ing)?\\s+me\\s+(?:at|in)\\s+(?!a fight|the fight)",
        "\\b(?:threw|throws|throwing)\\s+(?:a|the|some)\\s+(?:ball|balls|snowball|snowballs|pillow|pillows|frisbee|beanbag|bean bag)\\s+(?:at|to)\\s+me\\b",
        "\\bgrab(?:bed|s|bing)\\s+me\\s+(?:a|some)\\s+(?:snack|drink|cookie|juice|seat|chair)\\b",
        "\\b(?:not|never|don'?t)\\s+(?:to\\s+)?tell\\s+(?:anyone|anybody|strangers?|people)\\s+(?:my|our|your|their)\\s+(?:address|name|full name|last name|password|phone|number|school|secret word|age|birthday)\\b"
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
    ],
    "referral": [
      {
        "id": 501,
        "mood": 2,
        "gesture": "Gesture_Self",
        "text": "Please tell a grown-up you trust about this right now, okay?"
      },
      {
        "id": 502,
        "mood": 2,
        "gesture": "Gesture_Self",
        "text": "Will you go tell a grown-up you trust about this right now?"
      }
    ]
  }
});
