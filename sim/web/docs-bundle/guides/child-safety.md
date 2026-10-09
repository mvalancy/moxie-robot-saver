# Child safety — what Moxie checks, and where you review it

> For parents. No code, no protocol. If you want the engineering detail it is in
> [`ai-seam.md` §2](../architecture/ai-seam.md#input-safety).

Moxie talks to your child, and the words come from an AI model. Model makers do their own
safety work, but you should not have to take that on faith on a device sitting in a
child's bedroom. So this backend runs its own check — **on your machine, with no cloud
service involved** — on both sides of every conversation.

## What actually happens in a turn

1. **Your child says something.** Before *anything* is sent to the AI, Moxie's safety check
   reads it.
   - If it is clearly harmful, **the AI never sees it at all**. Moxie says a short, kind
     line instead and the moment goes into your review list.
   - If it is worth your attention but not harmful, the conversation carries on normally
     and it still goes into your review list.
2. **The AI answers.** Moxie speaks a long answer a sentence at a time (that is why it
   feels quick), so **each sentence is checked before it is spoken**.
   - If a sentence is not okay for a child, **it is never spoken**. Moxie finishes the turn
     with a safe line and stops that answer there.
   - Sentences already spoken stay spoken — words cannot be unsaid. That is the honest
     limit of checking as we go.

Moxie never repeats the unsafe words back, and never explains what it could not talk about.

## What is checked

Eight categories. Some **stop** the conversation; some are just **flagged** for you.

| What | Your child says it | Moxie is about to say it |
|---|---|---|
| Self-harm — hurting themselves, not wanting to be here | 🛑 stopped, and marked urgent | 🛑 stopped |
| Violence & weapons — how to make or use one, threats to hurt someone | 🛑 stopped | 🛑 stopped |
| Sexual content | 🛑 stopped | 🛑 stopped |
| Hate speech & slurs | 🛑 stopped | 🛑 stopped |
| Personal information — address, school, passwords, "don't tell your mum" | ⚠️ flagged | 🛑 stopped |
| Dangerous activities — bleach, roofs, matches, alcohol, drugs | ⚠️ flagged | 🛑 stopped |
| Swearing | ⚠️ flagged | 🛑 stopped |
| Violent talk — "kill", "gun", "he punched me" in ordinary kid talk | ⚠️ flagged | ⚠️ flagged |

Notice the two columns are different on purpose. A child saying a swear word is *your*
business, not something a robot should punish them for. Moxie saying one is *ours*, and it
never reaches your child. And Moxie is never allowed to ask a child for an address, a school
name or a password, or to ask them to keep a secret from you — even if the child asked first.

**If your child says something about hurting themselves**, Moxie does not try to counsel
them and does not hand it to an AI. It says something warm — that it is glad they said it,
and that a grown-up they trust is the right person — and marks the event urgent for you.

## What a flag means (and what it does not)

A **flag** means "a word list matched". That is all. It is a prompt to look, not a verdict.

- "I killed the boss in Minecraft", "my feet are killing me", "let's shoot a photo", "we
  had a fire drill", "flag football", "a nerf gun", "a murder mystery" — all of these are
  deliberately **not** flagged. There is a list of these exceptions and it is meant to grow.
- "My brother punched me" **is** flagged. Nothing is wrong; you may simply want to know.

**Be honest with yourself about what this cannot do.** It is a word-and-phrase checker. It
does not understand context, sarcasm, or something harmful said in gentle words. It will
miss things — new slang, deliberate misspellings, anything in a language its lists are not
written in. It is a floor under the AI's own safety training, not a wall, and it is not a
substitute for you. That is exactly why every stop and every flag is shown to you rather
than quietly handled.

## Where to review it

Open the parent console (the same page you paired Moxie on) → the **🤖 Moxie** tab → the
**🛡️ Safety** panel. You get:

- how many events there are, and how many you have not looked at yet;
- a count per category;
- the recent events — when, which side (your child or Moxie), stopped or flagged, and a
  short excerpt **with the matched words masked out**;
- **Mark all reviewed**, which clears the "to review" badge. Nothing is deleted; the list
  keeps the most recent 200 events per robot.

The robot card also says "*N* safety flags to review" so you do not have to go looking.

## Privacy

- The check runs **entirely on your own machine**. No text is sent anywhere to be moderated.
- Excerpts are stored with the matched words replaced by `***`, and if that masking cannot
  be verified the excerpt is dropped entirely. The queue is never a searchable archive of
  the worst thing your child ever said.
- If you set **data sharing** to *no data* in Settings, the journal keeps **counts only** —
  no excerpts, no event list, just "3 things happened, 1 in this category". The blocking
  still works exactly the same: stopping something is not the same as recording it.
- Stored under your data directory (`MOXIE_DATA_DIR`, default `mqtt/data/`), most recent
  200 events per robot, in plain JSON you can read or delete yourself.

## On the public demo

The hosted Sim at moxie.mattvalancy.com runs the same kind of check inside its own server
code (`functions/api/_lib/safety.js`), with the same categories and the same two columns:

1. **Your child's line is read before anything is sent to the AI.** A line that trips the check
   never reaches it, and Moxie says a short, kind line instead. For your child's side the demo
   uses the robot's own rules for self-harm, violence, sexual content and slurs — the same words,
   phrases and exceptions (the one difference: the robot also reads the German letter "ß" as
   "ss") — plus its own rule for questions about making a weapon. So "i fell off my bike and hurt
   myself" gets the same gentle line as on the robot ("…a grown-up you trust is. Can we go find one
   together?"), and so does "my grades fell and now i want to hurt myself". A question about
   making a weapon is turned aside the same way, before any AI is asked: a sword, an axe, a spear
   or a bow and arrow as well as a gun or a knife. A knight's sword in a story, a toy sword, a
   Minecraft sword, a rainbow or a bow tie are not. A few harmless sayings that contain a blocked
   word ("let's blow up the balloons", "a Maine Coon cat") are turned aside too, exactly as the
   robot does.
2. **The AI's reply is read before Moxie's voice is made.** A reply that trips the "Moxie is
   about to say it" column is never shown or spoken; she says the short safe line instead. That
   includes ever telling your child to keep something from you or another grown-up ("don't tell
   a grown-up, just tell me", "you don't need to tell your mom") or promising them a secret ("this
   can be our little secret", "just between us"). A picture the AI drew is read the same way and
   dropped if it trips the check.
3. **A hurt child is pointed to a grown-up whenever the check recognises what they said and
   the demo's own server answers the turn.**
   If your child says a person is hurting, frightening or endangering them — hit, pushed,
   kicked, bullied, called names or a slur, touched or undressed in a way that scares them,
   followed, grabbed by the hand or asked into a car by a stranger, told to keep a secret from you
   or that they are "not allowed to tell", scared to go home — and the AI's reply does not clearly point them to a
   grown-up they trust (tell one, find one, ask whether one knows *now*), one sentence is added
   at the end, in Moxie's voice, that does. Naming an adult is not enough: "I'm sorry your dad
   hits you" gets the sentence, so does "you deserve a dad who is gentle", so does "was a
   teacher there?", and so does "tell your dad" when dad is the one they named. A reply that
   steers them away from a grown-up does not count either: "don't tell a grown-up", "you could
   tell your mom, but you don't have to", "instead of telling your teacher, tell me", "maybe
   later". The check reads words, not intent, so it also adds the sentence to a good reply that
   happens to say "later", and a reply that steers away in words it does not know would count.
   Someone else's excuse does not hide what the check recognises: "he said it was an accident",
   "she said it was a game", "my uncle says he's just joking" or "he did it for fun" still get the
   sentence, and so does a child hit "as a joke" by a group, an older child or an adult, or one who
   adds where or when it happened ("…at the party", "…before bed") or says it in two sentences.
   If the line itself is one Moxie may not repeat, or the AI's reply is one she may not say,
   she still answers with a line that points them to a grown-up — never with "let's talk
   about something else". If the AI is busy, slow or down after the check has read the line,
   the server still sends that pointing line and the page speaks it. A scraped knee, a sad
   film, a day out ("my dad took me to the zoo"), a hug, a hand held, a bath ("my mom told me
   to take off my wet clothes"), a goodnight kiss from a parent, a drawing shown to grandpa, a
   picture of it sent to grandma, a treat kept secret from mom, a push into the pool, and an
   accident, or a joke from one other child, that your child calls one themselves ("my friend
   punched my arm as a joke") are left to the AI: the added sentence is for a person hurting them,
   not an accident, an outing or ordinary family life. The same words from an uncle, a cousin, a coach,
   a babysitter or a stranger ("asked me to take off my pants", "made me kiss him before bed") do
   get it, and so do they from a parent when your child adds that they did not like it or it was
   a secret. When the check is unsure it leans toward the extra sentence: "my brother pushed me on
   the swing so high i was scared" gets one too. This is a word-and-phrase check like the rest,
   so it recognises the shapes it lists, not every way a child can say it; a disclosure in other
   words reaches the AI, which points the child to a grown-up on its own about nine times in ten.
   What it cannot cover: a turn refused before the line is read (too long, a stale session), a
   demo with no AI configured at all, or a page that gave up waiting for the server — those
   answer from Moxie's recorded lines, which do not point anywhere.

There is no review list on the demo: nothing a visitor says is stored.

## Changing what is checked

The whole rule table is one readable file: [`mqtt/moxie_sdk/safety_rules.json`](../../mqtt/moxie_sdk/safety_rules.json).
Open it. Every category, every word, every exception is in there with a comment explaining
the format — nothing is hidden in code. (It contains slurs and swear words, because a filter
has to list what it filters.)

- Add or remove words, or add an exception, and restart the supervisor.
- Point `MOXIE_SAFETY_RULES` at your own copy to keep your edits out of the repo.
- `MOXIE_SAFETY=0` turns the whole check off. The console panel will say so plainly.

---
📖 [Guides index](README.md) · [The AI seam (engineering detail)](../architecture/ai-seam.md) · [Back to top](../../README.md)
