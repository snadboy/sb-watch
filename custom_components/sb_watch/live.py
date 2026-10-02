"""Home Assistant glue for state conditions: translated states, vocabularies,
rate history, and the one-shot answers the editors ask for.

Moved here from SB Filter (its grammar 4 `ha.py`) when the split was made:
SB Filter says WHICH entities, SB Watch owns everything about their STATE."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_cached_translations, async_translate_state
from homeassistant.util import dt as dt_util

from custom_components.sb_filter.ha import match_now
from custom_components.sb_filter.named import named_filters

from .condition import Condition, Lookup, StateRow, is_number, matching, parse_condition, unmatched_values, values
from .const import DOMAIN

RATE_MAX_SAMPLES = 2000


def _data(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DOMAIN, {})


@callback
def _vocabulary_for(hass: HomeAssistant, domain: str, device_class: str | None, platform: str | None,
                    translation_key: str | None) -> list[tuple[str, str | None]]:
    """The states HA's translation tables know for this kind of entity: (raw, translated).

    Keys look like  component.<domain>.entity_component.<device_class|_>.state.<raw>
    and, for an entity with its own vocabulary,
                    component.<platform>.entity.<domain>.<translation_key>.state.<raw>
    — exactly what async_translate_state reads, so the aliases can never disagree."""
    lang = hass.config.language
    found: dict[str, str] = {}
    if platform and translation_key:
        prefix = f"component.{platform}.entity.{domain}.{translation_key}.state."
        for k, v in async_get_cached_translations(hass, lang, "entity", platform).items():
            if k.startswith(prefix) and "." not in k[len(prefix):]:
                found[k[len(prefix):]] = v
    if not found:
        table = async_get_cached_translations(hass, lang, "entity_component", domain)
        for dc in ((device_class or "_"), "_"):
            prefix = f"component.{domain}.entity_component.{dc}.state."
            for k, v in table.items():
                if k.startswith(prefix) and "." not in k[len(prefix):]:
                    found.setdefault(k[len(prefix):], v)
            if found:
                break
    return [(raw, label) for raw, label in found.items()]


class RateTracker:
    """Numeric sample buffers for the entities a rate trigger needs.

    Seeded from the recorder once per entity (so a cold start can answer at
    once), then appended live from state_changed. Kept only while some rule
    with a rate trigger is running; capped per entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.buffers: dict[str, deque] = {}
        self.window: dict[str, float] = {}
        self.users: set[str] = set()                # entry ids of the rules using rates
        self._seeded: set[str] = set()
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def samples(self, entity_id: str, window: float) -> list[tuple[datetime, float]]:
        return list(self.buffers.get(entity_id, ()))

    @callback
    def release(self, user: str) -> None:
        """A rule stopped. When no rule uses rates any more, drop every buffer (the next one re-seeds)."""
        self.users.discard(user)
        if self.users:
            return
        self.buffers.clear()
        self.window.clear()
        self._seeded.clear()
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _on_state(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None or new.entity_id not in self.buffers:
            return
        n = is_number(new.state)
        if n is None:
            return
        buf = self.buffers[new.entity_id]
        buf.append((new.last_updated, n))
        keep_from = dt_util.utcnow() - timedelta(seconds=self.window.get(new.entity_id, 0) * 2 + 60)
        while len(buf) > 1 and buf[0][0] < keep_from and buf[1][0] <= keep_from:
            buf.popleft()          # keep one sample older than the window as the reference

    async def ensure(self, user: str | None, entity_ids: list[str], window: float) -> bool:
        """Seed buffers for any of these entities not yet tracked. True if anything was seeded."""
        if user:
            self.users.add(user)
        if self._unsub is None:
            self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state)
        todo = []
        for e in entity_ids:
            self.window[e] = max(self.window.get(e, 0), window)
            if e not in self._seeded:
                self._seeded.add(e)
                self.buffers.setdefault(e, deque(maxlen=RATE_MAX_SAMPLES))
                todo.append(e)
        if not todo:
            return False
        try:
            from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415
        except ImportError:
            return True
        start = dt_util.utcnow() - timedelta(seconds=window * 2 + 60)

        def _fetch(ids: list[str]) -> dict[str, list]:
            return {e: history.state_changes_during_period(self.hass, start, None, entity_id=e, no_attributes=True,
                                                           include_start_time_state=True).get(e, []) for e in ids}

        for i in range(0, len(todo), 50):
            try:
                rows = await get_instance(self.hass).async_add_executor_job(_fetch, todo[i:i + 50])
            except Exception:  # noqa: BLE001 - recorder off or busy: buffers fill live instead
                continue
            for e, states in rows.items():
                buf = self.buffers[e]
                have = {ts for ts, _ in buf}
                for st in states:
                    n = is_number(st.state)
                    if n is not None and st.last_updated not in have:
                        buf.append((st.last_updated, n))
                srt = sorted(buf)
                buf.clear()
                buf.extend(srt)
        return True


def rate_tracker(hass: HomeAssistant) -> RateTracker:
    d = _data(hass)
    if "rates" not in d:
        d["rates"] = RateTracker(hass)
    return d["rates"]


@callback
def lookup(hass: HomeAssistant) -> Lookup:
    """Translated states and vocabularies (cached per kind of entity) + rate history."""
    ent_reg = er.async_get(hass)
    fcache = _data(hass).setdefault("fmt_cache", {})
    vcache = _data(hass).setdefault("vocab_cache", {})
    if len(fcache) > 5000:
        fcache.clear()

    def _kind(entity_id: str):
        st = hass.states.get(entity_id)
        entry = ent_reg.async_get(entity_id)
        return st, entity_id.split(".", 1)[0], (entry.platform if entry else None), (entry.translation_key if entry else None)

    def formatted(entity_id: str) -> str | None:
        st, domain, platform, tkey = _kind(entity_id)
        if st is None:
            return None
        dclass = st.attributes.get("device_class")
        key = (st.state, domain, platform, tkey, dclass)
        if key not in fcache:
            try:
                fcache[key] = async_translate_state(hass, st.state, domain, platform, tkey, dclass)
            except Exception:  # noqa: BLE001 - a translation gap must never break matching
                fcache[key] = st.state
        return fcache[key]

    def vocabulary(entity_id: str) -> list[tuple[str, str | None]]:
        st, domain, platform, tkey = _kind(entity_id)
        if st is None:
            return []
        dclass = st.attributes.get("device_class")
        key = (domain, dclass, platform, tkey)
        if key not in vcache:
            try:
                vcache[key] = _vocabulary_for(hass, domain, dclass, platform, tkey)
            except Exception:  # noqa: BLE001
                vcache[key] = []
        vocab = list(vcache[key])
        options = st.attributes.get("options")          # enum sensors carry their own list
        if isinstance(options, (list, tuple)):
            known = {r.lower() for r, _ in vocab}
            vocab += [(str(o), None) for o in options if str(o).lower() not in known]
        return vocab

    return Lookup(formatted=formatted, vocabulary=vocabulary, history=rate_tracker(hass).samples)


@callback
def rows(hass: HomeAssistant, ids) -> dict[str, StateRow]:
    out = {}
    for e in ids:
        st = hass.states.get(e)
        if st is not None:
            out[e] = StateRow(state=st.state, attributes=st.attributes, last_changed=st.last_changed)
    return out


async def async_prepare_rates(hass: HomeAssistant, user: str | None, conds: list[Condition], ids) -> bool:
    """Seed rate history for the numeric entities among `ids` that a rate condition needs."""
    windows = [c.rate_window or 0 for c in conds if c.rates]
    if not windows:
        return False
    numeric = [e for e in ids if (st := hass.states.get(e)) is not None and is_number(st.state) is not None]
    return await rate_tracker(hass).ensure(user, numeric, max(windows))


@callback
def selected_ids(hass: HomeAssistant, source: dict[str, Any]) -> list[str]:
    """The entities a rule's source names: {filter: id} (a named filter's current set),
    {entities: [...]}, {selection: {...}} (SB Filter's answer), or a bare selection dict."""
    if not source:
        return []
    if "filter" in source:
        nf = named_filters(hass).get(source["filter"])
        return list(nf.ids) if nf else []
    if "entities" in source:
        return sorted(dict.fromkeys(e for e in source["entities"] if e))
    sel = source.get("selection", source)
    return list(match_now(hass, sel)[1].ids) if sel else []


@callback
def values_now(hass: HomeAssistant, ids: list[str]) -> list[dict]:
    return values(rows(hass, ids), ids, lookup(hass))


@callback
def unmatched_now(hass: HomeAssistant, condition: dict[str, Any], ids: list[str]) -> list[str]:
    """Typed words no selected entity can be in, as 'Cleat (did you mean Clear?)'."""
    cond = parse_condition(condition)
    return [u.value + (f" (did you mean {', '.join(u.suggestions)}?)" if u.suggestions else "")
            for u in unmatched_values(cond, rows(hass, ids), ids, lookup(hass))]


async def async_preview(hass: HomeAssistant, source: dict[str, Any], conditions: list[dict[str, Any]]) -> dict[str, Any]:
    """For an editor: what the source holds, and which of those meet each condition now (durations not applied)."""
    ids = selected_ids(hass, source)
    conds = [parse_condition(c) for c in conditions]
    await async_prepare_rates(hass, None, conds, ids)
    rs, look, now = rows(hass, ids), lookup(hass), dt_util.utcnow()
    per = [matching(c, rs, ids, look, now) for c in conds]
    tracker = rate_tracker(hass)
    if not tracker.users:
        tracker.release("")          # a one-shot answer keeps no buffers when no rule needs them
    union = sorted({e for m in per for e in m})
    return {
        "selected": ids,
        "matching": union,
        "per_condition": [len(m) for m in per],
        "unmatched": [u.value + (f" (did you mean {', '.join(u.suggestions)}?)" if u.suggestions else "")
                      for c in conds for u in unmatched_values(c, rs, ids, look)],
        "unreadable": [u for c in conds for u in c.unreadable],
    }
