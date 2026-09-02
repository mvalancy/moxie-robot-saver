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

**Eleven of the fourteen layers, 72 choices in all.** The two colour layers show a preview
swatch you can tap; the rest are named pieces of artwork that live on the robot itself, so
the card offers them by name.

| Layer | Colours we can preview | Named artwork | |
|---|---|---|---|
| **Eye colour** | Green · Blue · Purple · Brown · Gold · Teal | Brown · Gold · Grey · Hazel · Light blue · Purple · Turquoise | |
| **Eye design** | — | Blue circuits · Blue clouds · Circuits · Clouds · Gears · Gold stars · Purple gears · Red hearts · Stars | |
| **Eyelids** | — | Green eyeshadow · Purple eyeshadow · Rainbow stars · Red eyeshadow · Smokey lashes | |
| **Eyebrows** | — | Brown, cut · Grey, short · Purple · White, bushy · Yellow, thin | |
| **Mouth** | — | Black, small · Dark red, medium · Pink, pointy · Purple, full · Red, medium | |
| **Nose** | — | Cat · Clown · Dog · Human · Pig | |
| **Moustache** | — | Black, angled · Black Dali · Brown handlebar · Orange bat-wing · Yellow, upturned | |
| **Face colour** | Blue · Yellow · Green · Teal · Pink · Purple | Green · Pink · Purple · Teal · Yellow | |
| **Face design** | — | Candies · Flowers · Hearts · Leaves · Stars | |
| **Hair** | — | Black bob · Black, centre part · Pink shag · Red shag | |
| **Glasses** | — | Blue hearts · Gold half-round · Red cat-eye · Round, white dots · Small round | |
| **Stickers** | — | — | nothing anywhere names a piece for this layer |
| **Extras** | — | — | nothing anywhere names a piece for this layer |
| **Misc** | — | — | nothing anywhere names a piece for this layer |

Where a name appears in both columns of the same row (eye colour, face colour), they are
two different ways of asking for the same idea and the card marks the artwork one
`(bundle)`. If one does nothing, try the other.

Pick what you like, press **Save look**, and it goes down to the robot on the spot — no
reboot, no re-pairing. **Reset to default** puts everything back.

## Where these choices come from — and why it matters

The two lists behave differently, and the difference is worth thirty seconds of your time.

**The colours** come from our own study of Moxie's software: they are written down in the
robot's own code as a fixed list of six eye colours and six face colours, each with an exact
colour value, which is why we can show you a swatch. They are the only choices in the card
we can *draw* for you rather than merely name.

**The named artwork** — the other 60 choices — comes from
[OpenMoxie](https://github.com/jbeghtol/openmoxie), a sibling revival project (MIT licence)
whose authors had a working robot in front of them and wrote down the names it used. We
copied their **list of names and nothing else**, recorded exactly which version we took it
from, and wrote all the friendly labels above ourselves. The full citation sits in the data
file (`mqtt/moxie_sdk/face_assets.json`) and in [`ATTRIBUTION.md`](../../ATTRIBUTION.md).

**Three layers are still empty** — stickers, extras and misc. Neither source names a single
piece for them. We would rather show you an empty shelf than make names up and have Moxie
draw nothing.

## Two warnings that come with the artwork

**Some of these pieces are known to crash Moxie's display.** OpenMoxie's own note beside the
list says so plainly — some of them crashed the robot during testing — and it does not say
*which*. Our own study of the robot's software found the same thing said a second time, from
the other direction. So: **change the named-artwork layers one at a time, and save between
each.** One at a time is how you find out which one your robot dislikes. The colours are not
implicated in either warning.

**Your robot may not have all of these.** The artwork is downloaded to the robot in a pack,
and there is no promise that every robot's pack holds the same pieces. A name your robot has
never heard of simply does not draw.

If you know a name that is not on the list — from your own robot, or another project — open
**Advanced: layer names** and type it in, one per line. Those go through exactly as you typed
them, unchanged. The same one-at-a-time rule applies, for the same reason.

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

**No physical Moxie has ever rendered any of this** — not one option in the table above,
colours included. This project has no robot to test against. The plumbing — the picker, the
layering, the texture key, the config push — is tested end to end against a simulated robot
and is correct as far as our documents go. The 60 artwork names came from a project whose
authors *did* have a robot, which is a much better position than guessing, but it is still
their robot and not yours. And two details remain informed guesses, flagged as such in the
code and the contract: exactly how a colour choice is spelled on the wire, and the fact that
the texture cache is keyed the way we key it. Both are one-line changes if a real robot ever
tells us different.

If you have a working Moxie and try this, we would genuinely like to know what happened.

---
📖 [Guides index](README.md) · [Permitting a robot](permitting-a-robot.md) · [The config contract (engineering detail)](../architecture/config-and-telemetry-contract.md#-appearance-the-childs-chosen-face) · [Back to top](../../README.md)
