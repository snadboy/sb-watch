// ---- the panel shell (tools/panel_shell.js) ------------------------------------
// A sidebar page: HA's toolbar, then the all-rules list. Add / Edit open the rule
// editor defined above. /sb-watch?edit=<entry_id> opens that rule; ?add=1 a new one
// (the links on the integration's own forms).
class SbWatchPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    this._build();
    this._rules.hass = hass;
    this._menu.hass = hass;
  }
  set narrow(v) { this._narrow = v; if (this._menu) this._menu.narrow = v; }
  set route(_v) { /* single page */ }
  set panel(_v) { /* no panel config */ }

  _build() {
    if (this._rules) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color); }
      .bar { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px; box-sizing: border-box; position: sticky; top: 0; z-index: 2;
             background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); border-bottom: 1px solid var(--divider-color); }
      .title { font-size: 20px; flex: 1; margin-left: 4px; }
      .bar a { color: inherit; font-size: 14px; opacity: .85; text-decoration: none; padding: 6px 10px; border-radius: 14px; }
      .bar a:hover { background: rgba(127,127,127,.2); opacity: 1; }
      .page { padding: 16px; }
      .col { max-width: 860px; margin: 0 auto; }
      .intro { color: var(--secondary-text-color); font-size: 14px; margin: 0 4px 12px; line-height: 1.5; }
      .ver { color: var(--secondary-text-color); font-size: 12px; margin: 12px 4px 0; }
    </style>
    <div class="bar"><ha-menu-button></ha-menu-button><div class="title">SB Watch</div><a href="/config/integrations/integration/sb_watch">Integration page</a></div>
    <div class="page"><div class="col">
      <div class="intro">A rule watches the entities of a named filter (or a list of entities) and counts one of them when a trigger holds: a state, a numeric range or a rate of change, for as long as you say. Add a rule, or use the pencil on one to edit it.</div>
      <div class="slot"></div>
      <div class="ver">SB Watch __VERSION__ · editor __CARD_VERSION__</div>
    </div></div>`;
    this._menu = root.querySelector("ha-menu-button");
    this._menu.narrow = this._narrow;
    const rules = document.createElement(CARD);
    rules.setConfig({ title: "Rules", rules: "all" });
    root.querySelector(".slot").appendChild(rules);
    this._rules = rules;
    this._deepLink();
  }

  async _deepLink() {
    const q = new URLSearchParams(location.search);
    const edit = q.get("edit"), add = q.get("add");
    if (!edit && !add) return;
    for (let i = 0; i < 80 && !this._rules._loaded; i++) await new Promise((r) => setTimeout(r, 250));
    history.replaceState(history.state, "", location.pathname);
    if (add) { this._rules._openEditor(null); return; }
    const rule = this._rules._rules.find((r) => r.entryId === edit);
    if (rule) this._rules._openEditor(rule);
  }
}
if (!customElements.get("sb-watch-panel")) customElements.define("sb-watch-panel", SbWatchPanel);
console.info("%c SB-WATCH-PANEL %c v__VERSION__ ", "background:#455a64;color:#fff", "background:#90a4ae;color:#000");
