# SB Watch

Rules that watch what an [SB Filter](https://github.com/snadboy/sb-filter) matches.
A rule is **a filter + a dwell**; each rule is a config entry with its own device page:

| Entity | Meaning |
|---|---|
| `binary_sensor.<rule>_active` | on while anything is active (device class *problem* by default) |
| `sensor.<rule>_count` | how many are active; attributes `entity_ids`, `names`, `pending` (matched but still dwelling), `filter`, `for` |
| event `sb_watch_changed` | `{rule, entry_id, entered, left, active, count}` on every change (never on the first evaluation) |

Requires the SB Filter integration (declared as a dependency; install it first).

## A rule

*Settings → Devices & services → Add integration → SB Watch.* Pickers for labels and
areas, text for the rest, and an **advanced YAML field** that takes an SB Entity
Browser card's filter keys verbatim and overrides the fields:

```yaml
device_classes: [battery]
units: ["%"]
states: [unavailable, "<20"]
```

`for` is the rule's own dwell (its clocks survive an HA restart): an entity counts only after it has matched
continuously that long (`10m`, `2h`) — hysteresis for values that flap around a
threshold. It is separate from the filter's `state_for` (time in the current state).

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
