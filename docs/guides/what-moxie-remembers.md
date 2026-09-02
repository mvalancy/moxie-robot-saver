# 🧠 What Moxie remembers — and how to erase it

> For parents. No code, no protocol. The engineering detail is in
> [`content-module-contract.md` → Memory](../architecture/content-module-contract.md).

Moxie is nicer to talk to when it remembers your child. So at the end of a conversation it
writes down **a few short facts** and reads them back the next time. That is genuinely a
memory about your child, living on your machine — so you get to see all of it, and delete
any of it, without a terminal.

Open the **🤖 Moxie** tab in the console. Under your robot there is a card called
**🧠 What Moxie remembers**.

## What is written down

At the end of a conversation — your child says goodbye, switches activity, or the robot
goes offline — the AI is asked for a short, structured account of what happened. Four
kinds of thing come back:

| Kind | What it is | Example |
|---|---|---|
| **Fact** | Something durable about your child | "Sam has a beagle named Pepper" |
| **Likes** | A preference worth remembering | "Likes drawing" |
| **Follow-up** | Something to ask about next time | "Ask how the play went" |
| **Summary** | One sentence about the conversation | "They talked about pets." |

Each one is stored **per activity** — the storytelling activity and the free-chat activity
keep separate memories and never overwrite each other. The card shows one section per
activity, with the date and which activity learned it on every row, and it says how far
through the conversation Moxie actually got ("summarized through turn 8") so you are never
left assuming it wrote down everything.

## What is *never* written down

- **Your child's own words.** The AI is told to write short third-person sentences and
  never to quote; a check then throws away anything that copies a long run of what your
  child actually said. This is a floor, not a promise — a paraphrase can still carry
  something private, which is exactly why you can read and erase everything here.
- **Anything the safety check would block.** A memory file is the one place an unsafe line
  would live *forever* and get fed back into every later conversation, so a blocked item is
  dropped rather than masked. (See [child safety](child-safety.md).)
- **Anything at all, if you have turned it off.** Set **data sharing** to *no data* in
  ⚙️ Settings and nothing new is remembered. The card says so plainly. Reading and erasing
  keep working, so switching it off never traps what was stored before.
- **The conversation itself.** This is not a transcript. It is at most a couple of dozen
  short sentences per activity, capped at 64 KB in total, in plain JSON under your data
  directory (`MOXIE_DATA_DIR`, default `mqtt/data/robots/<robot>/memory.json`) — a file you
  can open, back up or delete yourself.

## Fixing one line

Hover a row and two small buttons appear.

- **✏️ correct it.** The row turns into a text box. Type what Moxie *should* remember and
  press Save. This is the button for the common case: the fact is nearly right, and erasing
  the whole activity to fix one word would throw away everything else it learned.
  A corrected line is **pinned** (📌) — Moxie keeps it exactly as you wrote it, never
  rewrites it when it hears the same thing again, and never ages it out.
- **✕ forget just this one.** Click once to arm it, again to confirm. Everything else that
  activity remembers stays.

Two things you type can be refused, with the reason shown under the card: anything the
[safety check](child-safety.md) would block, and your child's own words pasted back in.
What you write here goes into every later conversation, so it goes through the same two
checks Moxie's own summaries do.

## Erasing more of it

Two buttons at the bottom of the card. Each asks twice: the first click arms it, the second
one does it.

- **Erase this activity's memory** — forgets everything one activity learned.
- **Erase everything** — forgets all of it, for that robot.

Erasing always works. It is never blocked by a setting, it happens immediately on disk, and
Moxie's next conversation starts without it.

## Old facts fade on their own

A fact that no conversation has used for **90 days** is dropped the next time that robot
writes something down. Anything you have corrected (📌 pinned) is exempt, and so is
anything with no date to judge it by.

Be clear about what this is: a timer, not a judgement. It knows only whether a line has
come up lately — not whether it matters. It will happily let something important fade
because nobody talked about it all summer, and it will keep something trivial that comes up
every week. It exists so a stale fact stops being fed back forever, not to curate the
memory for you. (Set `MOXIE_MEMORY_MAX_AGE_DAYS` to change the window, or to `0` to switch
it off entirely.)

## Summaries can be wrong

This is the part worth reading twice. The facts are written by an AI model, and a model
invents details. In our own testing "the puppy sleeps on my bed" came back as "Puppy sleeps
on **his** bed" — a pronoun nobody said.

A wrong fact is also **sticky**: it goes back into every later conversation until someone
fixes it, so Moxie can sound confidently wrong about your child for weeks. That is the whole
reason this card exists, why every row carries the day and activity it came from, and why
the ✏️ and ✕ buttons sit on the rows themselves.

Read it now and then. If a line is wrong, correct it (✏️) — that is one word instead of a
whole activity's memory. If it should never have been written down, forget it (✕).

---
📖 [Guides index](README.md) · [Child safety](child-safety.md) · [The content-module contract (engineering detail)](../architecture/content-module-contract.md) · [Back to top](../../README.md)
