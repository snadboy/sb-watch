"""SB Watch constants."""

DOMAIN = "sb_watch"
EVENT_CHANGED = "sb_watch_changed"

# rule options (entry.options)
CONF_NAME = "name"
CONF_PATTERNS = "patterns"
CONF_LABELS = "labels"
CONF_AREAS = "areas"
CONF_DEVICE_CLASSES = "device_classes"
CONF_UNITS = "units"
CONF_STATES = "states"
CONF_STATE_FOR = "state_for"
CONF_RATE = "rate"
CONF_RATE_WINDOW = "rate_window"
CONF_FOR = "for"                 # rule-level dwell: matched continuously this long before it counts
CONF_FILTER_YAML = "filter_yaml"  # advanced: a YAML mapping in the FILTER.md grammar; overrides the fields
CONF_PROBLEM = "problem"          # binary sensor device_class problem (on = something needs attention)

# when the rule is in effect (entry.options) — both optional, ANDed
CONF_WINDOW_ENABLED = "window_enabled"
CONF_WINDOW_START = "window_start"     # "18:00:00"
CONF_WINDOW_END = "window_end"         # "06:00:00" — may be earlier than start (crosses midnight)
CONF_DAYS_ENABLED = "days_enabled"
CONF_DAYS = "days"                     # ["mon", …]; for a window crossing midnight the day is the one it STARTED on
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# actions (entry.options)
CONF_ACTION = "action"             # none | notify | notify_then_act | act
CONF_NOTIFY_SERVICE = "notify_service"   # "notify.mobile_app_x"; empty = a persistent notification
CONF_NOTIFY_URL = "notify_url"           # where a tap on the push goes: a dashboard path, or entityId:<id>; empty = the first active entity's more-info
CONF_ACT = "act"                   # turn_off | turn_on | toggle | run_script
CONF_ACT_SCRIPT = "act_script"     # run_script: the script entity to run (variables: entity_id, entity_ids, rule)
CONF_ACT_ACTIONS = "act_actions"   # run_actions: an HA action list (like a script sequence), run with the same variables
EVENT_ACTION = "sb_watch_action"
CONF_WARN_AHEAD = "warn_ahead"     # notify_then_act: act this long after the notification, if still active
ACTIONS = ("none", "notify", "notify_then_act", "act")
ACTS = ("turn_off", "turn_on", "toggle", "run_script", "run_actions")

FILTER_FIELDS = (CONF_PATTERNS, CONF_LABELS, CONF_AREAS, CONF_DEVICE_CLASSES, CONF_UNITS, CONF_STATES, CONF_STATE_FOR, CONF_RATE, CONF_RATE_WINDOW)
DWELL_TICK_SECONDS = 30
STARTUP_SETTLE_SECONDS = 60   # after EVENT_HOMEASSISTANT_STARTED, before the first evaluation on a cold start
