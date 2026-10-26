/* MarketingIQ operator interface.
 *
 * A dependency-free single-page app served by the FastAPI backend. It talks only to the
 * same-origin /api/v1 endpoints with a short-lived bearer token kept in sessionStorage.
 * All dynamic text is inserted with textContent (never innerHTML) to avoid script injection.
 */
"use strict";

(() => {
  const TOKEN_KEY = "marketingiq.token";
  const ORG_KEY = "marketingiq.org";

  const CRITERION_KINDS = [
    "industry",
    "country",
    "location",
    "employee_min",
    "employee_max",
    "company_size",
    "active_hiring",
    "business_service",
    "keyword",
    "presence_signal",
  ];

  const FRIENDLY_ERRORS = {
    OUTBOUND_DRAFT_NOT_APPROVED: "Approve the draft before sending it.",
    OUTBOUND_DRAFT_ALREADY_SENT: "This draft has already been sent.",
    OUTBOUND_RECIPIENT_SUPPRESSED: "This recipient is on the suppression list (unsubscribed or blocked).",
    OUTBOUND_VERIFIED_EMAIL_REQUIRED: "The contact needs a verified (VALID) email before sending.",
    OUTBOUND_REQUALIFICATION_REQUIRED:
      "Company intelligence changed since qualification. Re-assess fit and re-qualify first.",
    OUTBOUND_QUALIFICATION_NOT_ACTIONABLE: "The lead is not qualified for outreach.",
    OUTBOUND_BUYER_ROLE_ALIGNMENT_REQUIRED: "The contact does not match the recommended buyer role.",
    OUTBOUND_IDEMPOTENCY_KEY_REUSED: "Send request conflicted with another draft. Try again.",
    CAMPAIGN_DRAFT_VERIFIED_EMAIL_REQUIRED: "Verify the contact's email before drafting a campaign.",
    CAMPAIGN_DRAFT_VALUE_PROPOSITION_REQUIRED:
      "Add a value proposition to the product before drafting campaigns.",
    CAMPAIGN_DRAFT_QUALIFICATION_NOT_ACTIONABLE: "The lead is not qualified for outreach.",
    CAMPAIGN_DRAFT_REQUALIFICATION_REQUIRED:
      "Company intelligence changed. Re-assess fit and re-qualify before drafting.",
    CAMPAIGN_DRAFT_BUYER_ROLE_ALIGNMENT_REQUIRED:
      "The contact does not match the recommended buyer role.",
    CAMPAIGN_DRAFT_ALREADY_APPROVED: "This draft is already approved.",
    CAMPAIGN_DRAFT_ALREADY_REJECTED: "This draft is already rejected.",
    CAMPAIGN_DRAFT_EDIT_REQUIRED_AFTER_REJECTION: "Edit the rejected draft before approving it.",
    CONTACT_DISCOVERY_BUYER_ROLE_REQUIRED:
      "The qualification has no recommended buyer role. Add buyer roles to the product.",
    CONTACT_DISCOVERY_DOMAIN_REQUIRED: "The company needs a domain for contact discovery.",
    EMAIL_FIND_NAME_REQUIRED: "The contact needs a first and last name to find an email.",
    EMAIL_REQUIRED: "Enter an email address to verify.",
    OPPORTUNITY_REQUIRES_SENT_ATTEMPT: "Opportunities can only be created from a sent email.",
    OPPORTUNITY_STAGE_CANNOT_MOVE_BACKWARD: "Opportunity stages cannot move backward.",
    OPPORTUNITY_STAGE_IS_TERMINAL: "This opportunity is already closed.",
    OPPORTUNITY_WON_REQUIRES_PROPOSAL: "Move the opportunity to PROPOSAL before marking it WON.",
    INVALID_OPPORTUNITY_STAGE_TRANSITION: "That stage change is not allowed.",
    ORCHESTRATION_ACTIVE_ICP_REQUIRED: "Activate an ICP for this product first.",
    ORCHESTRATION_CURRENT_FIT_REQUIRED: "A current fit assessment is required first.",
    ORCHESTRATION_ACTIONABLE_QUALIFICATION_REQUIRED: "An actionable qualification is required first.",
    ORCHESTRATION_PROVIDER_CREDIT_APPROVAL_REQUIRED:
      "Contact discovery spends provider credits. Tick “Allow provider credits” to continue.",
  };

  Object.assign(FRIENDLY_ERRORS, {
    DECISION_MAKERS_PERSONAS_REQUIRED: "Add target personas (or buyer roles) to the product first.",
    HUNTER_NOT_CONFIGURED: "Hunter is not configured, so emails cannot be found or verified.",
    CAMPAIGN_DRAFT_CONTACT_EXCLUDED: "This person was excluded. Reset the review to use them.",
    CAMPAIGN_DRAFT_CONTACT_EMPLOYMENT_NOT_CURRENT:
      "Evidence says this person no longer holds the role (or sources conflict). Approve them manually if you know otherwise.",
    OUTBOUND_CONTACT_EXCLUDED: "This person was excluded from outreach.",
    OUTBOUND_CONTACT_EMPLOYMENT_NOT_CURRENT: "Evidence says this person may no longer work there.",
    PERSONA_NAME_DUPLICATE: "Two personas have the same name.",
  });

  const OUTCOME_TEXT = {
    READY_FOR_DRAFT: "Ready: at least one qualified decision-maker has a verified email.",
    EMAIL_UNVERIFIED: "Decision-makers found, but their emails are not verified yet.",
    CONTACT_FOUND_EMAIL_MISSING: "Decision-makers found, but no email yet. Allow provider credits or use Find email.",
    INSUFFICIENT_EVIDENCE: "People found, but the evidence is too weak to target anyone yet.",
    NO_RELEVANT_DECISION_MAKER: "People were found, but none match the target personas.",
    NO_CONTACTS_FOUND: "No named people were found in the sources checked.",
  };

  FRIENDLY_ERRORS.AUTOMATION_POLICY_ALREADY_EXISTS =
    "This company already has a schedule for that product. Manage it under Automation.";

  const STOP_REASONS = {
    DECISION_MAKERS_CONTACT_FOUND_EMAIL_MISSING:
      "Decision-makers found on public sources. Emails are missing: allow provider credits or open Contacts.",
    DECISION_MAKERS_EMAIL_UNVERIFIED: "Decision-makers found; their emails still need verification.",
    DECISION_MAKERS_INSUFFICIENT_EVIDENCE: "People found, but not enough evidence to target them. Review Contacts.",
    DECISION_MAKERS_NO_RELEVANT_DECISION_MAKER: "No one matching the target personas was found.",
    DECISION_MAKERS_NO_CONTACTS_FOUND: "No named people were found. Allow provider credits to search Hunter.",
    HUMAN_CAMPAIGN_GATE: "Ready for you: contacts found. Draft, review and approve a campaign next.",
    NO_READY_STEP: "No further automatic step is available. Check the plan below.",
    PROVIDER_CREDIT_APPROVAL_REQUIRED:
      "Stopped before contact discovery because it spends provider credits.",
  };

  const GOOD = new Set([
    "ACTIVE", "COMPLETED", "COMPLETE", "SENT", "APPROVED", "VALID", "HIGH_PRIORITY", "QUALIFIED",
    "WON", "EXACT", "STRONG", "A", "B", "available", "DONE", "CURRENT", "true",
  ]);
  const WARN = new Set([
    "DRAFT", "PARTIAL", "PENDING", "RUNNING", "NEEDS_MORE_RESEARCH", "NURTURE", "STALE", "RISKY",
    "ACCEPT_ALL", "C", "READY", "CONFLICTED", "UNVERIFIABLE",
  ]);
  const BAD = new Set([
    "FAILED", "REJECTED", "INVALID", "NOT_QUALIFIED", "LOST", "D", "ARCHIVED", "not_configured",
    "BLOCKED", "false",
  ]);
  const INFO = new Set(["CONTACTED", "RESPONDED", "MEETING", "PROPOSAL", "IN_PROGRESS"]);
  WARN.add("PAUSED");
  ["CURRENT_CONFIRMED", "READY_FOR_DRAFT", "VERIFIED", "HIGH"].forEach((x) => GOOD.add(x));
  [
    "CURRENT_LIKELY", "MEDIUM", "EMAIL_UNVERIFIED", "CONTACT_FOUND_EMAIL_MISSING", "CURRENT_UNCERTAIN",
    "PROVIDER_FOUND", "UNVERIFIED", "INSUFFICIENT_EVIDENCE", "LOW", "UNKNOWN",
  ].forEach((x) => WARN.add(x));
  ["FORMER", "CONFLICTING", "EXCLUDED", "NOT_RELEVANT", "NONE", "PATTERN_PREDICTED"].forEach((x) => BAD.add(x));

  const state = {
    token: safeGet(TOKEN_KEY),
    orgId: safeGet(ORG_KEY),
    me: null,
    renderId: 0,
    productCache: null,
  };

  // ---------------------------------------------------------------------------------------
  // Utilities

  function safeGet(key) {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function safeSet(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch {
      /* storage unavailable: keep in memory only */
    }
  }

  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    if (props) {
      for (const [key, value] of Object.entries(props)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") el.className = value;
        else if (key.startsWith("on") && typeof value === "function") {
          el.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (key === "value") el.value = value;
        else if (key === "checked") el.checked = Boolean(value);
        else if (key === "selected") el.selected = Boolean(value);
        else if (value === true) el.setAttribute(key, "");
        else el.setAttribute(key, String(value));
      }
    }
    append(el, children);
    return el;
  }

  function append(el, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
    return el;
  }

  function fmtDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function fmtValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) return value.map(fmtValue).join(", ");
    if (typeof value === "object") {
      if ("min" in value || "max" in value) {
        const min = value.min ?? null;
        const max = value.max ?? null;
        if (min === null && max === null) return "—";
        if (min === null) return `up to ${max}`;
        if (max === null) return `${min}+`;
        return min === max ? String(min) : `${min}–${max}`;
      }
      return JSON.stringify(value);
    }
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
    return String(value);
  }

  function fmtPct(value) {
    if (value === null || value === undefined) return "—";
    const n = Number(value);
    if (Number.isNaN(n)) return String(value);
    return `${Math.round(n <= 1 ? n * 100 : n)}%`;
  }

  function badge(value) {
    const text = value === null || value === undefined ? "—" : String(value);
    let tone = "";
    if (GOOD.has(text)) tone = "good";
    else if (WARN.has(text)) tone = "warn";
    else if (BAD.has(text)) tone = "bad";
    else if (INFO.has(text)) tone = "info";
    return h("span", { class: `badge ${tone}` }, text.replaceAll("_", " "));
  }

  function slugify(text) {
    return text
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 100);
  }

  function csv(value) {
    return (value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function numOrNull(value) {
    if (value === "" || value === null || value === undefined) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `send-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  }

  function toast(message, isError = false) {
    const box = document.getElementById("toasts");
    const item = h("div", { class: `toast${isError ? " error" : ""}` }, message);
    box.append(item);
    setTimeout(() => item.remove(), isError ? 7000 : 3500);
  }

  // ---------------------------------------------------------------------------------------
  // API

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  function describeError(data, status) {
    if (data && typeof data === "object" && "detail" in data) {
      const detail = data.detail;
      if (typeof detail === "string") return FRIENDLY_ERRORS[detail] || detail;
      if (Array.isArray(detail)) {
        return detail
          .map((item) => {
            const where = (item.loc || []).filter((part) => part !== "body").join(".");
            return where ? `${where}: ${item.msg}` : item.msg;
          })
          .join("; ");
      }
    }
    if (status === 403) return "You do not have permission for this action.";
    if (status >= 500) return "The server hit an error. Please try again.";
    return `Request failed (${status}).`;
  }

  async function api(method, path, body) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (response.status === 401 && state.token && !path.endsWith("/auth/login")) {
      logout("Your session expired. Please sign in again.");
      throw new ApiError(401, "Session expired");
    }
    if (!response.ok) throw new ApiError(response.status, describeError(data, response.status));
    return data;
  }

  const orgPath = (path) => `/api/v1/organizations/${encodeURIComponent(state.orgId)}${path}`;
  const get = (path) => api("GET", orgPath(path));
  const post = (path, body = {}) => api("POST", orgPath(path), body);
  const put = (path, body) => api("PUT", orgPath(path), body);
  const patch = (path, body) => api("PATCH", orgPath(path), body);

  async function soft(promise, fallback = null) {
    try {
      return await promise;
    } catch (error) {
      if (error.status === 401) throw error;
      return fallback;
    }
  }

  async function run(button, action, successMessage) {
    const buttons = button ? [button] : [];
    buttons.forEach((b) => (b.disabled = true));
    try {
      const result = await action();
      if (successMessage) toast(successMessage);
      return result;
    } catch (error) {
      if (error.status !== 401) toast(error.message, true);
      return undefined;
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function products(force = false) {
    if (!state.productCache || force) state.productCache = await get("/products");
    return state.productCache;
  }

  // ---------------------------------------------------------------------------------------
  // Modal

  function modal(title, bodyNodes, { submitLabel = "Save", onSubmit, danger = false } = {}) {
    const error = h("div", { class: "error-text", hidden: true });
    const submit = h("button", { class: `btn ${danger ? "danger" : "primary"}`, type: "submit" }, submitLabel);
    const close = () => backdrop.remove();
    const form = h(
      "form",
      {
        class: "modal",
        onsubmit: async (event) => {
          event.preventDefault();
          submit.disabled = true;
          error.hidden = true;
          try {
            await onSubmit();
            close();
          } catch (err) {
            if (err.status === 401) {
              close();
              return;
            }
            error.textContent = err.message;
            error.hidden = false;
          } finally {
            submit.disabled = false;
          }
        },
      },
      h("h2", null, title),
      error,
      bodyNodes,
      h(
        "div",
        { class: "actions" },
        h("button", { class: "btn", type: "button", onclick: close }, "Cancel"),
        onSubmit ? submit : null,
      ),
    );
    const backdrop = h(
      "div",
      {
        class: "modal-backdrop",
        onclick: (event) => {
          if (event.target === backdrop) close();
        },
      },
      form,
    );
    document.body.append(backdrop);
    const first = form.querySelector("input, select, textarea");
    if (first) first.focus();
    return { close };
  }

  function field(label, input, hint) {
    return h("label", { class: "field" }, h("span", null, label), input, hint ? h("span", { class: "hint" }, hint) : null);
  }

  function input(props = {}) {
    return h("input", { type: "text", ...props });
  }

  function select(options, props = {}) {
    return h(
      "select",
      props,
      options.map((opt) =>
        typeof opt === "string"
          ? h("option", { value: opt, selected: props.value === opt }, opt)
          : h("option", { value: opt.value, selected: props.value === opt.value }, opt.label),
      ),
    );
  }

  function table(columns, rows, { onRowClick, empty = "Nothing here yet.", selectedId } = {}) {
    if (!rows || rows.length === 0) return h("div", { class: "empty" }, empty);
    return h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        null,
        h("thead", null, h("tr", null, columns.map((col) => h("th", null, col.label)))),
        h(
          "tbody",
          null,
          rows.map((row) =>
            h(
              "tr",
              {
                class: [onRowClick ? "clickable" : "", selectedId && row.id === selectedId ? "selected" : ""]
                  .filter(Boolean)
                  .join(" "),
                onclick: onRowClick
                  ? (event) => {
                      if (event.target.closest("button, a, input, select")) return;
                      onRowClick(row);
                    }
                  : null,
              },
              columns.map((col) => h("td", null, col.render ? col.render(row) : fmtValue(row[col.key]))),
            ),
          ),
        ),
      ),
    );
  }

  function genericTable(items, empty) {
    if (!items || items.length === 0) return h("div", { class: "empty" }, empty || "No data yet.");
    const keys = Object.keys(items[0]).filter((key) => !key.endsWith("_id") && key !== "organization_id");
    return table(
      keys.map((key) => ({
        label: key.replaceAll("_", " "),
        key,
        render: (row) => (key.endsWith("rate") ? fmtPct(row[key]) : fmtValue(row[key])),
      })),
      items,
    );
  }

  function jsonDetails(label, value) {
    if (value === null || value === undefined) return null;
    return h("details", null, h("summary", null, label), h("pre", { class: "json" }, JSON.stringify(value, null, 2)));
  }

  function kv(pairs) {
    return h(
      "dl",
      { class: "kv" },
      pairs.filter(Boolean).map(([key, value]) => [h("dt", null, key), h("dd", null, value instanceof Node ? value : fmtValue(value))]),
    );
  }

  // ---------------------------------------------------------------------------------------
  // Auth & shell

  function logout(message) {
    state.token = null;
    state.me = null;
    state.productCache = null;
    safeSet(TOKEN_KEY, null);
    renderLogin(message);
  }

  function renderLogin(message) {
    const root = clear(document.getElementById("app"));
    const error = h("div", { class: "error-text", hidden: !message }, message || "");
    const email = input({ type: "email", name: "email", autocomplete: "username", required: true });
    const password = input({ type: "password", name: "password", autocomplete: "current-password", required: true, minlength: 8 });
    const submit = h("button", { class: "btn primary", type: "submit" }, "Sign in");
    root.append(
      h(
        "div",
        { class: "login-wrap" },
        h(
          "form",
          {
            class: "card login-card",
            onsubmit: async (event) => {
              event.preventDefault();
              submit.disabled = true;
              error.hidden = true;
              try {
                const result = await api("POST", "/api/v1/auth/login", {
                  email: email.value.trim(),
                  password: password.value,
                });
                state.token = result.access_token;
                safeSet(TOKEN_KEY, state.token);
                await boot();
              } catch (err) {
                error.textContent = err.status === 401 ? "Incorrect email or password." : err.message;
                error.hidden = false;
              } finally {
                submit.disabled = false;
              }
            },
          },
          h("div", { class: "brand" }, h("img", { src: "/static/favicon.svg", alt: "" }), "MarketingIQ"),
          h("p", { class: "muted" }, "B2B marketing intelligence. Sign in to continue."),
          error,
          field("Email", email),
          field("Password", password),
          submit,
        ),
      ),
    );
    email.focus();
  }

  async function boot() {
    if (!state.token) {
      renderLogin();
      return;
    }
    try {
      state.me = await api("GET", "/api/v1/me");
    } catch (error) {
      if (error.status !== 401) renderLogin(error.message);
      return;
    }
    const memberships = state.me.memberships || [];
    if (memberships.length === 0) {
      const root = clear(document.getElementById("app"));
      root.append(
        h(
          "div",
          { class: "login-wrap" },
          h(
            "div",
            { class: "card login-card" },
            h("h2", null, "No organization access"),
            h("p", null, "Your account is not a member of any organization yet. Ask an administrator to add you."),
            h("button", { class: "btn", onclick: () => logout() }, "Sign out"),
          ),
        ),
      );
      return;
    }
    if (!memberships.some((m) => m.organization_id === state.orgId)) {
      state.orgId = memberships[0].organization_id;
      safeSet(ORG_KEY, state.orgId);
    }
    if (!location.hash || location.hash === "#" || location.hash === "#/") location.hash = "#/dashboard";
    else render();
  }

  function currentMembership() {
    return (state.me?.memberships || []).find((m) => m.organization_id === state.orgId);
  }

  function shell(active, content) {
    const membership = currentMembership();
    const memberships = state.me.memberships || [];
    const link = (href, label, key) => h("a", { href, class: active === key ? "active" : "" }, label);
    const orgSwitch =
      memberships.length > 1
        ? select(
            memberships.map((m) => ({ value: m.organization_id, label: m.organization_name || m.organization_id })),
            {
              value: state.orgId,
              onchange: (event) => {
                state.orgId = event.target.value;
                state.productCache = null;
                safeSet(ORG_KEY, state.orgId);
                location.hash = "#/dashboard";
                render();
              },
            },
          )
        : null;
    return h(
      "div",
      { class: "shell" },
      h(
        "aside",
        { class: "sidebar" },
        h("div", { class: "brand" }, h("img", { src: "/static/favicon.svg", alt: "" }), "MarketingIQ"),
        h(
          "nav",
          { class: "nav" },
          link("#/dashboard", "Dashboard", "dashboard"),
          link("#/products", "Products & ICPs", "products"),
          link("#/companies", "Companies", "companies"),
          link("#/automation", "Automation", "automation"),
          link("#/insights", "Insights", "insights"),
          link("#/suppressions", "Suppression list", "suppressions"),
        ),
        h("div", { class: "spacer" }),
        orgSwitch,
        h(
          "div",
          { class: "who" },
          h("div", null, membership?.organization_name || "Organization"),
          h("div", null, state.me.email),
          h("div", null, (membership?.role || "").replaceAll("_", " ").toLowerCase()),
          h("a", { href: "#", onclick: (e) => (e.preventDefault(), logout()) }, "Sign out"),
        ),
      ),
      h("main", { class: "main" }, content),
    );
  }

  function pageHead(title, sub, actions, crumbs) {
    return h(
      "div",
      { class: "page-head" },
      h("div", null, crumbs ? h("div", { class: "crumbs" }, crumbs) : null, h("h1", null, title), sub ? h("div", { class: "sub" }, sub) : null),
      actions ? h("div", { class: "row" }, actions) : null,
    );
  }

  // ---------------------------------------------------------------------------------------
  // Router

  const routes = [
    [/^#\/dashboard$/, "dashboard", () => viewDashboard()],
    [/^#\/products$/, "products", () => viewProducts()],
    [/^#\/products\/([^/]+)$/, "products", (m) => viewProduct(m[1])],
    [/^#\/companies$/, "companies", () => viewCompanies()],
    [/^#\/companies\/([^/]+)(?:\/([a-z]+))?$/, "companies", (m) => viewCompany(m[1], m[2] || "overview")],
    [/^#\/automation$/, "automation", () => viewAutomation()],
    [/^#\/insights$/, "insights", () => viewInsights()],
    [/^#\/suppressions$/, "suppressions", () => viewSuppressions()],
  ];

  async function render() {
    if (!state.me) return;
    const id = ++state.renderId;
    const hash = location.hash || "#/dashboard";
    const match = routes.map(([re, key, fn]) => [hash.match(re), key, fn]).find(([m]) => m);
    const [m, key, fn] = match || [null, "dashboard", () => viewDashboard()];
    const root = clear(document.getElementById("app"));
    root.append(shell(key, h("div", { class: "boot" }, "Loading…")));
    let content;
    try {
      content = await fn(m);
    } catch (error) {
      if (error.status === 401) return;
      content = h("div", { class: "card" }, h("h2", null, "Something went wrong"), h("p", null, error.message));
    }
    if (id !== state.renderId) return;
    clear(root).append(shell(key, content));
  }

  const refresh = () => render();

  // ---------------------------------------------------------------------------------------
  // Dashboard

  async function viewDashboard() {
    const [productList, companies, research, outbound, ai, summary] = await Promise.all([
      products(true),
      get("/companies"),
      soft(get("/providers/status"), []),
      soft(get("/outbound/providers/status"), []),
      soft(get("/campaign-ai/status"), null),
      soft(get("/learning/conversion-summary"), null),
    ]);
    const activeProducts = productList.filter((p) => p.status === "ACTIVE");
    const counts = summary?.counts || {};
    const stat = (label, value) => h("div", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));

    const hasIcp = activeProducts.length > 0 && (await Promise.all(activeProducts.map((p) => soft(get(`/products/${p.id}/icps`), [])))).some((list) => list.some((icp) => icp.is_active));
    const steps = [
      [activeProducts.length > 0, "Add a product and activate it", "Describe what you sell, the value proposition and the buyer roles you target.", "#/products"],
      [hasIcp, "Define and activate an ICP", "Your ideal customer profile: industries, countries, company size and signals.", "#/products"],
      [companies.length > 0, "Add target companies", "Add companies one by one or import a CSV list.", "#/companies"],
      [(counts.total ?? summary?.sample_size ?? 0) > 0, "Run the workflow and send your first approved email", "Research → fit → qualify → contacts → draft → approve → send.", "#/companies"],
    ];

    const integration = (label, ok, detail) =>
      h("tr", null, h("td", null, label), h("td", null, badge(ok ? "available" : "not_configured")), h("td", { class: "muted small" }, detail || ""));

    return h(
      "div",
      null,
      pageHead("Dashboard", currentMembership()?.organization_name || ""),
      h(
        "div",
        { class: "grid cols-4" },
        stat("Active products", `${activeProducts.length} / ${productList.length}`),
        stat("Companies", companies.length),
        stat("Opportunities", summary?.sample_size ?? 0),
        stat("Won", counts.won ?? counts.WON ?? 0),
      ),
      h(
        "div",
        { class: "grid cols-2" },
        h(
          "div",
          { class: "card" },
          h("h2", null, "Getting started"),
          h(
            "ol",
            { class: "steps" },
            steps.map(([done, title, text, href], index) =>
              h(
                "li",
                null,
                h("div", { class: `num ${done ? "done" : ""}` }, done ? "✓" : index + 1),
                h("div", null, h("a", { href }, h("strong", null, title)), h("div", { class: "muted small" }, text)),
              ),
            ),
          ),
        ),
        h(
          "div",
          { class: "card" },
          h("h2", null, "Integrations"),
          h(
            "table",
            null,
            h(
              "tbody",
              null,
              research.map((p) =>
                integration(
                  `Research: ${p.provider_key}`,
                  p.credentials_configured,
                  p.costs_credits ? "Uses provider credits" : "Free public web research",
                ),
              ),
              outbound.map((p) => integration(`Email sending: ${p.provider_key}`, p.configured, "Outbound SMTP")),
              ai ? integration("AI campaign drafting", ai.configured, ai.model ? `Model ${ai.model}` : "") : null,
            ),
          ),
          h("p", { class: "muted small" }, "Integrations are configured through the hosting environment variables."),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------------------------
  // Products & ICPs

  function criteriaEditor(initial = []) {
    const list = h("div");
    const datalistId = "criteria-kinds";
    if (!document.getElementById(datalistId)) {
      document.body.append(h("datalist", { id: datalistId }, CRITERION_KINDS.map((k) => h("option", { value: k }))));
    }
    const addRow = (item = { kind: "industry", value: "", weight: 3, required: false }) => {
      const kind = input({ list: datalistId, value: item.kind, placeholder: "kind", "aria-label": "Criterion kind" });
      const value = input({ value: item.value, placeholder: "value, e.g. Logistics", "aria-label": "Criterion value" });
      const weight = input({ type: "number", min: 1, max: 100, value: item.weight ?? 3, "aria-label": "Weight" });
      const required = h("input", { type: "checkbox", checked: item.required, "aria-label": "Required" });
      const row = h(
        "div",
        { class: "criteria-row" },
        kind,
        value,
        weight,
        h("label", { class: "row small" }, required, "Required"),
        h("button", { class: "btn small", type: "button", title: "Remove", onclick: () => row.remove() }, "×"),
      );
      row._read = () => ({
        kind: kind.value.trim(),
        value: value.value.trim(),
        weight: Number(weight.value) || 3,
        required: required.checked,
      });
      list.append(row);
    };
    initial.forEach((item) => addRow(item));
    const node = h(
      "div",
      { class: "field" },
      h("span", { class: "small muted" }, "Kind · value · weight (1–100) · required"),
      list,
      h("button", { class: "btn small", type: "button", onclick: () => addRow() }, "+ Add criterion"),
    );
    return {
      node,
      value: () => [...list.children].map((row) => row._read()).filter((c) => c.kind && c.value),
    };
  }

  function openProductForm(product, onSaved) {
    const name = input({ value: product?.name || "", required: true, maxlength: 200 });
    const slug = input({ value: product?.slug || "", required: true, pattern: "[a-z0-9]+(-[a-z0-9]+)*", maxlength: 100 });
    if (!product) name.addEventListener("input", () => (slug.value = slugify(name.value)));
    const description = h("textarea", { maxlength: 10000 }, product?.description || "");
    const value = h("textarea", { maxlength: 10000 }, product?.value_proposition || "");
    const min = input({ type: "number", min: 0, value: product?.employee_min ?? "" });
    const max = input({ type: "number", min: 0, value: product?.employee_max ?? "" });
    const primary = input({ value: (product?.primary_buyer_roles || []).join(", ") });
    const secondary = input({ value: (product?.secondary_buyer_roles || []).join(", ") });
    const criteria = criteriaEditor(product?.criteria || []);
    modal(
      product ? `Edit ${product.name}` : "New product",
      [
        h("div", { class: "form-grid" }, field("Name", name), field("Slug", slug, "Lowercase letters, numbers and hyphens")),
        field("Description", description),
        field("Value proposition", value, "Required for campaign drafting: the core benefit for the buyer."),
        h("div", { class: "form-grid" }, field("Min employees", min), field("Max employees", max)),
        field("Primary buyer roles", primary, "Comma separated, e.g. CIO, IT Director, HR Director"),
        field("Secondary buyer roles", secondary, "Comma separated"),
        h("h3", null, "Product criteria"),
        criteria.node,
      ],
      {
        submitLabel: product ? "Save product" : "Create product",
        onSubmit: async () => {
          const body = {
            name: name.value.trim(),
            slug: slug.value.trim(),
            description: description.value.trim() || null,
            value_proposition: value.value.trim() || null,
            employee_min: numOrNull(min.value),
            employee_max: numOrNull(max.value),
            primary_buyer_roles: csv(primary.value),
            secondary_buyer_roles: csv(secondary.value),
            criteria: criteria.value(),
          };
          const saved = product ? await put(`/products/${product.id}`, body) : await post("/products", body);
          state.productCache = null;
          toast(product ? "Product saved" : "Product created");
          onSaved(saved);
        },
      },
    );
  }

  function openIcpForm(productId, icp, onSaved) {
    const name = input({ value: icp?.name || "", required: true, maxlength: 200 });
    const min = input({ type: "number", min: 0, value: icp?.employee_min ?? "" });
    const max = input({ type: "number", min: 0, value: icp?.employee_max ?? "" });
    const criteria = criteriaEditor(
      icp?.criteria || [
        { kind: "industry", value: "", weight: 5, required: false },
        { kind: "country", value: "", weight: 3, required: false },
      ],
    );
    modal(
      icp ? `Revise ICP: ${icp.name}` : "New ideal customer profile",
      [
        icp ? h("p", { class: "muted" }, "Saving creates a new ICP version. Earlier assessments keep the version they used.") : null,
        field("Name", name),
        h("div", { class: "form-grid" }, field("Min employees", min), field("Max employees", max)),
        h("h3", null, "Targeting criteria"),
        criteria.node,
      ],
      {
        submitLabel: icp ? "Save new version" : "Create ICP",
        onSubmit: async () => {
          const body = {
            name: name.value.trim(),
            employee_min: numOrNull(min.value),
            employee_max: numOrNull(max.value),
            criteria: criteria.value(),
          };
          const saved = icp ? await put(`/icps/${icp.id}`, body) : await post(`/products/${productId}/icps`, body);
          toast(icp ? "ICP saved" : "ICP created");
          onSaved(saved);
        },
      },
    );
  }

  async function viewProducts() {
    const list = await products(true);
    return h(
      "div",
      null,
      pageHead(
        "Products & ICPs",
        "What you sell and who you sell it to.",
        h("button", { class: "btn primary", onclick: () => openProductForm(null, (p) => (location.hash = `#/products/${p.id}`)) }, "+ New product"),
      ),
      h(
        "div",
        { class: "card" },
        table(
          [
            { label: "Product", render: (p) => h("strong", null, p.name) },
            { label: "Status", render: (p) => badge(p.status) },
            { label: "Primary buyer roles", render: (p) => fmtValue(p.primary_buyer_roles) },
            { label: "Criteria", render: (p) => (p.criteria || []).length },
            { label: "Updated", render: (p) => fmtDate(p.updated_at) },
          ],
          list,
          { onRowClick: (p) => (location.hash = `#/products/${p.id}`), empty: "No products yet. Create your first product." },
        ),
      ),
    );
  }

  async function viewProduct(productId) {
    const [product, icps, personas] = await Promise.all([
      get(`/products/${productId}`),
      get(`/products/${productId}/icps`),
      soft(get(`/products/${productId}/personas`), null),
    ]);
    const active = product.status === "ACTIVE";
    return h(
      "div",
      null,
      pageHead(
        product.name,
        h("span", null, badge(product.status), " ", h("span", { class: "mono" }, product.slug)),
        [
          h("button", { class: "btn", onclick: () => openProductForm(product, refresh) }, "Edit"),
          h(
            "button",
            {
              class: active ? "btn danger" : "btn primary",
              onclick: (e) =>
                run(e.currentTarget, async () => {
                  await patch(`/products/${product.id}/activation`, { active: !active });
                  state.productCache = null;
                  refresh();
                }, active ? "Product deactivated" : "Product activated"),
            },
            active ? "Deactivate" : "Activate",
          ),
        ],
        h("a", { href: "#/products" }, "Products"),
      ),
      h(
        "div",
        { class: "grid cols-2" },
        h(
          "div",
          { class: "card" },
          h("h2", null, "Positioning"),
          kv([
            ["Description", product.description],
            ["Value proposition", product.value_proposition],
            ["Employee range", `${product.employee_min ?? "any"} – ${product.employee_max ?? "any"}`],
            ["Primary buyer roles", product.primary_buyer_roles],
            ["Secondary buyer roles", product.secondary_buyer_roles],
          ]),
        ),
        h(
          "div",
          { class: "card" },
          h("h2", null, "Product criteria"),
          table(
            [
              { label: "Kind", key: "kind" },
              { label: "Value", key: "value" },
              { label: "Weight", key: "weight" },
              { label: "Required", render: (c) => (c.required ? "Yes" : "No") },
            ],
            product.criteria,
            { empty: "No product criteria." },
          ),
        ),
      ),
      h(
        "div",
        { class: "card" },
        h(
          "div",
          { class: "card-head" },
          h("h2", null, "Ideal customer profiles"),
          h("button", { class: "btn primary", onclick: () => openIcpForm(product.id, null, refresh) }, "+ New ICP"),
        ),
        h("p", { class: "muted small" }, "Fit assessments use the active ICP. Activate one ICP per product."),
        table(
          [
            { label: "Name", render: (i) => h("strong", null, i.name) },
            { label: "Version", key: "version" },
            { label: "Active", render: (i) => badge(i.is_active ? "ACTIVE" : "DRAFT") },
            { label: "Criteria", render: (i) => (i.criteria || []).map((c) => `${c.kind}: ${c.value}`).join(" · ") || "—" },
            {
              label: "",
              render: (i) =>
                h(
                  "div",
                  { class: "row" },
                  h("button", { class: "btn small", onclick: () => openIcpForm(product.id, i, refresh) }, "Revise"),
                  h(
                    "button",
                    {
                      class: "btn small",
                      onclick: (e) =>
                        run(e.currentTarget, async () => {
                          await patch(`/icps/${i.id}/activation`, { active: !i.is_active });
                          refresh();
                        }, i.is_active ? "ICP deactivated" : "ICP activated"),
                    },
                    i.is_active ? "Deactivate" : "Activate",
                  ),
                ),
            },
          ],
          icps,
          { empty: "No ICPs yet. Create one to start assessing companies." },
        ),
      ),
      personas ? personaCard(product, personas) : null,
    );
  }

  // Suggested starting points only. The discovery engine never assumes these roles; they are
  // saved as ordinary, editable personas on the product.
  const PERSONA_TEMPLATES = [
    { name: "Executive leadership", titles: ["CEO", "Managing Director", "General Manager", "Founder", "Owner"], departments: ["Executive", "Management"], seniorities: ["C-level"], max_contacts: 1 },
    { name: "IT leadership", titles: ["CIO", "CTO", "IT Director", "Head of IT", "IT Manager", "Head of Technology"], departments: ["IT", "Technology", "Digital"], seniorities: ["C-level", "Director", "Head"], max_contacts: 2 },
    { name: "HR leadership", titles: ["CHRO", "HR Director", "Head of HR", "Head of People", "Chief People Officer", "HR Manager"], departments: ["HR", "People"], seniorities: ["C-level", "Director", "Head"], max_contacts: 2 },
    { name: "Communications", titles: ["Head of Communications", "Communications Director", "Corporate Communications Manager", "PR Manager"], departments: ["Communications", "PR"], seniorities: ["Director", "Head", "Manager"], max_contacts: 1 },
    { name: "Marketing", titles: ["CMO", "Marketing Director", "Head of Marketing", "Brand Manager"], departments: ["Marketing"], seniorities: ["C-level", "Director", "Head"], max_contacts: 1 },
  ];

  function personaCard(product, data) {
    const rows = data.personas.length ? data.personas : data.effective;
    return h(
      "div",
      { class: "card" },
      h(
        "div",
        { class: "card-head" },
        h("h2", null, "Target personas"),
        h("button", { class: "btn primary", onclick: () => openPersonaEditor(product, data.personas) }, "Edit personas"),
      ),
      h(
        "p",
        { class: "muted small" },
        data.derived_from_buyer_roles
          ? "No personas saved yet, so decision-maker discovery uses the product's buyer roles below. Add personas for better targeting."
          : "Decision-maker discovery looks for these people at qualified companies, best 1–3 per persona.",
      ),
      table(
        [
          { label: "Persona", render: (p) => h("strong", null, p.name) },
          { label: "Titles", render: (p) => (p.titles || []).join(", ") || "—" },
          { label: "Departments", render: (p) => (p.departments || []).join(", ") || "—" },
          { label: "Seniority", render: (p) => (p.seniorities || []).join(", ") || "—" },
          { label: "Max people", key: "max_contacts" },
          { label: "Active", render: (p) => (p.is_active === false ? badge("PAUSED") : badge("ACTIVE")) },
        ],
        rows,
        { empty: "No personas and no buyer roles. Add personas to find decision-makers." },
      ),
    );
  }

  function openPersonaEditor(product, existing) {
    const list = h("div", { class: "persona-list" });
    const rows = [];
    const addRow = (p = {}) => {
      const r = {
        id: p.id || null,
        name: input({ value: p.name || "", maxlength: 120, placeholder: "e.g. IT leadership" }),
        titles: input({ value: (p.titles || []).join(", "), placeholder: "CIO, IT Director, Head of IT" }),
        departments: input({ value: (p.departments || []).join(", "), placeholder: "IT, Technology" }),
        seniorities: input({ value: (p.seniorities || []).join(", "), placeholder: "C-level, Director, Head" }),
        max: select(["1", "2", "3"], { value: String(p.max_contacts || 2) }),
        active: h("input", { type: "checkbox", checked: p.is_active !== false }),
      };
      const box = h(
        "fieldset",
        { class: "persona-row" },
        h("div", { class: "grid cols-2" }, field("Persona name", r.name), field("Job titles (comma separated)", r.titles)),
        h(
          "div",
          { class: "grid cols-3" },
          field("Departments", r.departments),
          field("Seniority", r.seniorities),
          field("Max people", r.max),
        ),
        h(
          "div",
          { class: "row" },
          h("label", { class: "check" }, r.active, "Active"),
          h(
            "button",
            {
              class: "btn small danger",
              type: "button",
              onclick: () => {
                rows.splice(rows.indexOf(r), 1);
                box.remove();
              },
            },
            "Remove",
          ),
        ),
      );
      rows.push(r);
      list.append(box);
    };
    (existing.length ? existing : []).forEach(addRow);
    const template = select(
      [{ value: "", label: "Add from a suggested template…" }, ...PERSONA_TEMPLATES.map((t) => ({ value: t.name, label: t.name }))],
      {
        onchange: (event) => {
          const t = PERSONA_TEMPLATES.find((x) => x.name === event.target.value);
          if (t) addRow(t);
          event.target.value = "";
        },
      },
    );
    modal(
      `Target personas · ${product.name}`,
      [
        h("p", { class: "muted small" }, "Personas describe who to reach. Templates are only suggestions; edit them freely. Up to 12 personas."),
        list,
        h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => addRow() }, "+ Blank persona"), template),
      ],
      {
        submitLabel: "Save personas",
        onSubmit: async () => {
          const personas = rows
            .filter((r) => r.name.value.trim())
            .map((r, index) => ({
              id: r.id,
              name: r.name.value.trim(),
              titles: csv(r.titles.value),
              departments: csv(r.departments.value),
              seniorities: csv(r.seniorities.value),
              max_contacts: Number(r.max.value) || 2,
              priority: index + 1,
              is_active: r.active.checked,
            }));
          await put(`/products/${product.id}/personas`, { personas });
          toast("Personas saved");
          refresh();
        },
      },
    );
  }

  // ---------------------------------------------------------------------------------------
  // Companies

  function openCompanyForm(onSaved) {
    const f = {
      domain: input({ required: true, placeholder: "example.com" }),
      canonical_name: input({ required: true }),
      website_url: input({ placeholder: "https://example.com" }),
      country_code: input({ maxlength: 2, placeholder: "SA" }),
      industry: input(),
      employee_min: input({ type: "number", min: 0 }),
      employee_max: input({ type: "number", min: 0 }),
      description: h("textarea"),
      lifecycle_status: input({ placeholder: "e.g. target, prospect, customer" }),
      private_notes: h("textarea"),
    };
    modal(
      "Add company",
      [
        h("div", { class: "form-grid" }, field("Domain", f.domain), field("Company name", f.canonical_name)),
        h("div", { class: "form-grid" }, field("Website", f.website_url), field("Country code", f.country_code, "ISO 2-letter code")),
        h("div", { class: "form-grid" }, field("Industry", f.industry), field("Lifecycle status", f.lifecycle_status)),
        h("div", { class: "form-grid" }, field("Min employees", f.employee_min), field("Max employees", f.employee_max)),
        field("Description", f.description),
        field("Private notes", f.private_notes, "Visible only to your organization"),
      ],
      {
        submitLabel: "Add company",
        onSubmit: async () => {
          const body = {};
          for (const [key, el] of Object.entries(f)) {
            const raw = el.value.trim();
            if (key.startsWith("employee_")) body[key] = numOrNull(raw);
            else if (key === "country_code") body[key] = raw ? raw.toUpperCase() : null;
            else body[key] = raw || null;
          }
          const saved = await post("/companies", body);
          toast("Company added");
          onSaved(saved);
        },
      },
    );
  }

  function openImport() {
    const file = h("input", { type: "file", accept: ".csv,text/csv" });
    const text = h("textarea", { rows: 8, placeholder: "company_name,domain,country_code,industry\nAcme Logistics,acme.example,SA,Logistics" });
    file.addEventListener("change", async () => {
      if (file.files[0]) text.value = await file.files[0].text();
    });
    const report = h("div");
    let previewed = false;
    const previewButton = h(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: (e) =>
          run(e.currentTarget, async () => {
            const result = await post("/company-imports/preview", { content: text.value });
            previewed = true;
            clear(report).append(importReport(result, true));
          }),
      },
      "Preview",
    );
    modal(
      "Import companies from CSV",
      [
        h(
          "p",
          { class: "muted small" },
          "Required columns: company_name, domain. Optional: country_code, industry, employee_min, employee_max, website_url, lifecycle_status, private_notes. Up to 1,000 rows.",
        ),
        field("CSV file", file),
        field("…or paste CSV", text),
        h("div", { class: "row" }, previewButton),
        report,
      ],
      {
        submitLabel: "Import",
        onSubmit: async () => {
          if (!previewed) {
            const result = await post("/company-imports/preview", { content: text.value });
            previewed = true;
            clear(report).append(importReport(result, true));
            throw new ApiError(0, "Check the preview, then click Import again.");
          }
          const result = await post("/company-imports", { content: text.value });
          toast(`Imported: ${result.new_tenant_relationships} new, ${result.already_attached} already present`);
          refresh();
        },
      },
    );
  }

  function importReport(result, isPreview) {
    return h(
      "div",
      { class: "stack" },
      h("h3", null, isPreview ? "Preview" : "Result"),
      kv([
        ["Rows", result.total_rows],
        ["Valid / invalid", `${result.valid_rows} / ${result.invalid_rows}`],
        ["New companies", result.new_companies],
        ["Already in your list", result.already_attached],
      ]),
      (result.warnings || []).length ? h("div", { class: "muted small" }, result.warnings.join(" · ")) : null,
      table(
        [
          { label: "Row", key: "row_number" },
          { label: "Company", key: "company_name" },
          { label: "Domain", key: "domain" },
          { label: "Action", render: (r) => badge(r.action) },
          { label: "Issues", render: (r) => [...(r.errors || []), ...(r.warnings || [])].join("; ") || "—" },
        ],
        (result.rows || []).slice(0, 200),
      ),
    );
  }

  async function viewCompanies() {
    const list = await get("/companies");
    const filter = input({ type: "search", placeholder: "Filter by name, domain, industry or country…" });
    const holder = h("div");
    const draw = () => {
      const q = filter.value.trim().toLowerCase();
      const rows = q
        ? list.filter((c) => [c.canonical_name, c.domain, c.industry, c.country_code, c.lifecycle_status].some((v) => (v || "").toLowerCase().includes(q)))
        : list;
      clear(holder).append(
        table(
          [
            { label: "Company", render: (c) => h("strong", null, c.canonical_name) },
            { label: "Domain", key: "domain" },
            { label: "Industry", key: "industry" },
            { label: "Country", key: "country_code" },
            { label: "Employees", render: (c) => fmtValue({ min: c.employee_min, max: c.employee_max }) },
            { label: "Lifecycle", render: (c) => (c.lifecycle_status ? badge(c.lifecycle_status) : "—") },
          ],
          rows,
          { onRowClick: (c) => (location.hash = `#/companies/${c.id}`), empty: list.length ? "No companies match the filter." : "No companies yet. Add one or import a CSV." },
        ),
      );
    };
    filter.addEventListener("input", draw);
    draw();
    return h(
      "div",
      null,
      pageHead("Companies", `${list.length} target companies`, [
        h("button", { class: "btn", onclick: openImport }, "Import CSV"),
        h("button", { class: "btn primary", onclick: () => openCompanyForm((c) => (location.hash = `#/companies/${c.id}`)) }, "+ Add company"),
      ]),
      h("div", { class: "card" }, h("div", { class: "field" }, filter), holder),
    );
  }

  const COMPANY_TABS = [
    ["overview", "Overview & workflow"],
    ["research", "Research"],
    ["fit", "Fit & qualification"],
    ["contacts", "Decision makers"],
    ["campaigns", "Campaigns"],
    ["pipeline", "Pipeline"],
  ];

  async function viewCompany(id, tab) {
    const [company, productList] = await Promise.all([get(`/companies/${id}`), products()]);
    const base = `/companies/${id}`;
    const ctx = { id, base, company, productList, productName: (pid) => productList.find((p) => p.id === pid)?.name || "Unknown product" };
    const tabView = {
      overview: companyOverview,
      research: companyResearch,
      fit: companyFit,
      contacts: companyContacts,
      campaigns: companyCampaigns,
      pipeline: companyPipeline,
    }[tab] || companyOverview;
    const body = await tabView(ctx);
    return h(
      "div",
      null,
      pageHead(
        company.canonical_name,
        h(
          "span",
          { class: "row" },
          company.domain ? h("a", { href: company.website_url || `https://${company.domain}`, target: "_blank", rel: "noopener noreferrer" }, company.domain) : null,
          company.industry ? h("span", { class: "muted" }, company.industry) : null,
          company.country_code ? h("span", { class: "muted" }, company.country_code) : null,
          company.lifecycle_status ? badge(company.lifecycle_status) : null,
        ),
        null,
        h("a", { href: "#/companies" }, "Companies"),
      ),
      h("nav", { class: "tabs" }, COMPANY_TABS.map(([key, label]) => h("a", { href: `#/companies/${id}/${key}`, class: key === tab ? "active" : "" }, label))),
      body,
    );
  }

  function productSelect(productList, { onlyActive = true, value } = {}) {
    const options = productList.filter((p) => !onlyActive || p.status === "ACTIVE").map((p) => ({ value: p.id, label: p.name }));
    return { el: select(options, { value: value || options[0]?.value }), empty: options.length === 0 };
  }

  async function companyOverview(ctx) {
    const { company, productList, base } = ctx;
    const picker = productSelect(productList);
    const credits = h("input", { type: "checkbox" });
    const output = h("div");

    const drawPlan = (plan, result) => {
      append(clear(output), [
        result ? h("div", { class: "card" }, h("strong", null, STOP_REASONS[result.stop_reason] || result.stop_reason), result.executed_steps?.length ? h("div", { class: "muted small" }, `Ran: ${result.executed_steps.map((s) => s.step || s).join(" → ")}`) : null) : null,
        h(
          "div",
          { class: "row" },
          h("span", null, "State: "),
          badge(plan.state),
          plan.qualification_status ? [h("span", null, " Qualification: "), badge(plan.qualification_status)] : null,
          h(
            "span",
            { class: "muted" },
            plan.contact_mode === "DECISION_MAKERS"
              ? ` Ready decision-makers: ${plan.contact_count}`
              : ` Contacts: ${plan.contact_count}`,
          ),
        ),
        h(
          "ol",
          { class: "steps" },
          plan.steps.map((step, index) =>
            h(
              "li",
              null,
              h("div", { class: `num ${step.status === "DONE" ? "done" : ""}` }, step.status === "DONE" ? "✓" : index + 1),
              h(
                "div",
                null,
                h("strong", null, step.step.replaceAll("_", " ").toLowerCase()),
                " ",
                badge(step.status),
                step.requires_provider_credits ? h("span", { class: "badge warn" }, "uses credits") : null,
                step.credit_retry_available ? h("span", { class: "badge info" }, "can retry with credits") : null,
                h("div", { class: "muted small" }, step.reason),
              ),
            ),
          ),
        ),
        plan.state === "READY_FOR_HUMAN_CAMPAIGN_REVIEW"
          ? h("a", { class: "btn primary", href: `#/companies/${ctx.id}/campaigns` }, "Draft a campaign →")
          : null,
      ]);
    };

    const lifecycle = input({ value: company.lifecycle_status || "", maxlength: 50 });
    const notes = h("textarea", { maxlength: 10000 }, company.private_notes || "");

    return h(
      "div",
      { class: "grid cols-2" },
      h(
        "div",
        { class: "card" },
        h("h2", null, "Guided workflow"),
        picker.empty
          ? h("div", { class: "empty" }, "Activate a product with an ICP first. ", h("a", { href: "#/products" }, "Go to products"))
          : [
              h("p", { class: "muted small" }, "Runs the safe automatic steps (research, fit, qualification, contact discovery) and stops before anything is sent. Drafting, approval and sending stay manual."),
              field("Product", picker.el),
              h("label", { class: "check" }, credits, "Allow provider credits (Hunter search, email find and verification)"),
              h(
                "div",
                { class: "row" },
                h(
                  "button",
                  {
                    class: "btn",
                    onclick: (e) =>
                      run(e.currentTarget, async () => {
                        const plan = await get(`${base}/automation/plan?product_id=${encodeURIComponent(picker.el.value)}`);
                        drawPlan(plan);
                      }),
                  },
                  "Check plan",
                ),
                h(
                  "button",
                  {
                    class: "btn primary",
                    onclick: (e) =>
                      run(e.currentTarget, async () => {
                        const result = await post(`${base}/automation/run?product_id=${encodeURIComponent(picker.el.value)}`, {
                          allow_provider_credits: credits.checked,
                          contact_provider: "HUNTER",
                          max_contacts: 10,
                          max_steps: 4,
                        });
                        drawPlan(result.plan, result);
                      }, "Workflow advanced"),
                  },
                  "Run safe steps",
                ),
                h(
                  "button",
                  {
                    class: "btn",
                    title: "Re-run these steps automatically every week",
                    onclick: (e) =>
                      run(e.currentTarget, async () => {
                        await post("/automation-policies", {
                          relationship_id: ctx.id,
                          product_id: picker.el.value,
                          cadence_minutes: 10080,
                          allow_provider_credits: credits.checked,
                          contact_provider: "HUNTER",
                          max_contacts: 10,
                          max_steps: 4,
                        });
                      }, "Weekly schedule created. Manage it under Automation."),
                  },
                  "Schedule weekly",
                ),
              ),
              output,
            ],
      ),
      h(
        "div",
        { class: "card" },
        h("h2", null, "Company details"),
        kv([
          ["Domain", company.domain],
          ["Website", company.website_url],
          ["Industry", company.industry],
          ["Country", company.country_code],
          ["Employees", { min: company.employee_min, max: company.employee_max }],
          ["Description", company.description],
        ]),
        h("h3", null, "Your notes"),
        field("Lifecycle status", lifecycle),
        field("Private notes", notes),
        h(
          "button",
          {
            class: "btn primary",
            onclick: (e) =>
              run(e.currentTarget, async () => {
                await patch(base, { lifecycle_status: lifecycle.value.trim() || null, private_notes: notes.value.trim() || null });
              }, "Saved"),
          },
          "Save notes",
        ),
      ),
    );
  }

  async function companyResearch(ctx) {
    const { base } = ctx;
    const [runs, intel] = await Promise.all([get(`${base}/research-runs`), get(`${base}/intelligence`)]);
    const mode = select(
      [
        { value: "PUBLIC_ONLY", label: "Public web only (free)" },
        { value: "PUBLIC_THEN_EXTERNAL", label: "Public web, then external providers" },
        { value: "EXTERNAL_ONLY", label: "External providers only" },
      ],
      { value: "PUBLIC_ONLY" },
    );
    const force = h("input", { type: "checkbox" });
    return h(
      "div",
      null,
      h(
        "div",
        { class: "card" },
        h("h2", null, "Run research"),
        h("div", { class: "form-grid" }, field("Mode", mode), h("label", { class: "check" }, force, "Force refresh (ignore recent results)")),
        h(
          "button",
          {
            class: "btn primary",
            onclick: (e) =>
              run(e.currentTarget, async () => {
                await post(`${base}/research`, { mode: mode.value, providers: [], force_refresh: force.checked });
                refresh();
              }, "Research completed"),
          },
          "Run research",
        ),
      ),
      h(
        "div",
        { class: "card" },
        h("h2", null, "Company intelligence"),
        table(
          [
            { label: "Fact", render: (f) => h("strong", null, f.fact_key.replaceAll("_", " ")) },
            { label: "Value", render: (f) => fmtValue(f.value) },
            { label: "Confidence", render: (f) => fmtValue(f.confidence) },
            { label: "Freshness", render: (f) => badge(f.staleness_status) },
            { label: "Conflict", render: (f) => badge(f.conflict_status) },
            { label: "Review", render: (f) => badge(f.review_status) },
            { label: "Evidence", render: (f) => jsonDetails(`${f.observation_count} observation(s)`, f.evidence_summary) },
          ],
          intel,
          { empty: "No intelligence yet. Run research to collect public evidence." },
        ),
      ),
      h(
        "div",
        { class: "card" },
        h("h2", null, "Research runs"),
        table(
          [
            { label: "Started", render: (r) => fmtDate(r.started_at) },
            { label: "Mode", key: "mode" },
            { label: "Status", render: (r) => badge(r.status) },
            { label: "Providers", render: (r) => `${fmtValue(r.providers_succeeded)} / ${fmtValue(r.providers_attempted)}` },
            { label: "Errors", render: (r) => fmtValue(r.error_summary) },
          ],
          runs,
          { empty: "No research runs yet." },
        ),
      ),
    );
  }

  async function companyFit(ctx) {
    const { base, productList, productName } = ctx;
    const [assessments, qualifications] = await Promise.all([get(`${base}/fit-assessments`), get(`${base}/qualifications`)]);
    const picker = productSelect(productList);
    const icpSelect = select([], {});
    const loadIcps = async () => {
      clear(icpSelect);
      if (!picker.el.value) return;
      const icps = await get(`/products/${picker.el.value}/icps`);
      const active = icps.filter((i) => i.is_active);
      append(icpSelect, [active.map((i) => h("option", { value: i.id }, `${i.name} (v${i.version})`))]);
      if (active.length === 0) icpSelect.append(h("option", { value: "" }, "No active ICP"));
    };
    picker.el.addEventListener("change", loadIcps);
    if (!picker.empty) await loadIcps();

    const qualify = (assessment, button) =>
      run(button, async () => {
        await post(`${base}/qualifications`, { fit_assessment_id: assessment.id });
        refresh();
      }, "Lead qualified");

    return h(
      "div",
      null,
      h(
        "div",
        { class: "card" },
        h("h2", null, "Assess fit"),
        picker.empty
          ? h("div", { class: "empty" }, "No active products. ", h("a", { href: "#/products" }, "Activate a product"))
          : [
              h("div", { class: "form-grid" }, field("Product", picker.el), field("ICP", icpSelect)),
              h(
                "div",
                { class: "row" },
                h(
                  "button",
                  {
                    class: "btn primary",
                    onclick: (e) =>
                      run(e.currentTarget, async () => {
                        if (!icpSelect.value) throw new ApiError(0, "Activate an ICP for this product first.");
                        await post(`${base}/fit-assessments`, { product_id: picker.el.value, icp_id: icpSelect.value });
                        refresh();
                      }, "Fit assessed"),
                  },
                  "Assess fit",
                ),
                h(
                  "button",
                  {
                    class: "btn",
                    onclick: (e) =>
                      run(e.currentTarget, async () => {
                        await post(`${base}/fit-assessments/evaluate-all-products`, { product_id: null });
                        refresh();
                      }, "All active products assessed"),
                  },
                  "Assess all active products",
                ),
              ),
            ],
      ),
      h(
        "div",
        { class: "card" },
        h("h2", null, "Fit assessments"),
        table(
          [
            { label: "Product", render: (a) => productName(a.product_id) },
            { label: "Grade", render: (a) => badge(a.grade) },
            { label: "Score", render: (a) => fmtValue(a.fit_score) },
            { label: "Evidence coverage", render: (a) => fmtPct(a.evidence_coverage) },
            { label: "Status", render: (a) => badge(a.status) },
            { label: "Evaluated", render: (a) => fmtDate(a.evaluated_at) },
            { label: "Explanation", render: (a) => jsonDetails("details", a.explanation) },
            { label: "", render: (a) => h("button", { class: "btn small primary", onclick: (e) => qualify(a, e.currentTarget) }, "Qualify") },
          ],
          assessments,
          { empty: "No assessments yet." },
        ),
      ),
      h(
        "div",
        { class: "card" },
        h("h2", null, "Lead qualifications"),
        table(
          [
            { label: "Product", render: (q) => productName(q.product_id) },
            { label: "Grade", render: (q) => badge(q.qualification_grade) },
            { label: "Score", render: (q) => fmtValue(q.qualification_score) },
            { label: "Status", render: (q) => badge(q.status) },
            { label: "Buyer role", render: (q) => fmtValue(q.recommended_buyer_role) },
            {
              label: "Why",
              render: (q) =>
                h(
                  "details",
                  null,
                  h("summary", null, "reasons"),
                  h("div", { class: "small" }, h("strong", null, "Positive"), h("ul", { class: "reason-list" }, (q.positive_reasons || []).map((r) => h("li", null, fmtValue(r))))),
                  h("div", { class: "small" }, h("strong", null, "Negative"), h("ul", { class: "reason-list" }, (q.negative_reasons || []).map((r) => h("li", null, fmtValue(r))))),
                  q.research_gaps?.length ? h("div", { class: "small" }, h("strong", null, "Research gaps: "), fmtValue(q.research_gaps)) : null,
                ),
            },
            { label: "Qualified", render: (q) => fmtDate(q.qualified_at) },
            {
              label: "",
              render: (q) =>
                h(
                  "div",
                  { class: "row" },
                  h(
                    "button",
                    {
                      class: "btn small",
                      onclick: (e) =>
                        run(e.currentTarget, async () => {
                          await post(`${base}/qualifications/${q.qualification_id}/reevaluate`);
                          refresh();
                        }, "Re-qualified"),
                    },
                    "Re-evaluate",
                  ),
                  ["HIGH_PRIORITY", "QUALIFIED"].includes(q.status)
                    ? h("button", { class: "btn small primary", onclick: () => openDiscover(ctx, q.qualification_id) }, "Find contacts")
                    : null,
                ),
            },
          ],
          qualifications,
          { empty: "No qualifications yet. Qualify a fit assessment above." },
        ),
      ),
    );
  }

  function openDiscover(ctx, qualificationId, qualifications) {
    const qualSelect = qualifications
      ? select(qualifications.map((q) => ({ value: q.qualification_id, label: `${ctx.productName(q.product_id)} · ${q.status} · ${fmtDate(q.qualified_at)}` })))
      : null;
    const max = input({ type: "number", min: 1, max: 25, value: 10 });
    const force = h("input", { type: "checkbox" });
    modal(
      "Discover contacts",
      [
        h("p", { class: "muted" }, "Searches the contact provider (Hunter) for people matching the recommended buyer role. This spends provider credits."),
        qualSelect ? field("Qualification", qualSelect) : null,
        field("Max contacts", max),
        h("label", { class: "check" }, force, "Force refresh"),
      ],
      {
        submitLabel: "Discover",
        onSubmit: async () => {
          const found = await post(`${ctx.base}/contacts/discover`, {
            qualification_id: qualSelect ? qualSelect.value : qualificationId,
            provider: "HUNTER",
            max_results: Number(max.value) || 10,
            force_refresh: force.checked,
          });
          toast(`${found.length} contact(s) found`);
          location.hash = `#/companies/${ctx.id}/contacts`;
          refresh();
        },
      },
    );
  }

  async function companyContacts(ctx) {
    const { base } = ctx;
    const [contacts, qualifications] = await Promise.all([get(`${base}/contacts`), get(`${base}/qualifications`)]);
    const productIds = [...new Set(qualifications.map((q) => q.product_id))];
    if (!state.dmProduct || !productIds.includes(state.dmProduct)) state.dmProduct = productIds[0] || null;
    const legacy = await legacyContacts(ctx, contacts, qualifications);
    if (!state.dmProduct) {
      return h(
        "div",
        null,
        h("div", { class: "card" }, h("h2", null, "Decision makers"), h("div", { class: "empty" }, "Qualify this company for a product first (Fit & qualification tab).")),
        legacy,
      );
    }
    const summary = await get(`${base}/decision-makers?product_id=${encodeURIComponent(state.dmProduct)}`);
    const latest = qualifications.find((q) => q.product_id === state.dmProduct);
    const actionable = latest && ["HIGH_PRIORITY", "QUALIFIED"].includes(latest.status);
    const credits = h("input", { type: "checkbox" });
    const picker = select(
      productIds.map((pid) => ({ value: pid, label: ctx.productName(pid) })),
      {
        value: state.dmProduct,
        onchange: (event) => {
          state.dmProduct = event.target.value;
          refresh();
        },
      },
    );
    const discover = (button, force) =>
      run(button, async () => {
        const result = await post(`${base}/decision-makers/discover`, {
          product_id: state.dmProduct,
          allow_provider_credits: credits.checked,
          force_refresh: force,
        });
        toast(OUTCOME_TEXT[result.run.outcome] || "Search complete");
        refresh();
      });
    const last = summary.last_run;
    const providers = summary.providers || {};
    const rows = summary.contacts.filter((c) => c.role_match !== null);
    const personaLabel = (c) =>
      c.persona_name
        ? h("span", null, h("strong", null, c.persona_name), c.persona_rank ? h("span", { class: "badge good", title: "Selected for this persona" }, `#${c.persona_rank}`) : null)
        : h("span", { class: "muted" }, "no persona match");
    return h(
      "div",
      null,
      h(
        "div",
        { class: "card" },
        h(
          "div",
          { class: "card-head" },
          h("h2", null, "Decision makers"),
          h("div", { class: "row" }, productIds.length > 1 ? picker : h("span", { class: "muted" }, ctx.productName(state.dmProduct))),
        ),
        h(
          "p",
          { class: "muted small" },
          "Checks the company's own website first (free), then — only if you allow credits — one filtered Hunter search, email finding and verification for the top people. LinkedIn links are shown only when a source published them; LinkedIn is never scraped. Emails are never guessed.",
        ),
        summary.derived_personas
          ? h("p", { class: "small" }, "Using the product's buyer roles as personas. ", h("a", { href: `#/products/${state.dmProduct}` }, "Define target personas"), " for better results.")
          : null,
        actionable
          ? h(
              "div",
              { class: "row" },
              h("label", { class: "check" }, credits, "Allow provider credits", providers.hunter_configured ? null : h("span", { class: "muted small" }, " (Hunter not configured)")),
              h("button", { class: "btn primary", onclick: (e) => discover(e.currentTarget, false) }, "Discover decision makers"),
              last ? h("button", { class: "btn", title: "Ignore cached results and search again", onclick: (e) => discover(e.currentTarget, true) }, "Refresh") : null,
            )
          : h("div", { class: "empty" }, `The latest qualification is ${latest ? latest.status.replaceAll("_", " ").toLowerCase() : "missing"}. Contacts can be searched once the company is qualified.`),
        summary.outcome || last
          ? h(
              "div",
              { class: "notice" },
              badge(summary.outcome || last.outcome),
              " ",
              OUTCOME_TEXT[summary.outcome || last.outcome] || "",
              last ? h("div", { class: "muted small" }, `Last search ${fmtDate(last.at)}${last.credits_allowed ? " · with provider credits" : " · public sources only"}${providers.ai_configured ? " · AI role check on" : " · AI role check off (rules only)"}`) : null,
            )
          : null,
        !actionable && rows.length === 0
          ? null
          : table(
          [
            { label: "Target persona", render: personaLabel },
            { label: "Person", render: (c) => h("div", null, h("strong", null, c.full_name || "—"), c.linkedin_url && /^https:\/\/www\.linkedin\.com\/in\//.test(c.linkedin_url) ? h("div", null, h("a", { href: c.linkedin_url, target: "_blank", rel: "noopener noreferrer", class: "small" }, "public profile ↗")) : null) },
            { label: "Current title", render: (c) => fmtValue(c.job_title) },
            { label: "Email", render: (c) => (c.email ? h("div", null, h("span", { class: "mono small" }, c.email), " ", badge(c.email_state)) : badge(c.email_state)) },
            { label: "Employment", render: (c) => badge(c.employment_status) },
            {
              label: "Confidence",
              render: (c) => h("span", { title: (c.suitability_components || {}).rule || "" }, badge(c.suitability), c.suitability_score !== null && c.suitability_score !== undefined ? h("span", { class: "muted small" }, ` ${c.suitability_score}/100`) : null),
            },
            { label: "Status", render: (c) => h("span", null, badge(c.contact_status), c.review_status ? h("span", { class: "muted small" }, ` (${c.review_status.toLowerCase()})`) : null) },
          ],
          rows,
          { onRowClick: (c) => openDecisionMaker(ctx, c.id), empty: last ? "No people found yet." : "Not searched yet. Click “Discover decision makers”." },
        ),
      ),
      h(
        "details",
        { class: "card", open: contacts.length > 0 && rows.length === 0 },
        h("summary", null, `All contact records (${contacts.length})`),
        legacy,
      ),
    );
  }

  async function openDecisionMaker(ctx, contactId) {
    const { base } = ctx;
    let detail;
    try {
      detail = await get(`${base}/decision-makers/${contactId}`);
    } catch (error) {
      if (error.status !== 401) toast(error.message, true);
      return;
    }
    const note = h("textarea", { maxlength: 500, placeholder: "Optional note for the audit trail" });
    let dialog;
    const act = (button, path, body, message) =>
      run(button, async () => {
        await post(`${base}/decision-makers/${contactId}/${path}`, body);
        toast(message);
        dialog.close();
        refresh();
      });
    const components = detail.suitability_components || {};
    const sourceLabel = {
      COMPANY_WEBSITE: "Company website",
      PROVIDER_RECORD: "Provider record",
      PROVIDER_SOURCE_PAGE: "Page cited by provider",
      PUBLIC_PROFILE_URL: "Public profile link",
      EMAIL_VERIFICATION: "Email verification",
      MANUAL: "Manual",
    };
    dialog = modal(detail.full_name || "Person", [
      kv([
        ["Target persona", detail.persona_name ? `${detail.persona_name}${detail.persona_rank ? ` (#${detail.persona_rank})` : ""}` : "No persona match"],
        ["Current title", detail.job_title],
        detail.position_raw && detail.position_raw !== detail.job_title ? ["Title as reported", detail.position_raw] : null,
        ["Department / seniority", [detail.department, detail.seniority].filter(Boolean).join(" · ") || null],
        ["Role match", h("span", null, badge(detail.role_match), ` ${detail.role_confidence ?? "—"}% · ${detail.role_resolver === "AI" ? "AI" : detail.role_resolver === "MANUAL" ? "manual" : "rules"}`)],
        ["Why", detail.role_reason],
        ["Employment", h("span", null, badge(detail.employment_status), " ", h("span", { class: "muted small" }, detail.employment_reason || ""))],
        ["Confidence", h("span", null, badge(detail.suitability), ` ${components.score ?? "—"}/100 — role ${components.role ?? 0}, employment ${components.employment ?? 0}, evidence ${components.evidence ?? 0}, email ${components.email ?? 0}`)],
        ["Rule applied", components.rule],
        ["Status", h("span", null, badge(detail.contact_status), detail.review_status ? ` · reviewed: ${detail.review_status.toLowerCase()}` : "")],
        detail.review_note ? ["Review note", detail.review_note] : null,
        ["Last checked", fmtDate(detail.employment_checked_at)],
      ]),
      h("h3", null, "Emails"),
      detail.emails.length
        ? table(
            [
              { label: "Email", render: (e) => h("span", { class: "mono" }, e.email) },
              { label: "State", render: (e) => badge(e.state) },
              { label: "Found via", render: (e) => `${e.source_provider}${e.origin ? ` · ${e.origin.replaceAll("_", " ").toLowerCase()}` : ""}` },
              { label: "Verified", render: (e) => (e.verified_at ? `${fmtDate(e.verified_at)} · ${(e.verification_source || "").replaceAll("_", " ").toLowerCase()}` : "not yet") },
            ],
            detail.emails,
          )
        : h("div", { class: "empty" }, "No email yet. Emails come only from a provider or from you — never guessed."),
      h("h3", null, "Evidence"),
      table(
        [
          { label: "Source", render: (e) => h("div", null, h("strong", null, sourceLabel[e.source_type] || e.source_type), e.provider_key ? h("div", { class: "muted small" }, e.provider_key) : null) },
          { label: "Says", render: (e) => h("div", null, e.claimed_title ? h("div", null, e.claimed_title) : null, e.snippet ? h("div", { class: "muted small" }, e.snippet) : null) },
          { label: "Link", render: (e) => (e.source_url && /^https?:\/\//i.test(e.source_url) ? h("a", { href: e.source_url, target: "_blank", rel: "noopener noreferrer" }, (() => { try { return new URL(e.source_url).hostname; } catch { return "open"; } })()) : "—") },
          { label: "Retrieved", render: (e) => h("div", null, fmtDate(e.retrieved_at), e.still_on_page === false ? h("div", { class: "badge bad" }, "no longer listed") : null, e.last_seen_on ? h("div", { class: "muted small" }, `last seen ${fmtDate(e.last_seen_on)}`) : null) },
        ],
        detail.evidence,
        { empty: "No evidence stored." },
      ),
      detail.history.length ? jsonDetails(`Audit trail (${detail.history.length})`, detail.history.map((x) => ({ action: x.action, at: x.at, ...(x.details || {}) }))) : null,
      field("Review note", note),
      h(
        "div",
        { class: "row" },
        detail.first_name && detail.last_name
          ? h("button", { class: "btn", type: "button", onclick: (e) => act(e.currentTarget, "find-email", {}, "Email search complete") }, "Find email")
          : null,
        detail.emails.length ? h("button", { class: "btn", type: "button", onclick: (e) => act(e.currentTarget, "verify-email", {}, "Verification complete") }, "Verify email") : null,
        detail.review_status !== "APPROVED" ? h("button", { class: "btn primary", type: "button", onclick: (e) => act(e.currentTarget, "review", { action: "APPROVE", note: note.value.trim() || null }, "Approved") }, "Approve person") : null,
        detail.review_status !== "EXCLUDED" ? h("button", { class: "btn danger", type: "button", onclick: (e) => act(e.currentTarget, "review", { action: "EXCLUDE", note: note.value.trim() || null }, "Excluded") }, "Exclude person") : null,
        detail.review_status ? h("button", { class: "btn", type: "button", onclick: (e) => act(e.currentTarget, "review", { action: "RESET", note: note.value.trim() || null }, "Review reset") }, "Reset review") : null,
        detail.contact_status === "READY_FOR_DRAFT"
          ? h("a", { class: "btn primary", href: `#/companies/${ctx.id}/campaigns`, onclick: () => { state.draftContact = contactId; dialog.close(); } }, "Write email →")
          : null,
      ),
      h("p", { class: "muted small" }, "Find/verify email use Hunter credits. Approve/Exclude are recorded in the audit trail."),
    ]);
  }

  async function legacyContacts(ctx, contacts, qualifications) {
    const { base } = ctx;
    const actionable = qualifications.filter((q) => ["HIGH_PRIORITY", "QUALIFIED"].includes(q.status));
    const verify = (contact) => {
      const email = input({ type: "email", value: contact.emails?.[0]?.email || "" });
      modal(
        `Verify email for ${contact.full_name || "contact"}`,
        [field("Email", email, "Leave as is to verify the email already found."), h("p", { class: "muted small" }, "Uses Hunter verification credits.")],
        {
          submitLabel: "Verify",
          onSubmit: async () => {
            await post(`${base}/contacts/${contact.id}/verify-email`, { provider: "HUNTER", force_refresh: false, email: email.value.trim() || null });
            toast("Verification complete");
            refresh();
          },
        },
      );
    };
    return h(
      "div",
      null,
      h(
        "div",
        { class: "card" },
        h(
          "div",
          { class: "card-head" },
          h("h2", null, "Contacts"),
          actionable.length
            ? h("button", { class: "btn primary", onclick: () => openDiscover(ctx, null, actionable) }, "Discover contacts")
            : h("span", { class: "muted small" }, "Qualify the company to discover contacts."),
        ),
        table(
          [
            { label: "Name", render: (c) => h("strong", null, c.full_name || [c.first_name, c.last_name].filter(Boolean).join(" ") || "—") },
            { label: "Title", render: (c) => fmtValue(c.job_title) },
            { label: "Role match", render: (c) => h("span", null, badge(c.buyer_role_match), " ", h("span", { class: "muted small" }, c.normalized_buyer_role || "")) },
            { label: "Confidence", render: (c) => fmtValue(c.confidence) },
            {
              label: "Emails",
              render: (c) =>
                (c.emails || []).length
                  ? h("div", null, c.emails.map((e) => h("div", null, h("span", { class: "mono" }, e.email), " ", badge(e.verification_status))))
                  : h("span", { class: "muted" }, "none"),
            },
            {
              label: "",
              render: (c) =>
                h(
                  "div",
                  { class: "row" },
                  h(
                    "button",
                    {
                      class: "btn small",
                      onclick: (e) =>
                        run(e.currentTarget, async () => {
                          await post(`${base}/contacts/${c.id}/find-email`, { provider: "HUNTER", force_refresh: false });
                          refresh();
                        }, "Email search complete"),
                    },
                    "Find email",
                  ),
                  h("button", { class: "btn small", onclick: () => verify(c) }, "Verify email"),
                  (c.emails || []).some((e) => e.verification_status === "VALID")
                    ? h("a", { class: "btn small primary", href: `#/companies/${ctx.id}/campaigns`, onclick: () => (state.draftContact = c.id) }, "Write email")
                    : null,
                ),
            },
          ],
          contacts,
          { empty: "No contacts yet." },
        ),
      ),
    );
  }

  async function companyCampaigns(ctx) {
    const { base } = ctx;
    const [drafts, contacts, qualifications, ai, opportunities] = await Promise.all([
      get(`${base}/campaign-drafts`),
      get(`${base}/contacts`),
      get(`${base}/qualifications`),
      soft(get("/campaign-ai/status"), null),
      get(`${base}/opportunities`),
    ]);
    const contactName = (id) => {
      const c = contacts.find((x) => x.id === id);
      return c ? c.full_name || [c.first_name, c.last_name].filter(Boolean).join(" ") || c.job_title || "Contact" : "Contact";
    };
    const actionable = qualifications.filter((q) => ["HIGH_PRIORITY", "QUALIFIED"].includes(q.status));
    const qualSelect = select(actionable.map((q) => ({ value: q.qualification_id, label: `${ctx.productName(q.product_id)} · ${q.status}` })));
    const contactSelect = h("select");
    const fillContacts = () => {
      clear(contactSelect);
      const eligible = contacts.filter((c) => c.qualification_id === qualSelect.value);
      append(contactSelect, [
        eligible.map((c) => {
          const valid = (c.emails || []).some((e) => e.verification_status === "VALID");
          return h("option", { value: c.id, selected: state.draftContact === c.id }, `${contactName(c.id)}${c.job_title ? ` — ${c.job_title}` : ""}${valid ? "" : " (email not verified)"}`);
        }),
      ]);
      if (!eligible.length) contactSelect.append(h("option", { value: "" }, "No contacts for this qualification"));
    };
    qualSelect.addEventListener("change", fillContacts);
    fillContacts();
    const cta = input({ maxlength: 500, placeholder: "e.g. Book a 20-minute demo next week" });

    const generate = (useAi, button) =>
      run(button, async () => {
        if (!contactSelect.value) throw new ApiError(0, "Choose a contact first.");
        const body = { qualification_id: qualSelect.value, contact_id: contactSelect.value, call_to_action: cta.value.trim() || null };
        const draft = useAi
          ? await post(`${base}/campaign-drafts/ai`, { ...body, fallback_to_deterministic: true })
          : await post(`${base}/campaign-drafts`, { ...body, channel: "EMAIL" });
        state.draftContact = null;
        state.selectedDraft = draft.id;
        refresh();
      }, "Draft created");

    const selected = drafts.find((d) => d.id === state.selectedDraft) || drafts[0];
    const editor = selected ? await draftEditor(ctx, selected, contactName, opportunities) : null;

    return h(
      "div",
      null,
      h(
        "div",
        { class: "card" },
        h("h2", null, "New campaign draft"),
        actionable.length === 0
          ? h("div", { class: "empty" }, "Qualify the company and find contacts first.")
          : [
              h("div", { class: "form-grid" }, field("Qualification", qualSelect), field("Contact", contactSelect)),
              field("Call to action (optional)", cta),
              h(
                "div",
                { class: "row" },
                h("button", { class: "btn primary", disabled: !ai?.configured, title: ai?.configured ? "" : "OpenAI is not configured", onclick: (e) => generate(true, e.currentTarget) }, "Draft with AI"),
                h("button", { class: "btn", onclick: (e) => generate(false, e.currentTarget) }, "Draft from template"),
              ),
              h("p", { class: "muted small" }, "Drafts are grounded in the company's verified intelligence. Nothing is sent until you approve and click Send."),
            ],
      ),
      h(
        "div",
        { class: "card" },
        h("h2", null, "Drafts"),
        table(
          [
            { label: "Subject", render: (d) => h("strong", null, d.subject || "(no subject)") },
            { label: "To", render: (d) => contactName(d.contact_id) },
            { label: "Status", render: (d) => badge(d.status) },
            { label: "Revision", key: "revision_number" },
            { label: "Angle", render: (d) => fmtValue(d.message_angle) },
            { label: "Created", render: (d) => fmtDate(d.created_at) },
          ],
          drafts,
          {
            empty: "No drafts yet.",
            selectedId: selected?.id,
            onRowClick: (d) => {
              state.selectedDraft = d.id;
              refresh();
            },
          },
        ),
      ),
      editor,
    );
  }

  async function draftEditor(ctx, draft, contactName, opportunities) {
    const { base } = ctx;
    const draftBase = `${base}/campaign-drafts/${draft.id}`;
    const [attempts, history] = await Promise.all([get(`${draftBase}/send-attempts`), get(`${draftBase}/history`)]);
    const sent = attempts.find((a) => a.status === "SENT");
    const subject = input({ value: draft.subject || "", maxlength: 255 });
    const body = h("textarea", { rows: 12, maxlength: 20000 }, draft.body || "");
    const cta = input({ value: draft.call_to_action || "", maxlength: 500 });
    const reason = input({ maxlength: 500, placeholder: "Why this change or approval (optional)" });
    const locked = Boolean(sent);

    const approve = (button) =>
      run(button, async () => {
        await post(`${draftBase}/approve`, { reason: reason.value.trim() || null });
        refresh();
      }, "Draft approved");

    const reject = () => {
      const why = input({ required: true, maxlength: 500 });
      modal("Reject draft", [field("Reason", why)], {
        submitLabel: "Reject",
        danger: true,
        onSubmit: async () => {
          await post(`${draftBase}/reject`, { reason: why.value.trim() });
          toast("Draft rejected");
          refresh();
        },
      });
    };

    const send = () => {
      const key = newKey();
      modal(
        "Send email",
        [
          h("p", null, "Send this approved email to ", h("strong", null, contactName(draft.contact_id)), " now?"),
          h("div", { class: "email-preview" }, h("strong", null, draft.subject), "\n\n", draft.body),
          h("p", { class: "muted small" }, "An unsubscribe link is added automatically. The email is sent once only."),
        ],
        {
          submitLabel: "Send now",
          onSubmit: async () => {
            const attempt = await post(`${draftBase}/send`, { provider: "SMTP", idempotency_key: key });
            if (attempt.status === "SENT") toast("Email sent");
            else toast(`Send failed: ${attempt.error_category || attempt.status}`, true);
            refresh();
          },
        },
      );
    };

    const opportunityFor = (attempt) => opportunities.find((o) => o.send_attempt_id === attempt.id);

    return h(
      "div",
      { class: "card" },
      h(
        "div",
        { class: "card-head" },
        h("h2", null, "Review draft"),
        h("div", { class: "row" }, badge(draft.status), h("span", { class: "muted small" }, `Revision ${draft.revision_number} · to ${contactName(draft.contact_id)}`)),
      ),
      locked
        ? h("div", null, h("div", { class: "email-preview" }, h("strong", null, draft.subject), "\n\n", draft.body))
        : [
            field("Subject", subject),
            field("Body", body),
            field("Call to action", cta),
            field("Review note", reason),
            h(
              "div",
              { class: "row" },
              h(
                "button",
                {
                  class: "btn",
                  onclick: (e) =>
                    run(e.currentTarget, async () => {
                      await patch(draftBase, {
                        subject: subject.value,
                        body: body.value,
                        call_to_action: cta.value || null,
                        reason: reason.value.trim() || null,
                      });
                      refresh();
                    }, "Changes saved"),
                },
                "Save changes",
              ),
              draft.status !== "APPROVED" ? h("button", { class: "btn primary", onclick: (e) => approve(e.currentTarget) }, "Approve") : null,
              draft.status !== "REJECTED" ? h("button", { class: "btn danger", onclick: reject }, "Reject") : null,
              draft.status === "APPROVED" ? h("button", { class: "btn primary", onclick: send }, "Send email…") : null,
            ),
          ],
      h("h3", { class: "stack" }, "Send attempts"),
      table(
        [
          { label: "Requested", render: (a) => fmtDate(a.requested_at) },
          { label: "Status", render: (a) => badge(a.status) },
          { label: "Error", render: (a) => fmtValue(a.error_category) },
          {
            label: "",
            render: (a) =>
              a.status !== "SENT"
                ? null
                : opportunityFor(a)
                  ? h("a", { href: `#/companies/${ctx.id}/pipeline` }, `Opportunity: ${opportunityFor(a).stage}`)
                  : h(
                      "button",
                      {
                        class: "btn small primary",
                        onclick: (e) =>
                          run(e.currentTarget, async () => {
                            await post(`${base}/opportunities`, { send_attempt_id: a.id });
                            location.hash = `#/companies/${ctx.id}/pipeline`;
                          }, "Opportunity created"),
                      },
                      "Track as opportunity",
                    ),
          },
        ],
        attempts,
        { empty: "Not sent yet." },
      ),
      h(
        "details",
        null,
        h("summary", null, `Review history (${history.length})`),
        table(
          [
            { label: "Rev", key: "revision_number" },
            { label: "Action", render: (e) => badge(e.action) },
            { label: "Status", render: (e) => badge(e.status) },
            { label: "Reason", render: (e) => fmtValue(e.reason) },
            { label: "When", render: (e) => fmtDate(e.created_at) },
          ],
          history,
        ),
      ),
    );
  }

  const STAGES = ["CONTACTED", "RESPONDED", "MEETING", "PROPOSAL", "WON", "LOST"];

  async function companyPipeline(ctx) {
    const { base } = ctx;
    const opportunities = await get(`${base}/opportunities`);
    const move = (opp) => {
      const stage = select(STAGES.filter((s) => s !== opp.stage), {});
      const note = h("textarea", { maxlength: 2000 });
      modal(`Move opportunity from ${opp.stage}`, [field("New stage", stage), field("Note", note)], {
        submitLabel: "Move",
        onSubmit: async () => {
          await post(`${base}/opportunities/${opp.id}/stage`, { stage: stage.value, note: note.value.trim() || null });
          toast("Stage updated");
          refresh();
        },
      });
    };
    const edit = (opp) => {
      const value = input({ type: "number", min: 0, step: "any", value: opp.estimated_value ?? "" });
      const currency = input({ maxlength: 3, value: opp.currency || "SAR" });
      const next = input({ maxlength: 500, value: opp.next_action || "" });
      modal("Edit opportunity", [h("div", { class: "form-grid" }, field("Estimated value", value), field("Currency", currency)), field("Next action", next)], {
        onSubmit: async () => {
          await patch(`${base}/opportunities/${opp.id}`, {
            estimated_value: numOrNull(value.value),
            currency: currency.value.trim().toUpperCase() || null,
            next_action: next.value.trim() || null,
          });
          toast("Opportunity saved");
          refresh();
        },
      });
    };
    return h(
      "div",
      { class: "card" },
      h("h2", null, "Opportunities"),
      h("p", { class: "muted small" }, "Opportunities start from a sent email. Stages move forward: contacted → responded → meeting → proposal → won/lost."),
      table(
        [
          { label: "Stage", render: (o) => badge(o.stage) },
          { label: "Value", render: (o) => (o.estimated_value != null ? `${fmtValue(o.estimated_value)} ${o.currency || ""}` : "—") },
          { label: "Next action", render: (o) => fmtValue(o.next_action) },
          { label: "Created", render: (o) => fmtDate(o.created_at) },
          { label: "Updated", render: (o) => fmtDate(o.updated_at) },
          {
            label: "",
            render: (o) =>
              h(
                "div",
                { class: "row" },
                ["WON", "LOST"].includes(o.stage) ? null : h("button", { class: "btn small primary", onclick: () => move(o) }, "Move stage"),
                h("button", { class: "btn small", onclick: () => edit(o) }, "Edit"),
              ),
          },
        ],
        opportunities,
        { empty: "No opportunities yet. Send an approved email, then track it as an opportunity." },
      ),
    );
  }

  // ---------------------------------------------------------------------------------------
  // Insights & suppressions

  // ---------------------------------------------------------------------------------------
  // Automation: company discovery and scheduled workflows

  const CADENCES = [
    { value: "360", label: "Every 6 hours" },
    { value: "1440", label: "Daily" },
    { value: "4320", label: "Every 3 days" },
    { value: "10080", label: "Weekly" },
  ];
  const HEADCOUNTS = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001-10000", "10001+"];
  const DISCOVERY_ERRORS = {
    DISCOVERY_PROVIDER_NOT_CONFIGURED: "Hunter is not configured (HUNTER_API_KEY).",
    DISCOVERY_NO_DISCOVER_ACCESS: "Your Hunter plan does not include Discover.",
    DISCOVERY_RATE_LIMITED: "Hunter rate limit reached; it will retry on the next run.",
    DISCOVERY_AUTHENTICATION_ERROR: "Hunter rejected the API key.",
    DISCOVERY_INVALID_FILTER: "Hunter rejected one of the filters. Edit the rule.",
    DISCOVERY_RUN_AS_PERMISSION_DENIED: "The rule's owner no longer has permission to run it.",
  };

  const cadenceLabel = (minutes) =>
    CADENCES.find((c) => Number(c.value) === Number(minutes))?.label || `Every ${Math.round(minutes / 60)} h`;

  function criteriaSummary(c) {
    if (c.query) return `“${c.query}”`;
    const parts = [];
    if (c.countries?.length) parts.push(c.countries.join(", "));
    if (c.industries?.length) parts.push(c.industries.join(", "));
    if (c.keywords?.length) parts.push(`keywords: ${c.keywords.join(", ")}`);
    if (c.headcounts?.length) {
      const low = c.headcounts[0].split(/[-+]/)[0];
      const last = c.headcounts[c.headcounts.length - 1];
      const high = last.endsWith("+") ? "+" : `–${last.split("-")[1]}`;
      parts.push(`${low}${high} staff`);
    }
    return parts.join(" · ") || "—";
  }

  function criteriaForm(initial = {}) {
    const query = input({ maxlength: 500, value: initial.query || "", placeholder: "Optional: describe the companies in plain English, e.g. Container terminal operators in the GCC" });
    const countries = input({ value: (initial.countries || []).join(", "), placeholder: "SA, AE, QA" });
    const keywords = input({ value: (initial.keywords || []).join(", "), placeholder: "logistics, ports, shipping" });
    const industries = input({ value: (initial.industries || []).join(", "), placeholder: "Optional: Hunter industry names, e.g. Transportation, Logistics, Supply Chain and Storage" });
    const boxes = HEADCOUNTS.map((value) => {
      const box = h("input", { type: "checkbox", value, checked: (initial.headcounts || []).includes(value) });
      return { value, box, label: h("label", { class: "row small" }, box, value) };
    });
    const node = h(
      "div",
      null,
      field("Describe the companies (optional)", query, "If filled, this description is used instead of the filters below."),
      h("div", { class: "form-grid" }, field("Countries", countries, "2-letter codes, comma separated"), field("Keywords", keywords, "Comma separated; companies matching any keyword")),
      field("Industries (optional)", industries, "Must match Hunter's industry names; unrecognised names are searched as keywords."),
      h("div", { class: "field" }, h("span", { class: "small muted" }, "Company size (employees)"), h("div", { class: "row" }, boxes.map((b) => b.label))),
    );
    return {
      node,
      value: () => ({
        query: query.value.trim() || null,
        countries: csv(countries.value).map((c) => c.toUpperCase()),
        keywords: csv(keywords.value),
        industries: csv(industries.value),
        headcounts: boxes.filter((b) => b.box.checked).map((b) => b.value),
      }),
    };
  }

  function openRuleForm(productList, productId, criteria, onSaved) {
    const product = select(productList.map((p) => ({ value: p.id, label: p.name })), { value: productId });
    const name = input({ maxlength: 200, value: `${productList.find((p) => p.id === productId)?.name || "Product"} discovery` });
    const form = criteriaForm(criteria);
    const cadence = select(CADENCES, { value: "1440" });
    const maxNew = input({ type: "number", min: 1, max: 100, value: 20 });
    const workflow = h("input", { type: "checkbox", checked: true });
    const credits = h("input", { type: "checkbox" });
    modal(
      "Automatic company discovery",
      [
        h("p", { class: "muted small" }, "On each run, MarketingIQ searches Hunter Discover (free, no credits), adds up to the limit of companies you don't have yet, marks them “discovered”, and optionally runs research → fit → qualification on each. Nothing is ever emailed without your approval."),
        h("div", { class: "form-grid" }, field("Rule name", name), field("Product", product)),
        form.node,
        h("div", { class: "form-grid" }, field("How often", cadence), field("New companies per run (max)", maxNew)),
        h("label", { class: "check" }, workflow, "Run research, fit and qualification on each new company"),
        h("label", { class: "check" }, credits, "Also find contacts via Hunter for qualified companies (uses Hunter credits)"),
      ],
      {
        submitLabel: "Create rule",
        onSubmit: async () => {
          await post("/company-discovery/policies", {
            name: name.value.trim() || null,
            product_id: product.value,
            ...form.value(),
            cadence_minutes: Number(cadence.value),
            max_new_per_run: Number(maxNew.value) || 20,
            run_workflow: workflow.checked,
            allow_provider_credits: credits.checked,
            enabled: true,
          });
          toast("Discovery rule created. It runs on the next scheduled check.");
          onSaved();
        },
      },
    );
  }

  async function showRuns(rule) {
    const runs = await get(`/company-discovery/policies/${rule.id}/runs`);
    modal(`Runs: ${rule.name}`, [
      table(
        [
          { label: "Started", render: (r) => fmtDate(r.started_at) },
          { label: "Status", render: (r) => badge(r.status) },
          { label: "Found", key: "candidates_found" },
          { label: "Added", key: "companies_added" },
          { label: "Already had", key: "already_known" },
          { label: "Workflows ok / stopped", render: (r) => `${r.workflows_completed} / ${r.workflows_failed}` },
          { label: "Notes", render: (r) => (r.error_code ? DISCOVERY_ERRORS[r.error_code] || r.error_code : (r.notes || []).join(" ")) || "—" },
        ],
        runs,
        { empty: "No runs yet." },
      ),
    ]);
  }

  async function viewAutomation() {
    const [productList, rules, schedules, companies, research] = await Promise.all([
      products(true),
      get("/company-discovery/policies"),
      soft(get("/automation-policies"), []),
      get("/companies"),
      soft(get("/providers/status"), []),
    ]);
    const hunterReady = research.some((p) => p.provider_key === "HUNTER" && p.credentials_configured);
    const productName = (id) => productList.find((p) => p.id === id)?.name || "—";
    const companyName = (id) => companies.find((c) => c.id === id)?.canonical_name || "—";

    // --- find companies now -----------------------------------------------------------
    const finder = h("div");
    const results = h("div");
    const picker = productSelect(productList, { onlyActive: false });
    let form = criteriaForm();
    const loadSuggestion = async () => {
      if (!picker.el.value) return;
      const suggestion = await soft(get(`/company-discovery/suggest?product_id=${encodeURIComponent(picker.el.value)}`), {});
      form = criteriaForm(suggestion || {});
      clear(finder).append(form.node);
    };
    picker.el.addEventListener("change", loadSuggestion);
    await loadSuggestion();

    const drawResults = (preview) => {
      const checks = new Map();
      const addButton = h("button", { class: "btn primary" }, "Add selected companies");
      addButton.addEventListener("click", () =>
        run(addButton, async () => {
          const chosen = preview.companies.filter((c) => checks.get(c.domain)?.checked);
          if (!chosen.length) throw new ApiError(0, "Select at least one company.");
          const result = await post("/company-discovery/add", { companies: chosen.map((c) => ({ domain: c.domain, name: c.name })) });
          toast(`${result.added.length} companies added`);
          chosen.forEach((c) => (c.already_in_list = true));
          drawResults(preview);
        }),
      );
      append(clear(results), [
        h("div", { class: "card-head" }, h("h3", null, `${preview.companies.length} companies found${preview.total_results ? ` (of ${preview.total_results} matching)` : ""}`), preview.companies.length ? addButton : null),
        (preview.notes || []).length ? h("p", { class: "muted small" }, preview.notes.join(" ")) : null,
        table(
          [
            {
              label: "",
              render: (c) => {
                const box = h("input", { type: "checkbox", checked: !c.already_in_list, disabled: c.already_in_list, "aria-label": `Select ${c.name}` });
                checks.set(c.domain, box);
                return box;
              },
            },
            { label: "Company", render: (c) => h("strong", null, c.name) },
            { label: "Domain", render: (c) => h("a", { href: `https://${c.domain}`, target: "_blank", rel: "noopener noreferrer" }, c.domain) },
            { label: "", render: (c) => (c.already_in_list ? badge("already in list") : null) },
          ],
          preview.companies,
          { empty: "No companies matched. Try fewer filters or a broader description." },
        ),
      ]);
    };

    return h(
      "div",
      null,
      pageHead("Automation", "Find new target companies automatically and keep their research up to date. Emails always need your approval."),
      hunterReady
        ? null
        : h("div", { class: "card" }, h("strong", null, "Hunter is not configured. "), "Company discovery and contact search need a HUNTER_API_KEY in the hosting settings (Setup Python App → Environment variables), then Restart."),
      h(
        "div",
        { class: "card" },
        h("div", { class: "card-head" }, h("h2", null, "Find companies now")),
        h("p", { class: "muted small" }, "Filters are pre-filled from the product's active ICP. Searching is free (Hunter Discover uses no credits)."),
        picker.empty ? h("div", { class: "empty" }, "Create a product first.") : [field("Product", picker.el), finder],
        h(
          "div",
          { class: "row" },
          h(
            "button",
            {
              class: "btn primary",
              disabled: picker.empty,
              onclick: (e) =>
                run(e.currentTarget, async () => {
                  const preview = await post("/company-discovery/preview", form.value());
                  drawResults(preview);
                }),
            },
            "Search",
          ),
          h(
            "button",
            {
              class: "btn",
              disabled: picker.empty,
              onclick: () => openRuleForm(productList, picker.el.value, form.value(), refresh),
            },
            "Save as automatic rule…",
          ),
        ),
        results,
      ),
      h(
        "div",
        { class: "card" },
        h("div", { class: "card-head" }, h("h2", null, "Automatic discovery rules")),
        table(
          [
            { label: "Rule", render: (r) => h("div", null, h("strong", null, r.name), h("div", { class: "muted small" }, productName(r.product_id))) },
            { label: "Looks for", render: (r) => criteriaSummary(r) },
            { label: "Schedule", render: (r) => `${cadenceLabel(r.cadence_minutes)} · up to ${r.max_new_per_run}` },
            { label: "Then", render: (r) => (r.run_workflow ? (r.allow_provider_credits ? "Research → qualify → contacts" : "Research → qualify") : "Just add") },
            { label: "Status", render: (r) => badge(r.enabled ? "ACTIVE" : "PAUSED") },
            {
              label: "Last run",
              render: (r) =>
                r.last_run_at
                  ? h("div", null, badge(r.last_status), h("div", { class: "muted small" }, r.last_error_code ? DISCOVERY_ERRORS[r.last_error_code] || r.last_error_code : fmtDate(r.last_run_at)))
                  : "—",
            },
            { label: "Next run", render: (r) => (r.enabled ? fmtDate(r.next_run_at) : "—") },
            {
              label: "",
              render: (r) =>
                h(
                  "div",
                  { class: "row" },
                  h(
                    "button",
                    {
                      class: "btn small primary",
                      onclick: (e) =>
                        run(e.currentTarget, async () => {
                          const result = await post(`/company-discovery/policies/${r.id}/run`);
                          if (result.status === "FAILED") throw new ApiError(0, DISCOVERY_ERRORS[result.error_code] || result.error_code);
                          toast(`Added ${result.companies_added} new companies (${result.already_known} already in your list)`);
                          refresh();
                        }),
                    },
                    "Run now",
                  ),
                  h(
                    "button",
                    {
                      class: "btn small",
                      onclick: (e) =>
                        run(e.currentTarget, async () => {
                          await patch(`/company-discovery/policies/${r.id}`, { enabled: !r.enabled });
                          refresh();
                        }, r.enabled ? "Rule paused" : "Rule resumed"),
                    },
                    r.enabled ? "Pause" : "Resume",
                  ),
                  h("button", { class: "btn small", onclick: (e) => run(e.currentTarget, () => showRuns(r)) }, "History"),
                ),
            },
          ],
          rules,
          { empty: "No rules yet. Search above, then “Save as automatic rule…”." },
        ),
      ),
      h(
        "div",
        { class: "card" },
        h("h2", null, "Scheduled company workflows"),
        h("p", { class: "muted small" }, "Per-company schedules that keep research, fit and qualification current. Create one from a company's page with “Schedule weekly”."),
        table(
          [
            { label: "Company", render: (s) => h("a", { href: `#/companies/${s.organization_company_id}` }, companyName(s.organization_company_id)) },
            { label: "Product", render: (s) => productName(s.product_id) },
            { label: "Schedule", render: (s) => cadenceLabel(s.cadence_minutes) },
            { label: "Contacts", render: (s) => (s.allow_provider_credits ? "Yes (credits)" : "No") },
            { label: "Status", render: (s) => badge(s.enabled ? "ACTIVE" : "PAUSED") },
            { label: "Last run", render: (s) => (s.last_run_at ? h("span", null, badge(s.last_status), " ", h("span", { class: "muted small" }, s.last_error_code || fmtDate(s.last_run_at))) : "—") },
            { label: "Next run", render: (s) => (s.enabled ? fmtDate(s.next_run_at) : "—") },
            {
              label: "",
              render: (s) =>
                h(
                  "button",
                  {
                    class: "btn small",
                    onclick: (e) =>
                      run(e.currentTarget, async () => {
                        await patch(`/automation-policies/${s.id}`, { enabled: !s.enabled });
                        refresh();
                      }, s.enabled ? "Schedule paused" : "Schedule resumed"),
                  },
                  s.enabled ? "Pause" : "Resume",
                ),
            },
          ],
          schedules,
          { empty: "No company schedules yet." },
        ),
      ),
    );
  }

  async function viewInsights() {
    const productList = await products();
    const picker = select([{ value: "", label: "All products" }, ...productList.map((p) => ({ value: p.id, label: p.name }))], { value: state.insightProduct || "" });
    picker.addEventListener("change", () => {
      state.insightProduct = picker.value;
      refresh();
    });
    const q = state.insightProduct ? `?product_id=${encodeURIComponent(state.insightProduct)}` : "";
    const [summary, roles, angles, grades, industries, cycle] = await Promise.all([
      get(`/learning/conversion-summary${q}`),
      soft(get(`/learning/buyer-role-performance${q}`), []),
      soft(get(`/learning/message-angle-performance${q}`), []),
      soft(get(`/learning/qualification-grade-performance${q}`), []),
      soft(get(`/learning/industry-performance${q}`), []),
      soft(get(`/learning/cycle-time${q}`), null),
    ]);
    const counts = summary.counts || {};
    const rates = summary.rates || {};
    return h(
      "div",
      null,
      pageHead("Insights", `Observed outcomes from ${summary.sample_size} opportunities. Descriptive, not predictive.`, picker),
      h(
        "div",
        { class: "grid cols-2" },
        h("div", { class: "card" }, h("h2", null, "Funnel counts"), kv(Object.entries(counts).map(([k, v]) => [k.replaceAll("_", " "), v]))),
        h("div", { class: "card" }, h("h2", null, "Conversion rates"), kv(Object.entries(rates).map(([k, v]) => [k.replaceAll("_", " "), fmtPct(v)]))),
      ),
      h("div", { class: "card" }, h("h2", null, "By buyer role"), genericTable(roles)),
      h("div", { class: "card" }, h("h2", null, "By message angle"), genericTable(angles)),
      h("div", { class: "card" }, h("h2", null, "By qualification grade"), genericTable(grades)),
      h("div", { class: "card" }, h("h2", null, "By industry"), genericTable(industries)),
      cycle ? h("div", { class: "card" }, h("h2", null, "Cycle time"), jsonDetails("show", cycle)) : null,
    );
  }

  async function viewSuppressions() {
    const list = await get("/outbound/suppressions");
    const email = input({ type: "email", required: true, placeholder: "person@company.com" });
    const reason = input({ maxlength: 500 });
    const source = select(["MANUAL", "OPT_OUT", "BOUNCE", "COMPLAINT"], { value: "MANUAL" });
    return h(
      "div",
      null,
      pageHead("Suppression list", "Addresses that will never be emailed. Recipients who click unsubscribe are added automatically."),
      h(
        "form",
        {
          class: "card",
          onsubmit: (event) => {
            event.preventDefault();
            run(event.submitter, async () => {
              await post("/outbound/suppressions", { email: email.value.trim(), reason: reason.value.trim() || null, source: source.value });
              refresh();
            }, "Address suppressed");
          },
        },
        h("h2", null, "Add address"),
        h("div", { class: "form-grid" }, field("Email", email), field("Source", source)),
        field("Reason", reason),
        h("button", { class: "btn primary", type: "submit" }, "Suppress"),
      ),
      h(
        "div",
        { class: "card" },
        table(
          [
            { label: "Email", render: (s) => h("span", { class: "mono" }, s.email) },
            { label: "Source", render: (s) => badge(s.source) },
            { label: "Reason", render: (s) => fmtValue(s.reason) },
            { label: "Added", render: (s) => fmtDate(s.created_at) },
          ],
          list,
          { empty: "No suppressed addresses." },
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------------------------

  window.addEventListener("hashchange", () => {
    if (state.me) render();
  });
  document.addEventListener("DOMContentLoaded", boot);
})();
