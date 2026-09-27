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
CONF_FOR = "for"                 # rule-level dwell: matched continuously this long before it counts
CONF_FILTER_YAML = "filter_yaml"  # advanced: a YAML mapping in the FILTER.md grammar; overrides the fields
CONF_PROBLEM = "problem"          # binary sensor device_class problem (on = something needs attention)

# actions (entry.options)
CONF_ACTION = "action"             # none | notify | notify_then_act | act
CONF_NOTIFY_SERVICE = "notify_service"   # "notify.mobile_app_x"; empty = a persistent notification
CONF_ACT = "act"                   # turn_off | turn_on | toggle  (homeassistant.<act> on the entered entities)
CONF_WARN_AHEAD = "warn_ahead"     # notify_then_act: act this long after the notification, if still active
ACTIONS = ("none", "notify", "notify_then_act", "act")
ACTS = ("turn_off", "turn_on", "toggle")

FILTER_FIELDS = (CONF_PATTERNS, CONF_LABELS, CONF_AREAS, CONF_DEVICE_CLASSES, CONF_UNITS, CONF_STATES, CONF_STATE_FOR)
DWELL_TICK_SECONDS = 30
STARTUP_SETTLE_SECONDS = 60   # after EVENT_HOMEASSISTANT_STARTED, before the first evaluation on a cold start
