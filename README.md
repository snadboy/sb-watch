# SB Watch

Rules that watch what an [SB Filter](https://github.com/snadboy/sb-filter) matches.
A rule is **a selection + triggers**; each rule is a config entry with its own device page:

| Entity | Meaning |
|---|---|
| `binary_sensor.<rule>_active` | on while anything is active (device class *problem* by default) |
| `sensor.<rule>_count` | how many are active; attributes `entity_ids`, `names`, `pending`, `matched_since` (the clocks), `selection`, `triggers`, `filter`, `advanced` |
| event `sb_watch_changed` | `{rule, entry_id, entered, left, active, count}` on every change (never on the first evaluation) |

Requires the SB Filter integration ≥ 0.5.0 (grammar 4: `classes` pairs; declared as a dependency, install it first).

## A rule

**Which entities** — the selection. Every filled row must match; within a row, any entry matches.

| Row | Example | Meaning |
|---|---|---|
| Patterns | `fp300`, `light.`, `cover.garage_door` | words in one pattern AND; `*` `?` wildcards; an entity id works |
| Areas / Labels | Kitchen, Office | the entity's own, else its device's |
| Classes | `battery:%`, `temperature`, `:°F` | device class **and** unit as a pair; either side optional |

**When do they trigger** — one list of rows; **any one** row triggers the rule for an entity. Each row has its own duration.

| Type | Value | Duration means |
|---|---|---|
| State | a word: `off`, `open`, `unavailable` (empty = any state) | held that state for this long |
| Range | a number: `<20`, `>=80`, `15-50`, `=3` | stayed inside the range for this long |
| Rate | change per time: `>0.5` per hour | the window the rate is measured over |

No rows = every selected entity counts. A number typed into a State row is treated as a Range.

**How durations are timed.** Each row keeps a clock per entity. It starts when the
entity first matches, **seeded from the entity's `last_changed`** — a bulb that
has already been on for two hours counts at once when you create a rule — and it
is **persisted**, so a Home Assistant restart does not start anyone over
(`last_changed` itself is reset by a restart). The "any state" row is the
exception: with no value to match on, only time-since-last-change can say
"unchanged for 6 h", and that does reset at a restart.

Where to edit:

- **The SB Watch panel** — a sidebar entry (admins), `/sb-watch`. Every rule in one
  list with Add, edit, pause and delete, and the full editor: chips with the add
  control beneath, trigger rows editable inline, live "N selected / N match now"
  counts. Shipped inside the integration; nothing to add to a dashboard.
- *Settings → Devices & services → SB Watch* — **Add service** and each rule's
  **gear** open Home Assistant's own form: one page with the same sections
  (*Which entities*, *When do they trigger*, *When the rule is in effect*,
  *Actions*, *Advanced*), a trigger row opening in a small sub-form. It carries a
  link to the same rule in the panel and works on its own if the panel does not.
- The **SB Watch Card** (`rules: all`) puts the panel's list and editor on a dashboard.

**Advanced — filter as YAML.** Paste an SB Entity Browser card's filter. What the
form can express is *absorbed*: the selection fills the chips, the conditions
become trigger rows, and the YAML box empties. What it cannot say stays as YAML
and then defines the whole filter, with its own "matched continuously for" dwell:
a `state_for: "<5m"` (changed *recently*), a `rate` ANDed with `states`.

```yaml
# entry options (version 2)
patterns: []
classes: ["battery:%"]
triggers:
  - {kind: state, value: unavailable, for: 2m}
  - {kind: range, value: "<20", for: 2m}
```

Version 1 entries (one filter + one dwell) are migrated on first load; nothing
changes what it matches, and running clocks carry over.

## Actions — staged

| Action | What happens |
|---|---|
| **none** | entities and the event only — write your own automation |
| **notify** | one notification per rule, **replaced in place** as the active set changes and cleared when it empties. `notify.mobile_app_…` with a `tag`, or a persistent notification when no service is given |
| **notify, then act** | the notification says what will happen; after the **warn-ahead** (`10m`) each entity that is still active gets `homeassistant.<turn_off\|turn_on\|toggle>` |
| **act** | the act at once on every entity that **enters** |

Acting is always on the entities that just entered, never again on the whole set, so a
rule can't keep re-firing on something you've handled. `switch.<rule>_paused` is the
override: on = keep tracking, take no action (it survives restarts). Richer logic
belongs in an automation on `sb_watch_changed`.
