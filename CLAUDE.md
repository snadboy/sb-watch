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
