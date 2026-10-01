# SB Watch — session notes

Repo `snadboy/sb-watch`, local `~/projects/git/sb-watch`, branch `main`, MIT.
Born 2026-09-27 as step 4 of the plan in memory `project_sb_watch`. Domain
`sb_watch`, depends on `sb_filter` (manifest `dependencies`; imports
`custom_components.sb_filter.ha.FilterSubscription` + `grammar`).

## Layout

- `rule.py` — pure: `build_filter(options)` (form fields → filter config; the
  advanced `filter_yaml` mapping overrides key by key, `null`/empty deletes),
  `RuleState.apply(matched, now)` → (entered, left) with the dwell
  (`for`), first evaluation = baseline (never "enters"), `next_promotion()`.
- `runner.py` — `RuleRunner` per entry: `FilterSubscription` → `_evaluate`
  → event `sb_watch_changed` + listeners; `async_call_later` for the next
  dwell promotion.
- `binary_sensor.py` (Active, problem class optional), `sensor.py` (Count +
  attributes), `device.py` (SERVICE device named after the rule — entities
  carry only "Active"/"Count", HA 2026.9 prefixes the device name),
  `config_flow.py` (config + options, same form; validation via
  `parse_filter`/`parse_duration`), `diagnostics.py`, service `refresh`.
- `tests/test_rule.py` — plain python3 (stub package past the HA imports).

## Deploying (no HACS yet)

```bash
tar czf /tmp/sbw.tgz -C custom_components sb_watch
ssh snadboy@homeassistant "cat > /tmp/sbw.tgz" < /tmp/sbw.tgz
ssh snadboy@homeassistant "cd /config/custom_components && tar xzf /tmp/sbw.tgz && rm /tmp/sbw.tgz"
```
Full restart for Python changes. Rules are created through the config flow
(`POST /api/config/config_entries/flow {handler: sb_watch}` then the form).

## First live run (2026-09-27)

Three rules via the flow API (fields / labels / YAML) + one invalid
(`state_for: soon` → `bad_duration`, form re-shown). "Batteries low"
(`for: 2m`): matched 13, pending 13, then ONE `sb_watch_changed` with
entered=13 exactly at the dwell, binary sensor on with `active_since`.
Bug found: PyYAML 1.1 read `states: [on]` as `[true]` — `_FilterLoader`
drops the bool resolver so on/off/yes/no stay strings (test added).
Known limitation: the dwell clock restarts on HA restart (RuleState is in
memory) — a RestoreEntity for `matched_since` is the fix if it matters.

## 0.2.0 — staged actions (2026-09-27)

`actions.py` `RuleActions`: mode none/notify/notify_then_act/act;
`on_change(entered, left, active, names)` from the runner. Notify =
replace-in-place (tag / notification_id `sb_watch_<entry_id>`), clear on
empty; `notify.*` service or persistent_notification. Act = ONE
`homeassistant.<act>` on the ENTERED ids (never the whole set);
notify_then_act schedules per-entity `async_call_later(warn_ahead)` and
acts only if still active (`set_active_getter`). `switch.<rule>_paused`
(RestoreEntity) = override: tracking continues, no effects, pending
timers dropped. `actions_pure.format_message` is HA-free for the tests.
Config flow: action select, notify_service (must be `notify.*`), act
select, warn_ahead; validation errors bad_action/bad_notify/bad_act.

### Two bugs the live test found (2026-09-27)

1. **Warn-ahead act ran in the executor.** `async_call_later` with a plain
   lambda is not a @callback → HA ran it off the event loop →
   `async_create_task` raised "from a thread other than the event loop".
   Fix: `HassJob(partial(self._act_if_still, entity_id))` on a @callback.
2. **Paused restored from a DELETED rule.** RestoreEntity is keyed by
   entity_id; a rule deleted and re-created under the same name inherited
   the old switch's "on" and took no action (diagnostics showed
   `paused: true` on a brand-new entry). Fix: the switch writes `entry_id`
   as an attribute and only trusts a restored state from its own entry.

Also: `async_remove_entry` clears the rule's notification (deleting a rule
used to leave its persistent notification behind). Test harness: throwaway
`input_boolean.sb_watch_test` (WS `input_boolean/create`/`delete`), rules
via the flow API, diagnostics via `/api/diagnostics/config_entry/<id>`.

## 0.3.0 — dwell persistence (2026-09-27)

`RuleRunner.snapshot()`/`restore()`: the Count sensor is a RestoreEntity
whose `extra_restore_state_data` (`DwellData`) carries `matched_since`,
`active`, `active_since` and the `entry_id` (guard, as for the Paused
switch). Restored before `runner.start()` (platforms set up first), and
`baselined=True` so changes during the downtime count as real changes.
**Deploy gotcha:** once HACS has installed the integration its files are
ROOT-owned — `tar xzf` as snadboy fails with "can't remove old file" and
the old build keeps running (cost one wasted restart pair). Use `sudo tar`.

### Boot race on restore (2026-09-27)

Restart 2 restored 5 of 13 with 8 back to pending: at the first evaluation
after boot those 8 battery sensors still read `unknown` (integrations not
up yet) → not matched → `matched_since` dropped → clocks reset. Fix
(0.3.0): on a cold start the runner starts at `EVENT_HOMEASSISTANT_STARTED`
+ `STARTUP_SETTLE_SECONDS` (60); the entities show the restored count
meanwhile; `start()` is idempotent. `HassJob` for the timer (a lambda would
run off-loop — same trap as the warn-ahead).

## 0.4.0 — two-step form (2026-09-28)

`config_flow.py` rewritten: `_TwoStep` mixin shared by config and options
flows. Step 1 (`user`/`init`): STEP1_KEYS. Step 2 (`values`): `states` =
`SelectSelector(multiple, custom_value)` with options from
`sb_filter.ha.values_now(hass, scope)` (label "Clear — off (13 now)", value
raw), list mode ≤12 options else dropdown, current entries kept selectable;
`state_min`/`state_max`; a `section("actions", collapsed unless set)` for
action/notify_service/act/warn_ahead — `_merge` FLATTENS it back so
`entry.options` stays flat (runner/actions unchanged, legacy rules load).
`_validate_all` runs `match_now` and turns `unmatched_values` into error
`unknown_value` with a `{unmatched}` placeholder ("Cleat (did you mean
Clear?)"). Driven via the flow API in the test.

## 0.5.0 — rate fields (2026-09-28)

Step 1 gains `rate` (comma list) and `rate_window`; `build_filter` passes
them through; unreadable → `bad_rate` / `bad_duration` on the right field.
Nothing else: the runner hands the filter to SB Filter, which owns rates.

## 0.5.1 (2026-09-28)

Rule subscriptions carry `origin="rule: <name>"` for SB Filter's Live filters sensor.

## 0.5.2 — a new rule acts on what it finds (2026-09-28)

User added the desk bulb (on for 1 h 50 min) with a 1-minute timeout via
the card: "Turn off failed". Diagnostics: active=[bulb], last_action=None
— the first evaluation was treated as a BASELINE and never "entered"
anything, so no action was scheduled. Now only a RESTORED rule (restore()
sets `baselined` + `active`) compares against its previous set; a
brand-new rule's first evaluation enters everything it finds. Tests
updated (+ restored-set case).

Also 0.5.2: unloading a rule after startup logged "Unable to remove unknown job listener" — the one-time HOMEASSISTANT_STARTED listener had already removed itself; the unload hook now skips it once fired.

## 0.6.0 — run_script act + action logging (2026-09-28)

User: actions at timeout = Turn on / Turn off / Toggle / Run script; "are
timeout actions logged?". `act: run_script` + `act_script` (script entity
picker; `script.turn_on` with variables entity_id/entity_ids/rule).
Every act now: `logbook.log` on EACH acted entity (name "SB Watch",
message "turn off by rule “X”"), a `sb_watch_action` event, INFO log line,
and Count-sensor attributes `action/act/act_script/last_action{at}/
actions_taken`. Flow validation `bad_script`.

## 0.7.0 — `run_actions`: an HA action list at the timeout (2026-09-28)

User: "instead of On/Off/Toggle, execute an action like SB Schedule does"
+ other domains (cover…). `act: run_actions` + `act_actions` (HA
ActionSelector in the flow) → `homeassistant.helpers.script.Script`
(cv.SCRIPT_SCHEMA), built once per runner, `async_run` with variables
entity_id/entity_ids/rule and a fresh Context. Validation `bad_actions`.
The quick acts stay for compatibility. Logbook says "ran N actions by
rule …".

## 0.7.1 — the push has a tap target (2026-09-28)

`notify_url` option; else `entityId:<first active>` — sent as both
`clickAction` (Android) and `url` (iOS) in the notify data. Persistent
notifications unchanged.

## 0.8.0 — when the rule is in effect (2026-09-29)

User: a checkbox for a time window (may cross midnight, 6PM→6AM) and a
checkbox for weekdays, ANDed. `rule.Effect.from_options` (pure, tested):
window_enabled/start/end + days_enabled/days; a window crossing midnight
attributes the early-morning part to the day it STARTED (Friday 18:00 →
Saturday 06:00 = "fri"); empty/unreadable window = no window; days
enabled with none ticked = every day. Runner: the gate multiplies the
match — outside it `apply(())` so Active off / Count 0 / no actions, and
when it opens the current matches ENTER (dwell then runs). A timer fires
at the next edge/midnight (`next_change`) so the flip is on time.
Flow: section "effect" (collapsed unless enabled) with Time selectors
and a weekday multi-select; `_merge` flattens. Count sensor attributes
`in_effect`, `window`, `days`.

## 0.9.0 — selection + triggers; per-trigger durations (2026-10-01)

User sketch: split the form into "Selection filter" and "Triggers", every
trigger row with its own duration (they had asked what dwell vs time-in-state
meant — two concepts for one idea). Built:

- **Options v2** (`OPTIONS_VERSION`, `async_migrate_entry`): `patterns[]`,
  `labels[]`, `areas[]`, `classes[]` (`"battery:%"` pairs — SB Filter grammar 4),
  `triggers[{kind: state|range|rate, value, for, per}]`, plus `filter_yaml` +
  `for` for the advanced path only. `rule.upgrade_options` converts v1 exactly
  (tests carry the nine live rules); what the model cannot say stays YAML.
- **Clauses**: one SB Filter subscription per trigger (`build_clauses`), a
  `RuleState` dwell each, `RuleEngine` = the union. The runner waits for EVERY
  clause's first payload before evaluating (a partial union would drop and
  re-enter entities → a second notification).
- **Timing decision, measured not guessed.** First cut timed word states with
  `state_for` (last_changed). Restart test: "Batteries low" 13 → 1, because HA
  resets last_changed at every restart and v1's persisted dwell had not. Final:
  state AND range rows both use the persisted dwell clock, SEEDED from
  last_changed at first match (`RuleState.apply(..., since)`). Second restart:
  13 restored before the first evaluation and held. Only the any-state row
  keeps `state_for`.
- **Flow**: step 1 chips (SelectSelector custom_value) + collapsed Advanced
  section; step 2 `ObjectSelector(fields, multiple)` trigger list + the old
  effect/actions sections verbatim. YAML is ABSORBED into fields + rows when
  representable (`_absorb_yaml`); a step-2 post without `triggers` keeps the
  absorbed rows — that is how the Entity Browser's save-as-rule lands as rows.
- GOTCHA: a translation containing `<act>` renders "Translation error:
  UNCLOSED_TAG" in the form — no angle-bracket words in strings.json.
