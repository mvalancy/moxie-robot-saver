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
activity, with the date and which activity learned it on every row.

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
  short sentences per activity, capped at 16 KB in total, in plain JSON under your data
  directory (`MOXIE_DATA_DIR`, default `mqtt/data/robots/<robot>/memory.json`) — a file you
  can open, back up or delete yourself.

## Erasing it

Two buttons, both in the memory card. Each asks twice: the first click arms it, the second
one does it.

- **Erase this activity's memory** — forgets everything one activity learned.
- **Erase everything** — forgets all of it, for that robot.

Erasing always works. It is never blocked by a setting, it happens immediately on disk, and
Moxie's next conversation starts without it.

**There is no "delete just this one fact" yet.** The finest cut today is one activity. If a
single line is wrong, erase that activity and Moxie relearns the rest over the next few
conversations. (Tracked as a known gap in the
[implementation plan](../architecture/implementation-plan.md).)

## Summaries can be wrong

This is the part worth reading twice. The facts are written by an AI model, and a model
invents details. In our own testing "the puppy sleeps on my bed" came back as "Puppy sleeps
on **his** bed" — a pronoun nobody said.

A wrong fact is also **sticky**: it goes back into every later conversation until someone
erases it, so Moxie can sound confidently wrong about your child for weeks. That is the
whole reason this card exists, why every row carries the day and activity it came from, and
why the erase buttons are one click away from the facts themselves.

Read it now and then. If something looks off, erase that activity.

---
📖 [Guides index](README.md) · [Child safety](child-safety.md) · [The content-module contract (engineering detail)](../architecture/content-module-contract.md) · [Back to top](../../README.md)
