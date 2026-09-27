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
