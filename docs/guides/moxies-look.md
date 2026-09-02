# 🎨 Moxie's look — letting your child style the face

> For parents. No code, no protocol. The engineering detail is in
> [`config-and-telemetry-contract.md` → Appearance](../architecture/config-and-telemetry-contract.md#-appearance-the-childs-chosen-face).

Moxie's face is not a picture. It is **layers** — a base head colour, eyes, brows, a mouth,
and optional extras like hair or glasses — stacked and drawn fresh every frame. Which
layers get used is part of your child's profile, which means it is something you can set
from your own server, and something your child can change whenever they like.

Open the **🤖 Moxie** tab in the console. Under your robot there is a card called
**🎨 Moxie's look**.

## What you can change today

Two layers, both colours, both with a preview swatch you can tap:

| Layer | Choices |
|---|---|
| **Eye colour** | green · blue · purple · brown · gold · teal |
| **Face colour** | blue · yellow · green · teal · pink · purple |

Pick one, press **Save look**, and it goes down to the robot on the spot — no reboot, no
re-pairing. **Reset to default** puts everything back.

## Why the other twelve layers are greyed out

The card lists all fourteen layers, because that is genuinely how Moxie's face is built —
eyes, eye design, eyelids, brows, mouth, nose, moustache, face colour, face design, hair,
glasses, stickers, extras, misc. But only the two colour layers above have choices you can
click.

That is honesty, not laziness. This project was rebuilt by studying the robot from the
outside, and the two colour lists are the only ones we can point at a document for. The
artwork for the other twelve ships inside a downloadable pack that we have never had a copy
of, so **we do not know the names of those pieces** — and we would rather show you an empty
shelf than make names up and have Moxie draw nothing.

If you *do* know the exact names your robot uses (from a robot you can already run, or from
another revival project), open **Advanced: layer names** and type them in, one per line.
They are sent through exactly as you typed them. Add them **one at a time**: some layer
names are known to crash Moxie's display, and one at a time is how you find out which.

## One robot, or all of them

If you have more than one Moxie on this server, tick **Apply to all robots (house rules)**
before saving and the look becomes the default for every one of them. A robot that has its
own look keeps it — the per-robot choice always wins over the house one, layer by layer, so
"all our robots are teal-eyed, except Sam's has a pink face" works exactly as it reads.

Pressing **Reset to default** on a single robot means *this robot wears no styling* — it
does not fall back to the house look. To go back to the house look, save the same choices
the house has, or clear the house look too.

## What actually happens when you press Save

Moxie composites those layers into one image and then, sensibly, keeps it rather than
redrawing it from scratch every time it wakes up. So a change of clothes is useless if the
robot keeps serving the old picture.

The server handles this for you: every look gets its own **texture key**, worked out from
exactly which layers you chose. Change any layer and the key changes, the robot sees a face
it has never composited before, and it draws the new one. Save the *same* look twice and the
key does not move, so nothing is disturbed for nothing. The key is shown under the card (the
first few characters) if you want to watch it change.

## An honest caveat

**No physical Moxie has ever rendered any of this.** This project has no robot to test
against. The plumbing — the picker, the layering, the texture key, the config push — is
tested end to end against a simulated robot and is correct as far as our documents go. But
two details are informed guesses, flagged as such in the code and the contract: exactly how
a layer name is spelled on the wire, and the fact that the texture cache is keyed the way we
key it. Both are one-line changes if a real robot ever tells us different.

If you have a working Moxie and try this, we would genuinely like to know what happened.

---
📖 [Guides index](README.md) · [Permitting a robot](permitting-a-robot.md) · [The config contract (engineering detail)](../architecture/config-and-telemetry-contract.md#-appearance-the-childs-chosen-face) · [Back to top](../../README.md)
