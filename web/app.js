// Overview of the AWS account: costs from the Cost and Usage Report, the
// budgets, the days and events of CloudTrail and what the rules found there.
// The page signs in with Cognito and calls /api with the ID token; the API is
// in lambda/app.py. The texts are in i18n.js, the charts in charts.js.
(function () {
  const config = window.AWS_CONFIG || {};
  const i18n = window.AWS_I18N;
  const charts = window.AWS_CHARTS;
  const root = document.getElementById("root");
  const state = {
    me: null,
    // overview, costs, trail, traffic or findings; the address keeps it
    // (#costs/2026-10).
    view: "overview",
    recommendations: null,
    // The access logs: the sources (a distribution or a bucket), the one
    // shown, its day, the totals of the 14 days to it and the lists of the day.
    trafficSources: null,
    trafficSource: "",
    trafficDay: null,
    trafficDays: {},
    trafficCounts: null,
    overview: null,
    periods: [],
    period: null,
    month: null,
    day: null,
    days: [],
    events: [],
    eventsCursor: null,
    // fields: what a click on a principal, service, event, region, address
    // or error code narrowed the events to; the server applies it.
    eventFilter: { text: "", errors: false, writes: false, fields: {} },
    eventSort: { key: "time", desc: true },
    // What the filter keeps, counted by combination (/api/trail/summary):
    // the tiles, the lists and the chart follow it; null without a filter.
    summary: null,
    findings: [],
    findingsCursor: null,
    severity: "",
    openOnly: false,
  };
  const ISSUER = "AWS account demo";
  // The open source project of this page, linked from the sign-in page.
  const REPOSITORY = "https://github.com/Tozapid/account-serverless-aws";
  // The language is the Cognito attribute "locale" of the user; the browser
  // keeps a copy so that the sign-in page speaks it too.
  const LANGUAGE_KEY = "aws-language";
  const VIEWS = ["overview", "costs", "trail", "traffic", "findings"];
  let language = i18n.fallback;
  let pool = null;
  let toastTimer = null;
  let listening = false;

  // Language ---------------------------------------------------------------

  function known(code) {
    return Boolean(code && i18n.strings[code]);
  }

  // The text of a key in the current language with its {name} parts filled.
  function t(key, params) {
    let text = i18n.strings[language][key] ?? i18n.strings[i18n.fallback][key] ?? key;
    if (typeof text === "object") {
      text = text[new Intl.PluralRules(language).select(params.count)] ?? text.other;
    }
    return text.replace(/\{(\w+)\}/g, (part, name) => (params && name in params ? String(params[name]) : part));
  }

  function setLanguage(code) {
    if (!known(code)) return;
    language = code;
    document.documentElement.lang = code;
    document.title = t("brand");
    try {
      localStorage.setItem(LANGUAGE_KEY, code);
    } catch (error) {
      // Private windows may refuse storage; the language holds until reload.
    }
  }

  function storedLanguage() {
    try {
      return localStorage.getItem(LANGUAGE_KEY);
    } catch (error) {
      return null;
    }
  }

  // The first language of the browser this page speaks, else the fallback.
  function browserLanguage() {
    const found = (navigator.languages || [navigator.language]).map((code) => String(code).slice(0, 2)).find(known);
    return found || i18n.fallback;
  }

  async function loadLanguage() {
    try {
      const { user } = await currentUser();
      const attributes = await cognito((done) => user.getUserAttributes(done));
      const locale = (attributes || []).find((attribute) => attribute.getName() === "locale");
      if (locale) setLanguage(locale.getValue());
    } catch (error) {
      // The language of this browser stays.
    }
  }

  async function saveLanguage(code) {
    setLanguage(code);
    try {
      const { user } = await currentUser();
      await cognito((done) => user.updateAttributes([{ Name: "locale", Value: code }], done));
      toast(t("language.saved"));
    } catch (error) {
      toast(authMessage(error));
    }
  }

  // Page helpers -----------------------------------------------------------

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value == null || value === false) return;
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "value") node.value = value;
      else node.setAttribute(key, value === true ? "" : value);
    });
    [].concat(children || []).forEach((child) => {
      if (child == null || child === false) return;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  // replaceChildren() turns null into the text "null"; empty parts are left out.
  function fill(node, children) {
    node.replaceChildren(...[].concat(children).filter(Boolean));
  }

  function toast(message) {
    let node = document.querySelector(".toast");
    if (!node) {
      node = el("div", { class: "toast", role: "status" });
      document.body.append(node);
    }
    node.textContent = message;
    node.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.add("hidden"), 3500);
  }

  function when(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language, { dateStyle: "short", timeStyle: "short" });
  }

  function clock(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  // A day (2026-10-17) as the page's language writes it; UTC, as in the logs.
  function dayName(day, style) {
    const date = new Date(day + "T12:00:00Z");
    if (Number.isNaN(date.getTime())) return day;
    return date.toLocaleDateString(language, style === "short" ? { day: "numeric", month: "short", timeZone: "UTC" }
      : { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  }

  function monthName(period) {
    const date = new Date(period + "-15T12:00:00Z");
    return Number.isNaN(date.getTime()) ? period : date.toLocaleDateString(language, { month: "long", year: "numeric", timeZone: "UTC" });
  }

  function money(value, unit) {
    const amount = Number(value || 0);
    const digits = Math.abs(amount) > 0 && Math.abs(amount) < 0.01 ? 4 : 2;
    try {
      return new Intl.NumberFormat(language, { style: "currency", currency: unit || "USD", minimumFractionDigits: 2, maximumFractionDigits: digits }).format(amount);
    } catch (error) {
      return amount.toFixed(digits) + " " + (unit || "USD");
    }
  }

  function number(value) {
    return new Intl.NumberFormat(language).format(value || 0);
  }

  function addDays(day, count) {
    const date = new Date(day + "T12:00:00Z");
    date.setUTCDate(date.getUTCDate() + count);
    return date.toISOString().slice(0, 10);
  }

  function today() {
    return new Date().toISOString().slice(0, 10);
  }

  function sum(values) {
    return Object.values(values || {}).reduce((total, value) => total + Number(value || 0), 0);
  }

  // A modal window over the page. The page below may re-render; the dialog
  // stays and fill() replaces its contents.
  function dialog(title, onClose) {
    const body = el("div", { class: "modal-body" });
    const heading = el("h2", {}, title);
    const node = el("dialog", { class: "modal" }, [
      el("div", { class: "modal-head" }, [
        heading,
        el("button", { type: "button", class: "icon-button close", "aria-label": t("close"), onclick: () => node.close() }, "×"),
      ]),
      body,
    ]);
    node.addEventListener("close", () => {
      node.remove();
      if (onClose) onClose();
    });
    node.addEventListener("click", (event) => {
      if (event.target === node) node.close();
    });
    document.body.append(node);
    node.showModal();
    return {
      node,
      close: () => node.close(),
      title: (text) => { heading.textContent = text; },
      fill: (children) => fill(body, children),
    };
  }

  // Severity is never colour alone: the badge says it in words.
  function severityBadge(severity) {
    return el("span", { class: "badge " + (severity === "critical" ? "bad" : "warn") }, t("severity." + severity));
  }

  function facts(pairs) {
    return el("dl", { class: "facts" }, pairs.filter((pair) => pair && pair[1] !== "" && pair[1] != null)
      .flatMap(([label, value]) => [el("dt", {}, label), el("dd", {}, value)]));
  }

  // Sign-in ----------------------------------------------------------------

  function userPool() {
    if (!pool) {
      pool = new window.AmazonCognitoIdentity.CognitoUserPool({ UserPoolId: config.userPoolId, ClientId: config.clientId });
    }
    return pool;
  }

  // The signed-in user with a valid session, which the account calls need.
  function currentUser() {
    return new Promise((resolve, reject) => {
      const user = userPool().getCurrentUser();
      if (!user) return reject(new Error("auth"));
      user.getSession((error, session) => {
        if (error || !session || !session.isValid()) reject(error || new Error("auth"));
        else resolve({ user, session });
      });
    });
  }

  async function currentSession() {
    return (await currentUser()).session;
  }

  // Wraps a callback-style call of the Cognito library.
  function cognito(call) {
    return new Promise((resolve, reject) => call((error, result) => (error ? reject(error) : resolve(result))));
  }

  function authMessage(error) {
    const code = error && (error.code || error.name);
    if (code === "NotAuthorizedException" || code === "UserNotFoundException") return t("auth.wrong");
    if (code === "InvalidPasswordException") return t("auth.passwordRules");
    if (code === "CodeMismatchException" || code === "EnableSoftwareTokenMFAException") return t("auth.wrongCode");
    if (code === "ExpiredCodeException") return t("auth.expiredCode");
    if (code === "LimitExceededException" || code === "TooManyRequestsException") return t("auth.tooMany");
    return (error && error.message) || t("failed");
  }

  function loginCard(children, onsubmit) {
    root.replaceChildren(el("main", { class: "login" }, el("form", {
      class: "login-card",
      onsubmit: (event) => {
        event.preventDefault();
        onsubmit();
      },
    }, children)));
  }

  function signInCallbacks(user, error) {
    return {
      onSuccess: () => boot(),
      onFailure: (failure) => { error.textContent = authMessage(failure); },
      newPasswordRequired: () => showNewPassword(user),
      totpRequired: () => showTotp(user),
    };
  }

  // The language menu of the sign-in page and of the settings.
  function languagePicker(onchange) {
    return el("select", { class: "lang-select", "aria-label": t("language.title"), onchange: (event) => onchange(event.target.value) },
      i18n.languages.map(([code, name]) => el("option", { value: code, selected: code === language }, name)));
  }

  // In the public demo (config.demo) the sign-in is shown and filled in.
  function showLogin(message) {
    const demo = config.demo || {};
    const error = el("p", { class: "error", role: "alert" }, message || "");
    const email = el("input", { type: "email", autocomplete: "username", required: true, value: demo.email || "" });
    const password = el("input", { type: "password", autocomplete: "current-password", required: true, value: demo.password || "" });
    loginCard([
      el("img", { class: "login-mark", src: "/favicon.svg", alt: "" }),
      el("h1", {}, t("brand")),
      demo.email ? el("p", { class: "demo-note" }, [
        t("demo.signIn", { email: demo.email, password: demo.password }),
        demo.reset ? " " + t("demo.reset") : "",
      ]) : null,
      config.ssoProvider ? el("button", { type: "button", class: "primary", onclick: startSso }, t("login.sso")) : null,
      config.ssoProvider ? el("p", { class: "login-or" }, t("login.or")) : null,
      el("label", {}, [t("login.email"), email]),
      el("label", {}, [t("login.password"), password]),
      el("button", { class: config.ssoProvider ? "ghost" : "primary", type: "submit" }, t("login.signIn")),
      error,
      el("div", { class: "login-lang" }, [
        REPOSITORY ? el("a", { href: REPOSITORY, rel: "noopener" }, t("login.source")) : null,
        languagePicker((code) => {
          setLanguage(code);
          showLogin();
        }),
      ]),
    ], () => {
      error.textContent = "";
      const user = new window.AmazonCognitoIdentity.CognitoUser({ Username: email.value.trim(), Pool: userPool() });
      user.authenticateUser(new window.AmazonCognitoIdentity.AuthenticationDetails({
        Username: email.value.trim(),
        Password: password.value,
      }), signInCallbacks(user, error));
    });
    if (!config.ssoProvider) email.focus();
  }

  // AWS SSO ------------------------------------------------------------------
  // The sign-in domain of the user pool sends the user straight on to IAM
  // Identity Center (identity_provider) and back here with a code, which the
  // page trades for the tokens itself (PKCE, no client secret). The tokens go
  // where the Cognito library keeps those of a password sign-in, so that the
  // session, its refresh and the sign-out work the same way.
  const SSO_KEY = "aws-sso";

  function base64url(bytes) {
    return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function randomText() {
    return base64url(crypto.getRandomValues(new Uint8Array(32)));
  }

  function returnUrl() {
    return location.origin + "/";
  }

  async function startSso() {
    const verifier = randomText();
    const stateValue = randomText();
    const challenge = base64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    try {
      sessionStorage.setItem(SSO_KEY, JSON.stringify({ verifier, state: stateValue, hash: location.hash }));
    } catch (error) {
      // Without storage the answer cannot be checked; the sign-in fails then.
    }
    const params = new URLSearchParams({
      response_type: "code",
      client_id: config.clientId,
      redirect_uri: returnUrl(),
      identity_provider: config.ssoProvider,
      scope: "openid email profile aws.cognito.signin.user.admin",
      state: stateValue,
      code_challenge: challenge,
      code_challenge_method: "S256",
    });
    location.assign(`https://${config.authDomain}/oauth2/authorize?${params}`);
  }

  // The answer of the sign-in domain: ?code=…&state=…, or ?error=…. Returns
  // an error to show, or nothing.
  async function finishSso(query) {
    let saved = null;
    try {
      saved = JSON.parse(sessionStorage.getItem(SSO_KEY) || "null");
      sessionStorage.removeItem(SSO_KEY);
    } catch (error) {
      saved = null;
    }
    history.replaceState(null, "", "/" + ((saved && saved.hash) || ""));
    if (query.get("error")) return query.get("error_description") || query.get("error");
    if (!saved || saved.state !== query.get("state")) return t("login.ssoState");
    const response = await fetch(`https://${config.authDomain}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: config.clientId,
        code: query.get("code"),
        redirect_uri: returnUrl(),
        code_verifier: saved.verifier,
      }),
    });
    const tokens = await response.json().catch(() => ({}));
    if (!response.ok) return tokens.error || t("failed");
    const Cognito = window.AmazonCognitoIdentity;
    const session = new Cognito.CognitoUserSession({
      IdToken: new Cognito.CognitoIdToken({ IdToken: tokens.id_token }),
      AccessToken: new Cognito.CognitoAccessToken({ AccessToken: tokens.access_token }),
      RefreshToken: new Cognito.CognitoRefreshToken({ RefreshToken: tokens.refresh_token }),
    });
    const user = new Cognito.CognitoUser({ Username: session.getAccessToken().payload.username, Pool: userPool() });
    user.setSignInUserSession(session);
    return null;
  }

  // Whether the user of this session signed in through AWS SSO: their
  // password and two-factor sign-in are those of IAM Identity Center.
  async function viaSso() {
    try {
      const identities = (await currentSession()).getIdToken().payload.identities || [];
      return identities.some((item) => item.providerName === config.ssoProvider);
    } catch (error) {
      return false;
    }
  }

  function showNewPassword(user) {
    const error = el("p", { class: "error", role: "alert" });
    const password = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: "12" });
    loginCard([
      el("h1", {}, t("newPassword.title")),
      el("p", { class: "lede" }, t("newPassword.hint")),
      el("label", {}, [t("password.new"), password]),
      el("button", { class: "primary", type: "submit" }, t("newPassword.submit")),
      error,
    ], () => user.completeNewPasswordChallenge(password.value, {}, signInCallbacks(user, error)));
    password.focus();
  }

  // The second step when two-factor sign-in is on: the code of the
  // authenticator app.
  function showTotp(user) {
    const error = el("p", { class: "error", role: "alert" });
    const code = el("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9]{6}", maxlength: "6", required: true });
    loginCard([
      el("h1", {}, t("totp.title")),
      el("p", { class: "lede" }, t("totp.hint")),
      el("label", {}, [t("totp.code"), code]),
      el("button", { class: "primary", type: "submit" }, t("login.signIn")),
      error,
    ], () => {
      error.textContent = "";
      user.sendMFACode(code.value.trim(), signInCallbacks(user, error), "SOFTWARE_TOKEN_MFA");
    });
    code.focus();
  }

  function signOut() {
    const user = pool && pool.getCurrentUser();
    if (user) user.signOut();
    document.querySelectorAll("dialog.modal").forEach((node) => node.close());
    document.querySelector(".dialog-wrap")?.remove();
    showLogin();
  }

  async function api(path, options) {
    const session = await currentSession().catch(() => null);
    if (!session) {
      showLogin(t("sessionEnded"));
      throw new Error(t("sessionEnded"));
    }
    const response = await fetch(config.apiBase + path, {
      method: (options && options.method) || "GET",
      headers: {
        Authorization: "Bearer " + session.getIdToken().getJwtToken(),
        ...(options && options.body ? { "Content-Type": "application/json" } : {}),
      },
      body: options && options.body ? JSON.stringify(options.body) : undefined,
    });
    let data = {};
    const text = await response.text();
    if (text) {
      try { data = JSON.parse(text); } catch (error) { data = { error: text }; }
    }
    if (response.status === 401) {
      signOut();
      throw new Error(t("sessionEnded"));
    }
    if (!response.ok) {
      // The API names the error with a code; its own text is the English one.
      const translated = data.code && i18n.strings[i18n.fallback]["api." + data.code];
      const message = translated ? t("api." + data.code) : data.error || t("requestFailed");
      throw new Error(data.detail ? message + ": " + data.detail : message);
    }
    return data;
  }

  // Navigation -------------------------------------------------------------
  // The address keeps the view and its month or day: #costs/2026-10,
  // #trail/2026-10-17, #findings.

  function readHash() {
    const [view, arg] = location.hash.replace(/^#/, "").split("/");
    state.view = VIEWS.includes(view) ? view : "overview";
    if (state.view === "costs" && /^\d{4}-\d{2}$/.test(arg || "")) state.period = arg;
    if (state.view === "trail") state.day = /^\d{4}-\d{2}-\d{2}$/.test(arg || "") ? arg : state.day || today();
    if (state.view === "traffic") state.trafficDay = /^\d{4}-\d{2}-\d{2}$/.test(arg || "") ? arg : state.trafficDay || today();
  }

  function go(view, arg) {
    const hash = "#" + view + (arg ? "/" + arg : "");
    if (location.hash === hash) show();
    else location.hash = hash;
  }

  async function show() {
    readHash();
    render();
    try {
      if (state.view === "overview") state.overview = await api("/overview");
      else if (state.view === "costs") await loadCosts();
      else if (state.view === "trail") await loadTrail();
      else if (state.view === "traffic") await loadTraffic();
      else await loadFindings(true);
    } catch (error) {
      toast(error.message);
    }
    render();
  }

  // Page -------------------------------------------------------------------

  function render() {
    const nav = el("nav", { class: "seg views", "aria-label": t("nav.label") }, VIEWS.map((view) => el("button", {
      type: "button",
      class: view === state.view ? "active" : "",
      "aria-current": view === state.view ? "page" : null,
      onclick: () => go(view),
    }, t("nav." + view))));
    const body = state.view === "costs" ? costsView() : state.view === "trail" ? trailView() : state.view === "traffic" ? trafficView()
      : state.view === "findings" ? findingsView() : overviewView();
    root.replaceChildren(el("div", { class: "app" }, [
      el("header", { class: "top" }, [
        el("div", { class: "brand" }, [el("img", { class: "brand-mark", src: "/favicon.svg", alt: "" }), el("strong", {}, t("brand")),
          el("span", {}, state.me.account ? t("brand.account", { account: state.me.account }) : location.hostname)]),
        nav,
        el("div", { class: "who" }, [
          el("span", { class: "who-email", title: state.me.email }, state.me.email),
          el("button", { type: "button", class: "icon-button", onclick: () => showAccount() }, t("menu.account")),
          el("button", { type: "button", class: "icon-button", onclick: signOut }, t("menu.signOut")),
        ]),
      ]),
      el("main", { class: "content" }, body),
    ]));
  }

  function loading() {
    return el("p", { class: "muted" }, t("loading"));
  }

  function tile(label, value, sub, tone) {
    return el("div", { class: "tile" + (tone ? " " + tone : "") }, [
      el("span", { class: "tile-label" }, label),
      el("strong", { class: "tile-value" }, value),
      sub ? el("span", { class: "tile-sub" }, sub) : null,
    ]);
  }

  function section(title, children, actions) {
    return el("section", { class: "panel" }, [
      el("div", { class: "panel-head" }, [el("h2", {}, title), actions || null]),
      ...[].concat(children),
    ]);
  }

  // The amounts of a map as rows with a bar of their share, biggest first.
  // With pick, a row is a button: pick(key) gets the key of the row, and
  // picked(key) marks the chosen one.
  function shareTable(values, unit, nameOf, limit, pick, picked) {
    const rows = Object.entries(values || {}).map(([key, value]) => [key, Number(value)]).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    const total = rows.reduce((all, [, value]) => all + Math.max(0, value), 0);
    if (!rows.length) return el("p", { class: "muted" }, t("none"));
    const attrs = (key) => (pick && key !== "other" ? {
      class: picked && picked(key) ? "pickable picked" : "pickable",
      tabindex: "0",
      role: "button",
      title: t("trail.pick"),
      onclick: () => pick(key),
      onkeydown: (event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), pick(key)),
    } : {});
    return el("table", { class: "data" }, el("tbody", {}, rows.slice(0, limit || 50).map(([key, value]) => el("tr", attrs(key), [
      el("td", { class: "name" }, nameOf ? nameOf(key) : key),
      el("td", { class: "share" }, el("span", { class: "share-bar", style: `width:${total > 0 ? Math.max(0, (value / total) * 100) : 0}%` })),
      el("td", { class: "amount" }, unit === null ? number(value) : money(value, unit)),
    ]))));
  }

  // Columns of a month's usage by day, the biggest services in colour and the
  // rest in "other".
  function costChart(month, until) {
    const period = month.period;
    const first = period + "-01";
    const last = addDays(addDays(first, 32).slice(0, 7) + "-01", -1);
    const end = until && until < last ? until : last;
    const labels = [];
    for (let day = first; day <= end; day = addDays(day, 1)) labels.push(day);
    const byDay = month.day_services || {};
    const totals = {};
    Object.values(byDay).forEach((services) => Object.entries(services).forEach(([name, value]) => {
      totals[name] = (totals[name] || 0) + Number(value);
    }));
    const { kept, folded } = charts.topKeys(totals);
    const series = kept.map((key) => ({ key, label: serviceName(key) }));
    if (folded.length) series.push({ key: "__other", label: t("chart.other"), other: true });
    const unit = month.currency || "USD";
    return charts.columns({
      labels,
      series: series.length ? series : [{ key: "__none", label: "" }],
      value: (day, key) => {
        const services = byDay[day] || {};
        if (key === "__other") return folded.reduce((all, name) => all + Number(services[name] || 0), 0);
        return Number(services[key] || 0);
      },
      format: (amount) => money(amount, unit),
      tick: (day) => String(Number(day.slice(8))),
      title: (day) => dayName(day),
      totalLabel: t("chart.total"),
      label: t("costs.chart", { month: monthName(period) }),
      key: "costs",
      legendTitle: t("chart.legendHint"),
    });
  }

  function trailChart(days, first, last, summary) {
    const byDay = Object.fromEntries((days || []).map((item) => [item.day, item]));
    const labels = [];
    for (let day = first; day <= last; day = addDays(day, 1)) labels.push(day);
    // With a filter, the calls it keeps by day and the rest; else the
    // changes and the reads. A day before the combinations has no data.
    const found = summary && ((day) => (summary.days[day] ? Number(summary.days[day].events || 0) : null));
    return charts.columns({
      labels,
      series: found
        ? [{ key: "found", label: filterLabel() }, { key: "rest", label: t("trail.otherCalls"), other: true }]
        : [{ key: "writes", label: t("trail.changes") }, { key: "reads", label: t("trail.reads") }],
      value: (day, key) => {
        const item = byDay[day] || {};
        const events = Number(item.events || 0);
        if (key === "found") return found(day) || 0;
        if (key === "rest") return found(day) === null ? 0 : Math.max(0, events - found(day));
        if (key === "writes") return Number(item.writes || 0);
        return Math.max(0, events - Number(item.writes || 0));
      },
      format: (amount) => number(Math.round(amount)),
      tick: (day) => dayName(day, "short"),
      title: (day) => dayName(day),
      extra: (day) => (found && found(day) === null ? t("trail.noCombos")
        : byDay[day] ? t("trail.errorsSignins", { errors: number(byDay[day].errors), signins: number(byDay[day].signins) }) : null),
      totalLabel: t("chart.total"),
      label: t("trail.chart"),
      height: 160,
      key: found ? "trail-found" : "trail",
      legendTitle: t("chart.legendHint"),
    });
  }

  // The filter in a few words, for the legend: the picked values, the text
  // and the switches.
  function filterLabel() {
    const filter = state.eventFilter;
    const parts = Object.values(filter.fields);
    if (filter.text.trim()) parts.push(`«${filter.text.trim()}»`);
    if (filter.writes) parts.push(t("trail.onlyChanges"));
    if (filter.errors) parts.push(t("trail.onlyErrors"));
    return t("trail.found", { value: parts.join(" · ") });
  }



  // "AmazonEC2" reads better as "EC2".
  function serviceName(code) {
    return String(code).replace(/^(Amazon|AWS)(?=[A-Z])/, "") || code;
  }

  // Overview --------------------------------------------------------------

  function forecastOf(data) {
    const monthly = ((data.budgets && data.budgets.budgets) || []).filter((budget) => budget.time_unit === "MONTHLY");
    if (monthly.length && monthly[0].forecast) return { amount: monthly[0].forecast, from: "budget" };
    const month = data.month;
    if (!month) return null;
    const used = Object.keys(month.usage_days || {}).length;
    if (!used) return null;
    const days = new Date(Date.UTC(Number(data.period.slice(0, 4)), Number(data.period.slice(5)), 0)).getUTCDate();
    return { amount: (Number(month.total || 0) / used) * days, from: "estimate" };
  }

  function overviewView() {
    const data = state.overview;
    if (!data) return loading();
    const month = data.month;
    const unit = (month && month.currency) || "USD";
    const usage = (month && month.usage_days) || {};
    const lastDay = Object.keys(usage).sort().pop();
    const forecast = forecastOf(data);
    const findings = data.findings;
    const parts = [
      el("div", { class: "tiles" }, [
        tile(t("overview.month"), month ? money(month.total, unit) : "—",
          data.previous ? t("overview.previous", { amount: money(data.previous.total, unit) }) : null),
        tile(t("overview.forecast"), forecast ? money(forecast.amount, unit) : "—", forecast ? t("overview.forecastFrom." + forecast.from) : null),
        tile(t("overview.lastDay"), lastDay ? money(usage[lastDay], unit) : "—", lastDay ? dayName(lastDay) : null),
        tile(t("overview.findings"), number(findings.critical + findings.warning),
          t("overview.findingsSplit", { critical: findings.critical, warning: findings.warning }), findings.critical ? "bad" : ""),
      ]),
    ];
    parts.push(budgetsPanel(data.budgets));
    if (month) {
      parts.push(section(t("overview.daily", { month: monthName(month.period) }), [costChart(month, data.today)],
        el("button", { type: "button", class: "ghost", onclick: () => go("costs", month.period) }, t("overview.allCosts"))));
    } else {
      parts.push(section(t("nav.costs"), el("p", { class: "muted" }, t("costs.noReport"))));
    }
    const first = addDays(data.today, -13);
    parts.push(el("div", { class: "columns" }, [
      month ? section(t("costs.services"), shareTable(month.services, unit, serviceName, 8)) : null,
      section(t("overview.trail"), [trailChart(data.trail, first, data.today)],
        el("button", { type: "button", class: "ghost", onclick: () => go("trail", data.today) }, t("overview.allTrail"))),
    ]));
    parts.push(section(t("overview.latest"), findings.latest.length
      ? el("ul", { class: "findings" }, findings.latest.map(findingRow))
      : el("p", { class: "muted" }, t("findings.none")),
    el("button", { type: "button", class: "ghost", onclick: () => go("findings") }, t("overview.allFindings"))));
    return parts;
  }

  function budgetsPanel(snapshot) {
    const budgets = (snapshot && snapshot.budgets) || [];
    if (!budgets.length) return section(t("budgets.title"), el("p", { class: "muted" }, t("budgets.none")));
    return section(t("budgets.title"), [
      el("ul", { class: "budgets" }, budgets.map((budget) => {
        const limit = Number(budget.limit) || 0;
        const share = limit ? Math.min(100, (Number(budget.actual) / limit) * 100) : 0;
        const forecastShare = limit ? Math.min(100, (Number(budget.forecast) / limit) * 100) : 0;
        const over = limit && Number(budget.actual) > limit;
        const overForecast = limit && Number(budget.forecast) > limit;
        return el("li", {}, [
          el("div", { class: "budget-head" }, [
            el("strong", {}, budget.name),
            el("span", { class: "muted small" }, t("budgets.unit." + (budget.time_unit || "MONTHLY"))),
            over ? el("span", { class: "badge bad" }, t("budgets.over")) : overForecast ? el("span", { class: "badge warn" }, t("budgets.overForecast")) : null,
          ]),
          el("div", { class: "meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": String(limit), "aria-valuenow": String(budget.actual) }, [
            el("span", { class: "meter-fill" + (over ? " bad" : ""), style: `width:${share}%` }),
            budget.forecast ? el("span", { class: "meter-mark", style: `left:${forecastShare}%`, title: t("budgets.forecast") }) : null,
          ]),
          el("div", { class: "small" }, t("budgets.line", {
            actual: money(budget.actual, budget.unit), limit: money(limit, budget.unit), forecast: money(budget.forecast, budget.unit),
          })),
        ]);
      })),
      el("p", { class: "hint" }, t("budgets.updated", { date: when(snapshot.updated) })),
    ]);
  }

  // Costs -------------------------------------------------------------------

  async function loadCosts() {
    state.periods = (await api("/costs")).periods || [];
    if (!state.period) state.period = state.periods[state.periods.length - 1] || today().slice(0, 7);
    const [month, recommendations] = await Promise.all([api("/costs/" + state.period), api("/recommendations")]);
    state.month = month;
    state.recommendations = recommendations;
  }

  // What Cost Optimization Hub would change, the biggest savings first.
  function recommendationsPanel() {
    const data = state.recommendations;
    if (!data) return null;
    const items = data.items || [];
    const unit = data.currency || "USD";
    const yes = (value) => t(value ? "recs.yes" : "recs.no");
    return section(t("recs.title"), [
      el("p", { class: "hint" }, data.updated ? t("recs.hint", { count: number(data.count), savings: money(data.savings, unit), date: when(data.updated) }) : t("recs.none")),
      items.length ? el("div", { class: "table-wrap" }, el("table", { class: "data recs" }, [
        el("thead", {}, el("tr", {}, [t("recs.action"), t("recs.resource"), t("recs.change"), t("recs.savings"), t("recs.effort"), t("recs.restart")].map((text) => el("th", {}, text)))),
        el("tbody", {}, items.slice(0, 50).map((item) => el("tr", {}, [
          el("td", {}, [t("recs.actionType." + item.action) === "recs.actionType." + item.action ? item.action : t("recs.actionType." + item.action),
            el("div", { class: "muted small" }, item.type)]),
          el("td", {}, [el("span", { class: "mono" }, item.resource || "—"), el("div", { class: "muted small" }, [item.region, item.project].filter(Boolean).join(" · "))]),
          el("td", {}, [item.current || "—", " → ", item.recommended || "—"]),
          el("td", { class: "amount" }, [money(item.savings, item.currency || unit), item.percent ? el("div", { class: "muted small" }, `${item.percent}%`) : null]),
          el("td", {}, item.effort || "—"),
          el("td", {}, yes(item.restart)),
        ]))),
      ])) : null,
    ]);
  }

  function costsView() {
    const month = state.month;
    const picker = el("select", { "aria-label": t("costs.month"), onchange: (event) => go("costs", event.target.value) },
      (state.periods.length ? state.periods : [state.period]).slice().reverse().map((period) => el("option", { value: period, selected: period === state.period }, monthName(period))));
    const refresh = state.me.admin && !state.me.demo ? el("button", {
      type: "button",
      class: "ghost",
      title: t("costs.refreshHint"),
      onclick: async () => {
        try {
          await api("/admin/refresh", { method: "POST" });
          toast(t("costs.refreshStarted"));
        } catch (error) {
          toast(error.message);
        }
      },
    }, t("costs.refresh")) : null;
    const head = el("div", { class: "toolbar" }, [picker, refresh, month && !month.empty ? el("span", { class: "muted small" }, t("costs.updated", { date: when(month.updated) })) : null]);
    if (!month || state.month.period !== state.period) return [head, loading()];
    if (month.empty) return [head, el("p", { class: "muted panel" }, t("costs.noReport")), recommendationsPanel()];
    const unit = month.currency || "USD";
    const kinds = month.kinds || {};
    const credits = Number(kinds.Credit || 0) + Number(kinds.Refund || 0);
    return [
      head,
      el("div", { class: "tiles" }, [
        tile(t("costs.total"), money(month.total, unit)),
        tile(t("costs.usage"), money(sum(month.usage_days), unit)),
        tile(t("costs.tax"), money(kinds.Tax || 0, unit)),
        tile(t("costs.credits"), money(credits, unit)),
      ]),
      section(t("costs.byDay"), costChart(month)),
      el("div", { class: "columns" }, [
        section(t("costs.services"), shareTable(month.services, unit, serviceName)),
        section(t("costs.projects"), [shareTable(month.projects, unit, (key) => (key && key !== "(untagged)" ? key : t("costs.untagged"))), el("p", { class: "hint" }, t("costs.projectsHint"))]),
        section(t("costs.regions"), shareTable(month.regions, unit, (key) => (key === "global" ? t("costs.global") : key))),
        section(t("costs.kinds"), shareTable(month.kinds, unit, (key) => t("kind." + key) === "kind." + key ? key : t("kind." + key))),
      ]),
      section(t("costs.usageTypes"), shareTable(month.usage, unit)),
      recommendationsPanel(),
    ];
  }

  // CloudTrail --------------------------------------------------------------

  async function loadTrail() {
    const first = addDays(state.day, -13);
    const [days, events, summary] = await Promise.all([
      api(`/trail/days?from=${first}&to=${state.day}`),
      api("/trail/events?" + eventQuery()),
      loadSummary(),
    ]);
    state.days = days.items || [];
    state.events = events.items || [];
    state.eventsCursor = events.next_cursor;
    state.summary = summary;
  }

  function filtering() {
    const filter = state.eventFilter;
    return Boolean(Object.keys(filter.fields).length || filter.text.trim() || filter.errors || filter.writes);
  }

  function filterParams() {
    const filter = state.eventFilter;
    const params = new URLSearchParams();
    if (filter.errors) params.set("errors", "1");
    if (filter.writes) params.set("writes", "1");
    if (filter.text.trim()) params.set("q", filter.text.trim());
    Object.entries(filter.fields).forEach(([name, value]) => params.set(name, value));
    return params;
  }

  async function loadSummary() {
    if (!filtering()) return null;
    const params = filterParams();
    params.set("from", addDays(state.day, -13));
    params.set("to", state.day);
    params.set("day", state.day);
    return api("/trail/summary?" + params);
  }

  function eventQuery(cursor) {
    const params = filterParams();
    params.set("day", state.day);
    if (cursor) params.set("cursor", cursor);
    return params.toString();
  }

  async function moreEvents() {
    try {
      const page = await api("/trail/events?" + eventQuery(state.eventsCursor));
      state.events = state.events.concat(page.items || []);
      state.eventsCursor = page.next_cursor;
    } catch (error) {
      toast(error.message);
    }
    render();
  }

  async function reloadEvents() {
    try {
      const [page, summary] = await Promise.all([api("/trail/events?" + eventQuery()), loadSummary()]);
      state.events = page.items || [];
      state.eventsCursor = page.next_cursor;
      state.summary = summary;
    } catch (error) {
      toast(error.message);
    }
    // The search box keeps the focus and the caret through the redraw.
    const active = document.activeElement;
    const typingHere = active && active.matches(".filters input[type=search]");
    const caret = typingHere ? active.selectionStart : 0;
    render();
    if (typingHere) {
      const again = document.querySelector(".filters input[type=search]");
      again.focus();
      again.setSelectionRange(caret, caret);
    }
  }

  // Typing waits a moment before the server is asked.
  let typing = null;
  function reloadSoon() {
    clearTimeout(typing);
    typing = setTimeout(reloadEvents, 400);
  }

  // The fields of the events a row of a list stands for: "s3:PutObject" of
  // the changes is a service and an event.
  const LIST_FIELDS = {
    principals: (key) => ({ principal: key }),
    sources: (key) => ({ source: key }),
    changes: (key) => {
      const [source, ...name] = key.split(":");
      return { source, name: name.join(":") };
    },
    error_codes: (key) => ({ error: key }),
    regions: (key) => ({ region: key }),
  };

  // Whether the filter already holds these values.
  function picked(fields) {
    return Object.entries(fields).every(([name, value]) => state.eventFilter.fields[name] === value);
  }

  // A click on a value adds it to the filter, next to those picked before
  // (a principal and a service, say); a second click on it takes it back.
  function pickEvents(fields) {
    const next = { ...state.eventFilter.fields };
    if (picked(fields)) Object.keys(fields).forEach((name) => delete next[name]);
    else Object.assign(next, fields);
    state.eventFilter.fields = next;
    reloadEvents();
  }

  function topList(title, values, list) {
    const toFields = LIST_FIELDS[list];
    return section(title, shareTable(values, null, null, 8, (key) => pickEvents(toFields(key)), (key) => picked(toFields(key))));
  }

  // A value in the table of events that narrows the list to it; the row
  // itself still opens the event.
  function pickCell(name, value, className) {
    if (!value) return "";
    return el("button", {
      type: "button",
      class: "cell-pick" + (className ? " " + className : ""),
      title: t("trail.pick"),
      onclick: (event) => {
        event.stopPropagation();
        pickEvents({ [name]: value });
      },
    }, value);
  }

  function sortEvents(events) {
    const { key, desc } = state.eventSort;
    const sorted = events.slice().sort((a, b) => String(a[key] || "").localeCompare(String(b[key] || ""), language, { numeric: true })
      || String(a.time).localeCompare(String(b.time)));
    return desc ? sorted.reverse() : sorted;
  }

  function sortHeader(key, label) {
    const active = state.eventSort.key === key;
    return el("th", { "aria-sort": active ? (state.eventSort.desc ? "descending" : "ascending") : "none" }, el("button", {
      type: "button",
      class: "sort" + (active ? " active" : ""),
      title: t("trail.sortBy", { field: label }),
      onclick: () => {
        state.eventSort = { key, desc: active ? !state.eventSort.desc : key === "time" };
        render();
      },
    }, [label, active ? (state.eventSort.desc ? " ↓" : " ↑") : ""]));
  }

  const FIELD_LABELS = { principal: "event.principal", source: "event.service", name: "event.name", region: "event.region", ip: "event.ip", error: "event.error" };

  function fieldChips() {
    const fields = Object.entries(state.eventFilter.fields);
    if (!filtering()) return null;
    return el("div", { class: "chips" }, [
      ...fields.map(([name, value]) => el("span", { class: "chip" }, [
        el("span", { class: "muted" }, t(FIELD_LABELS[name]) + ": "),
        el("span", { class: "mono" }, value),
        el("button", {
          type: "button",
          class: "chip-remove",
          "aria-label": t("trail.removeFilter", { field: t(FIELD_LABELS[name]) }),
          onclick: () => {
            const next = { ...state.eventFilter.fields };
            delete next[name];
            state.eventFilter.fields = next;
            reloadEvents();
          },
        }, "×"),
      ])),
      el("button", {
        type: "button",
        class: "link-button small",
        onclick: () => {
          state.eventFilter = { ...state.eventFilter, text: "", errors: false, writes: false, fields: {} };
          reloadEvents();
        },
      }, t("trail.clearFilter")),
    ]);
  }

  function trailView() {
    const day = state.day;
    const counters = state.days.find((item) => item.day === day) || {};
    const head = el("div", { class: "toolbar" }, [
      el("button", { type: "button", class: "ghost", "aria-label": t("trail.previous"), onclick: () => go("trail", addDays(day, -1)) }, "←"),
      el("input", { type: "date", value: day, max: today(), "aria-label": t("trail.day"), onchange: (event) => event.target.value && go("trail", event.target.value) }),
      el("button", { type: "button", class: "ghost", "aria-label": t("trail.next"), disabled: day >= today(), onclick: () => go("trail", addDays(day, 1)) }, "→"),
      el("span", { class: "muted small" }, t("trail.utc")),
    ]);
    const filter = state.eventFilter;
    // With a filter the numbers of the day are those it keeps.
    const summary = state.summary;
    const numbers = summary ? summary.day : counters;
    const shown = state.events;
    const search = el("input", {
      type: "search",
      value: filter.text,
      placeholder: t("trail.filter"),
      "aria-label": t("trail.filter"),
      oninput: (event) => {
        filter.text = event.target.value;
        reloadSoon();
      },
    });
    const check = (key, label) => el("label", { class: "check" }, [el("input", {
      type: "checkbox",
      checked: filter[key],
      onchange: (event) => {
        filter[key] = event.target.checked;
        reloadEvents();
      },
    }), label]);
    const value = (key) => (numbers ? number(numbers[key]) : "—");
    const sub = summary ? t(numbers ? "trail.filtered" : "trail.noCombos") : null;
    return [
      head,
      el("div", { class: "panel filters" }, [
        el("div", { class: "events-tools" }, [search, check("writes", t("trail.onlyChanges")), check("errors", t("trail.onlyErrors"))]),
        fieldChips(),
        el("p", { class: "hint" }, t("trail.filterHint")),
      ]),
      el("div", { class: "tiles" }, [
        tile(t("trail.events"), value("events"), sub, summary ? "picked" : ""),
        tile(t("trail.changes"), value("writes"), sub, summary ? "picked" : ""),
        tile(t("trail.errors"), value("errors"), sub, summary ? "picked" : numbers && numbers.errors ? "warn" : ""),
        tile(t("trail.signins"), value("signins"), sub, summary ? "picked" : ""),
      ]),
      section(t("trail.chart"), trailChart(state.days, addDays(day, -13), day, summary)),
      el("div", { class: "columns" }, [
        topList(t("trail.principals"), numbers && numbers.principals, "principals"),
        topList(t("trail.sources"), numbers && numbers.sources, "sources"),
        topList(t("trail.changesBy"), numbers && numbers.changes, "changes"),
        topList(t("trail.errorCodes"), numbers && numbers.error_codes, "error_codes"),
        topList(t("trail.regions"), numbers && numbers.regions, "regions"),
      ]),
      el("section", { class: "panel", id: "events" }, [
        el("div", { class: "panel-head" }, el("h2", {}, t("trail.list"))),
        el("p", { class: "hint" }, t("trail.listHint")),
        shown.length ? el("div", { class: "table-wrap" }, el("table", { class: "data events" }, [
          el("thead", {}, el("tr", {}, [["time", t("event.time")], ["name", t("event.name")], ["principal", t("event.principal")],
            ["region", t("event.region")], ["ip", t("event.ip")], ["error", t("event.error")]].map(([key, label]) => sortHeader(key, label)))),
          el("tbody", {}, sortEvents(shown).map((event) => el("tr", { tabindex: "0", class: event.error ? "failed" : "", onclick: () => showEvent(event), onkeydown: (key) => key.key === "Enter" && showEvent(event) }, [
            el("td", { class: "mono" }, clock(event.time)),
            el("td", {}, [pickCell("name", event.name, "mono"), " ", pickCell("source", event.source, "muted small")]),
            el("td", { class: "principal" }, pickCell("principal", event.principal)),
            el("td", {}, pickCell("region", event.region)),
            el("td", {}, pickCell("ip", event.ip, "mono")),
            el("td", {}, pickCell("error", event.error, "status-bad")),
          ]))),
        ])) : el("p", { class: "muted" }, t(filtering() ? "trail.noEventsFiltered" : "trail.noEvents")),
        state.eventsCursor ? el("div", { class: "actions" }, el("button", { type: "button", class: "ghost", onclick: moreEvents }, t("trail.more"))) : null,
      ]),
    ];
  }

  function pretty(text) {
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch (error) {
      return text;
    }
  }

  function showEvent(event) {
    const box = dialog(event.name);
    box.fill([
      facts([
        [t("event.time"), when(event.time)],
        [t("event.service"), event.source],
        [t("event.principal"), event.principal],
        [t("event.identity"), event.identity],
        ["ARN", event.arn ? el("span", { class: "mono" }, event.arn) : ""],
        [t("event.key"), event.key_id ? el("span", { class: "mono" }, event.key_id) : ""],
        [t("event.region"), event.region],
        [t("event.ip"), event.ip],
        [t("event.agent"), event.agent],
        [t("event.result"), event.result],
        [t("event.error"), event.error ? el("span", { class: "status-bad" }, event.error + (event.message ? ": " + event.message : "")) : ""],
        [t("event.readOnly"), t(event.read_only ? "yes" : "no")],
        [t("event.id"), el("span", { class: "mono" }, event.id)],
      ]),
      event.resources ? el("div", {}, [el("h3", {}, t("event.resources")), el("ul", { class: "plain mono small" }, event.resources.map((arn) => el("li", {}, arn)))]) : null,
      event.request ? el("div", {}, [el("h3", {}, t("event.request")), el("pre", { class: "code" }, pretty(event.request))]) : null,
      event.extra ? el("div", {}, [el("h3", {}, t("event.extra")), el("pre", { class: "code" }, pretty(event.extra))]) : null,
    ]);
  }

  // Traffic ----------------------------------------------------------------
  // The access logs of the sites (CloudFront) and of the buckets read without
  // it (S3), a source at a time: the day and the 14 days to it.

  async function loadTraffic() {
    state.trafficSources = (await api("/traffic")).sources || [];
    if (!state.trafficSources.some((item) => item.source === state.trafficSource)) {
      state.trafficSource = (state.trafficSources[0] || {}).source || "";
    }
    state.trafficCounts = null;
    if (!state.trafficSource) return;
    const source = encodeURIComponent(state.trafficSource);
    const [days, day] = await Promise.all([
      api(`/traffic/days?source=${source}&from=${addDays(state.trafficDay, -13)}&to=${state.trafficDay}`),
      api(`/traffic/day?source=${source}&day=${state.trafficDay}`),
    ]);
    state.trafficDays = days.days || {};
    state.trafficCounts = day.counts;
  }

  function sourceName(source) {
    const [kind, name] = source.split(/\/(.*)/);
    return t("traffic.kind." + kind, { name });
  }

  function bytes(value) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = Number(value || 0);
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return `${new Intl.NumberFormat(language, { maximumFractionDigits: unit ? 1 : 0 }).format(amount)} ${units[unit]}`;
  }

  function trafficChart(first, last) {
    const labels = [];
    for (let day = first; day <= last; day = addDays(day, 1)) labels.push(day);
    const days = state.trafficDays;
    return charts.columns({
      labels,
      series: [{ key: "ok", label: t("traffic.ok") }, { key: "s4xx", label: t("traffic.client") }, { key: "s5xx", label: t("traffic.server") }],
      value: (day, key) => {
        const item = days[day];
        if (!item) return 0;
        if (key === "ok") return Math.max(0, Number(item.requests) - Number(item.s4xx) - Number(item.s5xx));
        return Number(item[key] || 0);
      },
      format: (amount) => number(Math.round(amount)),
      tick: (day) => dayName(day, "short"),
      title: (day) => dayName(day),
      extra: (day) => (days[day] ? t("traffic.dataSent", { data: bytes(days[day].bytes) }) : t("traffic.noDay")),
      totalLabel: t("chart.total"),
      label: t("traffic.chart"),
      height: 160,
      key: "traffic",
      legendTitle: t("chart.legendHint"),
    });
  }

  function trafficView() {
    const day = state.trafficDay;
    const sources = state.trafficSources;
    if (sources === null) return [loading()];
    if (!sources.length) return [el("p", { class: "muted panel" }, t("traffic.none"))];
    const s3 = state.trafficSource.startsWith("s3/");
    const head = el("div", { class: "toolbar" }, [
      el("select", {
        "aria-label": t("traffic.source"),
        onchange: (event) => {
          state.trafficSource = event.target.value;
          show();
        },
      }, sources.map((item) => el("option", { value: item.source, selected: item.source === state.trafficSource }, sourceName(item.source)))),
      el("button", { type: "button", class: "ghost", "aria-label": t("trail.previous"), onclick: () => go("traffic", addDays(day, -1)) }, "←"),
      el("input", { type: "date", value: day, max: today(), "aria-label": t("trail.day"), onchange: (event) => event.target.value && go("traffic", event.target.value) }),
      el("button", { type: "button", class: "ghost", "aria-label": t("trail.next"), disabled: day >= today(), onclick: () => go("traffic", addDays(day, 1)) }, "→"),
      el("span", { class: "muted small" }, t("trail.utc")),
    ]);
    const counts = state.trafficCounts;
    const value = (key, format) => (counts ? (format || number)(counts[key]) : "—");
    const requests = counts ? Number(counts.requests) : 0;
    const list = (title, key, nameOf) => section(title, shareTable(counts && counts[key], null, nameOf, 10));
    return [
      head,
      el("p", { class: "hint" }, t(s3 ? "traffic.hintS3" : "traffic.hintCloudFront")),
      el("div", { class: "tiles" }, [
        tile(t("traffic.requests"), value("requests")),
        tile(t("traffic.data"), value("bytes", bytes)),
        tile(t("traffic.client"), value("s4xx"), null, counts && counts.s4xx ? "warn" : ""),
        tile(t("traffic.server"), value("s5xx"), null, counts && counts.s5xx ? "warn" : ""),
        s3 ? tile(t("traffic.avgTime"), requests ? `${number(Math.round(counts.time_ms / requests))} ms` : "—")
          : tile(t("traffic.hitRate"), requests ? `${Math.round((counts.hits / requests) * 100)}%` : "—"),
      ]),
      section(t("traffic.chart"), trafficChart(addDays(day, -13), day)),
      counts ? el("div", { class: "columns" }, [
        list(t(s3 ? "traffic.keys" : "traffic.paths"), "paths"),
        list(t("traffic.statuses"), "statuses"),
        list(t(s3 ? "traffic.operations" : "traffic.methods"), "methods"),
        s3 ? list(t("traffic.requesters"), "requesters") : list(t("traffic.results"), "results", (key) => t("traffic.result." + key) === "traffic.result." + key ? key : t("traffic.result." + key)),
        s3 ? list(t("traffic.errors"), "errors") : list(t("traffic.countries"), "countries"),
        list(t("traffic.ips"), "ips"),
        list(t("traffic.agents"), "agents"),
        list(t("traffic.referrers"), "referrers"),
        list(t("traffic.hours"), "hours", (key) => `${key}:00`),
      ]) : el("p", { class: "muted panel" }, t("traffic.noDay")),
    ];
  }

  // Findings --------------------------------------------------------------

  async function loadFindings(fresh) {
    const params = new URLSearchParams();
    if (state.severity) params.set("severity", state.severity);
    if (!fresh && state.findingsCursor) params.set("cursor", state.findingsCursor);
    const page = await api("/findings?" + params.toString());
    state.findings = (fresh ? [] : state.findings).concat(page.items || []);
    state.findingsCursor = page.next_cursor;
  }

  function findingDetail(item) {
    const details = item.details || {};
    const unit = details.unit || "USD";
    if (item.rule === "budget_exceeded" || item.rule === "budget_forecast") {
      return t("finding.budget", { budget: details.budget, actual: money(details.actual, unit), limit: money(details.limit, unit), forecast: money(details.forecast, unit) });
    }
    if (item.rule === "cost_spike") return t("finding.spike", { day: dayName(details.day), amount: money(details.amount), median: money(details.median) });
    if (item.rule === "new_service") return t("finding.service", { service: serviceName(details.service), amount: money(details.amount) });
    if (item.rule === "access_denied") return t("finding.denied", { principal: item.principal, day: dayName(item.day), count: details.threshold });
    if (item.rule === "insight") {
      return t("finding.insight", { api: `${item.event_source}:${item.event_name}`, type: t("insightType." + details.type),
        average: number(Math.round(Number(details.average || 0) * 100) / 100), baseline: number(Math.round(Number(details.baseline || 0) * 100) / 100) });
    }
    if (item.rule === "savings") {
      return t("finding.savings", { change: [details.current, details.recommended].filter(Boolean).join(" → "), resource: details.resource || details.type,
        amount: money(details.savings, details.currency || "USD") });
    }
    return [item.event_name, item.principal, item.ip, item.region].filter(Boolean).join(" · ");
  }

  function findingRow(item) {
    return el("li", { class: "finding" + (item.ack ? " done" : "") }, el("button", { type: "button", class: "finding-button", onclick: () => showFinding(item) }, [
      severityBadge(item.severity),
      el("span", { class: "finding-title" }, t("rule." + item.rule)),
      el("span", { class: "finding-detail" }, findingDetail(item)),
      el("span", { class: "finding-time muted small" }, [when(item.time), item.ack ? " · " + t("findings.acked") : ""]),
    ]));
  }

  function findingsView() {
    const filters = el("div", { class: "toolbar" }, [
      el("div", { class: "seg" }, [["", t("findings.all")], ["critical", t("severity.critical")], ["warning", t("severity.warning")]].map(([value, label]) => el("button", {
        type: "button",
        class: state.severity === value ? "active" : "",
        onclick: async () => {
          state.severity = value;
          try {
            await loadFindings(true);
          } catch (error) {
            toast(error.message);
          }
          render();
        },
      }, label))),
      el("label", { class: "check" }, [el("input", {
        type: "checkbox",
        checked: state.openOnly,
        onchange: (event) => {
          state.openOnly = event.target.checked;
          render();
        },
      }), t("findings.openOnly")]),
      el("button", { type: "button", class: "ghost", onclick: () => showRulesInfo() }, t("findings.rules")),
    ]);
    const items = state.findings.filter((item) => !state.openOnly || !item.ack);
    return [
      filters,
      section(t("nav.findings"), [
        items.length ? el("ul", { class: "findings" }, items.map(findingRow)) : el("p", { class: "muted" }, t("findings.none")),
        state.findingsCursor ? el("div", { class: "actions" }, el("button", {
          type: "button",
          class: "ghost",
          onclick: async () => {
            try {
              await loadFindings(false);
            } catch (error) {
              toast(error.message);
            }
            render();
          },
        }, t("findings.more"))) : null,
      ]),
    ];
  }

  function showFinding(item) {
    const box = dialog(t("rule." + item.rule));
    const draw = () => box.fill([
      el("p", {}, [severityBadge(item.severity), " ", t("ruleHint." + item.rule)]),
      facts([
        [t("finding.time"), when(item.time)],
        [t("finding.detail"), findingDetail(item)],
        [t("event.principal"), item.principal],
        [t("event.ip"), item.ip],
        [t("event.region"), item.region],
        [t("event.error"), item.error],
        [t("finding.state"), item.ack ? t("findings.ackedBy", { email: item.ack_by || "—", date: when(item.ack_at) }) : t("findings.open")],
      ]),
      el("div", { class: "dialog-actions start" }, [
        item.event_sk ? el("button", {
          type: "button",
          class: "ghost",
          onclick: async () => {
            try {
              const event = await api(`/trail/events/${item.day}/${encodeURIComponent(item.event_sk)}`);
              showEvent(event);
            } catch (error) {
              toast(error.message);
            }
          },
        }, t("finding.event")) : null,
        item.day && item.source === "trail" ? el("button", { type: "button", class: "ghost", onclick: () => { box.close(); go("trail", item.day); } }, t("finding.day")) : null,
        state.me.admin ? el("button", {
          type: "button",
          class: item.ack ? "ghost" : "primary",
          onclick: async () => {
            try {
              const saved = await api("/findings", { method: "PATCH", body: { sk: item.sk, ack: !item.ack } });
              Object.assign(item, saved);
              toast(t(saved.ack ? "findings.ackDone" : "findings.reopened"));
              draw();
              render();
            } catch (error) {
              toast(error.message);
            }
          },
        }, t(item.ack ? "findings.reopen" : "findings.ack")) : null,
      ]),
    ]);
    draw();
  }

  // What each rule looks for, for everyone; administrators change them in
  // the settings.
  function showRulesInfo() {
    const box = dialog(t("rules.title"));
    box.fill([
      el("p", { class: "hint" }, t("rules.intro")),
      ...["trail", "cost"].map((source) => el("div", {}, [
        el("h3", {}, t("rules.source." + source)),
        el("ul", { class: "rules" }, state.me.rules.filter((rule) => rule.source === source).map((rule) => el("li", {}, [
          severityBadge(rule.severity), " ", el("strong", {}, t("rule." + rule.id)), el("div", { class: "hint" }, t("ruleHint." + rule.id)),
        ]))),
      ])),
    ]);
  }

  // Settings ---------------------------------------------------------------

  async function showAccount(section) {
    const sso = await viaSso();
    // Visitors of the demo share one account: none may change its password or
    // turn on two-factor authentication for the others.
    const sections = sso ? [["sso", t("sso.title")]] : config.demo ? [] : [["password", t("password.title")], ["mfa", t("mfa.title")]];
    sections.push(["language", t("language.title")], ["mail", t("mail.title")]);
    // The demo reads no account, so it has nothing to load from S3.
    if (state.me.admin) sections.push(["rules", t("rules.title")], ...(state.me.demo ? [] : [["backfill", t("backfill.title")]]), ["users", t("users.title")]);
    sections.push(["version", t("version.title")]);
    let current = sections.some(([key]) => key === section) ? section : sections[0][0];
    const body = el("div", { class: "settings-body" });
    const tabs = el("div", { class: "tabs", role: "tablist" });
    const close = () => {
      wrap.remove();
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close();
    };
    const wrap = el("div", { class: "dialog-wrap", onclick: (event) => { if (event.target === wrap) close(); } }, [
      el("section", { class: "dialog wide", role: "dialog", "aria-modal": "true", "aria-label": t("account.title") }, [
        el("header", { class: "dialog-head" }, [
          el("h2", {}, t("account.title")),
          el("button", { type: "button", class: "icon-button", onclick: close }, t("close")),
        ]),
        tabs,
        body,
      ]),
    ]);
    function draw() {
      tabs.replaceChildren(...sections.map(([key, label]) => el("button", {
        type: "button",
        role: "tab",
        class: key === current ? "tab active" : "tab",
        "aria-selected": String(key === current),
        onclick: () => {
          current = key;
          draw();
        },
      }, label)));
      body.replaceChildren();
      if (current === "password") body.append(passwordForm());
      else if (current === "sso") body.append(el("p", {}, t("sso.text")));
      else if (current === "mfa") showMfa(body);
      else if (current === "mail") showMail(body);
      else if (current === "rules") showRules(body);
      else if (current === "backfill") showBackfill(body);
      else if (current === "users") showUsers(body);
      else if (current === "version") showVersion(body);
      else {
        body.append(languagePicker(async (code) => {
          await saveLanguage(code);
          // Everything on screen is redrawn in the new language.
          close();
          render();
          showAccount("language");
        }));
      }
    }
    document.querySelector(".dialog-wrap")?.remove();
    document.addEventListener("keydown", onKey);
    document.body.append(wrap);
    draw();
  }

  // Which mails the user gets: the daily digest and the alerts of critical
  // findings.
  function showMail(part) {
    const prefs = state.me.prefs;
    const box = (key) => el("label", { class: "check" }, [el("input", {
      type: "checkbox",
      checked: prefs[key],
      onchange: async (event) => {
        try {
          state.me.prefs = await api("/me/prefs", { method: "PUT", body: { [key]: event.target.checked } });
          toast(t("mail.saved"));
        } catch (error) {
          event.target.checked = !event.target.checked;
          toast(error.message);
        }
      },
    }), t("mail." + key)]);
    fill(part, [
      state.me.mail ? null : el("p", { class: "demo-note" }, t("mail.off")),
      el("p", { class: "muted small" }, t("mail.hint", { email: state.me.email })),
      box("digest"),
      box("alerts"),
    ]);
  }

  // Administrators: which rules are on, the principals no rule looks at and
  // the regions where changes are expected.
  async function showRules(part) {
    part.replaceChildren(loading());
    let data;
    try {
      data = await api("/rules");
    } catch (error) {
      part.replaceChildren(el("p", { class: "error" }, error.message));
      return;
    }
    const muted = new Set(data.settings.muted);
    const regions = el("input", { type: "text", value: data.settings.regions.join(", "), placeholder: "eu-central-1, us-east-1" });
    const ignored = el("textarea", { rows: "4", spellcheck: "false", placeholder: "role/AWSReservedSSO_*\nservice/*" }, data.settings.ignored.join("\n"));
    const save = el("button", { class: "primary", type: "submit" }, t("rules.save"));
    fill(part, [el("form", {
      class: "fields",
      onsubmit: async (event) => {
        event.preventDefault();
        save.disabled = true;
        try {
          await api("/admin/settings", {
            method: "PUT",
            body: {
              muted: [...muted],
              regions: regions.value.split(/[\s,]+/).filter(Boolean),
              ignored: ignored.value.split("\n").map((line) => line.trim()).filter(Boolean),
            },
          });
          toast(t("rules.saved"));
        } catch (error) {
          toast(error.message);
        }
        save.disabled = false;
      },
    }, [
      el("p", { class: "hint" }, t("rules.adminIntro")),
      ...["trail", "cost"].map((source) => el("fieldset", { class: "group" }, [
        el("legend", {}, t("rules.source." + source)),
        ...data.rules.filter((rule) => rule.source === source).map((rule) => el("label", { class: "check rule-check" }, [
          el("input", {
            type: "checkbox",
            checked: !muted.has(rule.id),
            onchange: (event) => (event.target.checked ? muted.delete(rule.id) : muted.add(rule.id)),
          }),
          severityBadge(rule.severity),
          el("span", {}, [t("rule." + rule.id), el("span", { class: "hint" }, " — " + t("ruleHint." + rule.id))]),
        ])),
      ])),
      el("label", {}, [t("rules.regions"), regions]),
      el("p", { class: "hint" }, t("rules.regionsHint")),
      el("label", {}, [t("rules.ignored"), ignored]),
      el("p", { class: "hint" }, t("rules.ignoredHint")),
      data.settings.updated_by ? el("p", { class: "hint" }, t("rules.updated", { email: data.settings.updated_by, date: when(data.settings.updated_at) })) : null,
      el("div", { class: "actions" }, [save]),
    ])]);
  }

  // Administrators: a period read from S3 again (lambda/backfill.py), the
  // logs of CloudTrail, the costs or both, kept for the days asked; the list
  // of the loads follows a running one every few seconds.
  function showBackfill(part) {
    const yesterday = addDays(today(), -1);
    const from = el("input", { type: "date", required: true, max: yesterday, value: addDays(yesterday, -29) });
    const to = el("input", { type: "date", required: true, max: yesterday, value: yesterday });
    const keep = el("input", { type: "number", required: true, min: "1", max: "3650", step: "1", value: "365" });
    const logs = el("input", { type: "checkbox", checked: true });
    const reports = el("input", { type: "checkbox" });
    const start = el("button", { class: "primary", type: "submit" }, t("backfill.start"));
    const list = el("div", { class: "table-wrap" }, loading());
    const intro = el("p", { class: "hint" });
    let timer = null;

    async function stop(job) {
      if (!window.confirm(t("backfill.stopConfirm"))) return;
      try {
        await api("/admin/backfill?id=" + encodeURIComponent(job.sk), { method: "DELETE" });
      } catch (error) {
        toast(error.message);
      }
      await draw();
    }

    function progress(job) {
      const parts = [];
      if (job.trail) parts.push(t("backfill.days", { count: job.days }) + " · " + t("backfill.events", { count: number(job.events) }));
      if (job.costs) parts.push(t("backfill.months", { count: job.months }));
      return parts.join(" · ");
    }

    async function draw() {
      clearTimeout(timer);
      let data;
      try {
        data = await api("/admin/backfill");
      } catch (error) {
        list.replaceChildren(el("p", { class: "error" }, error.message));
        return;
      }
      const jobs = data.items || [];
      intro.textContent = t("backfill.intro", { days: data.event_days });
      list.replaceChildren(jobs.length ? el("table", { class: "users" }, [
        el("thead", {}, el("tr", {}, [t("backfill.started"), t("backfill.period"), t("backfill.keepUntil"), t("backfill.progress"), t("backfill.status"), ""].map((text) => el("th", {}, text)))),
        el("tbody", {}, jobs.map((job) => el("tr", {}, [
          el("td", {}, [when(job.started), el("div", { class: "muted small" }, job.by)]),
          el("td", {}, [job.from + " — " + job.to, el("div", { class: "muted small" }, [job.trail ? "CloudTrail" : null, job.costs ? t("backfill.costs") : null].filter(Boolean).join(", "))]),
          el("td", {}, when(job.keep_until * 1000)),
          el("td", {}, [progress(job), job.status === "running" ? el("div", { class: "muted small" }, t("backfill.next", { day: job.next_day || job.next_month })) : null]),
          el("td", {}, [
            el("span", { class: "badge " + ({ done: "ok", running: "warn" }[job.status] || "bad") }, t("backfill.state." + job.status)),
            job.error ? el("div", { class: "muted small" }, job.error) : null,
          ]),
          el("td", { class: "row-actions" }, job.status === "running" ? el("button", { type: "button", onclick: () => stop(job) }, t("backfill.stop")) : null),
        ]))),
      ]) : el("p", { class: "muted" }, t("backfill.none")));
      start.disabled = jobs.some((job) => job.status === "running");
      // While a load runs and the tab is open, the list follows it.
      if (start.disabled) timer = setTimeout(() => list.isConnected && draw(), 5000);
    }

    fill(part, [
      el("form", {
        onsubmit: async (event) => {
          event.preventDefault();
          start.disabled = true;
          try {
            await api("/admin/backfill", {
              method: "POST",
              body: { from: from.value, to: to.value, keep_days: Number(keep.value), trail: logs.checked, costs: reports.checked },
            });
            toast(t("backfill.queued"));
          } catch (error) {
            toast(error.message);
          }
          await draw();
        },
      }, [
        intro,
        el("div", { class: "backfill-range" }, [
          el("label", {}, [t("backfill.from"), from]),
          el("label", {}, [t("backfill.to"), to]),
          el("label", {}, [t("backfill.keep"), keep]),
        ]),
        el("label", { class: "check" }, [logs, t("backfill.trail")]),
        el("label", { class: "check" }, [reports, t("backfill.reports")]),
        el("p", { class: "hint" }, t("backfill.hint")),
        el("div", { class: "actions" }, [start]),
      ]),
      list,
    ]);
    draw();
  }

  // Administrators: the accounts. A new user gets an invitation from Cognito;
  // the temporary password is also shown here once.
  function showUsers(part) {
    const note = el("div");
    const list = el("div", { class: "table-wrap" }, loading());
    const email = el("input", { type: "email", required: true, placeholder: "name@example.com", "aria-label": t("users.newEmail") });
    const admin = el("input", { type: "checkbox" });
    const invite = el("form", {
      class: "invite",
      onsubmit: async (event) => {
        event.preventDefault();
        try {
          const created = await api("/admin/users", { method: "POST", body: { email: email.value.trim(), admin: admin.checked } });
          note.replaceChildren(secretNote(created.email, created.temporary_password, true));
          email.value = "";
          admin.checked = false;
          await draw();
        } catch (error) {
          toast(error.message);
        }
      },
    }, [
      email,
      el("label", { class: "check" }, [admin, t("users.admin")]),
      el("button", { type: "submit", class: "primary" }, t("users.invite")),
    ]);
    part.replaceChildren(el("p", { class: "muted small" }, t("users.hint")), invite, note, list);

    async function update(user, changes, question) {
      if (question && !window.confirm(question)) return;
      try {
        const result = await api("/admin/users", { method: "PUT", body: { email: user.email, username: user.username, ...changes } });
        if (result.temporary_password) note.replaceChildren(secretNote(user.email, result.temporary_password, false));
        else toast(t("users.saved"));
      } catch (error) {
        toast(error.message);
      }
      await draw();
    }

    async function remove(user) {
      if (!window.confirm(t("users.deleteConfirm", { email: user.email }))) return;
      try {
        await api(`/admin/users?email=${encodeURIComponent(user.email)}&username=${encodeURIComponent(user.username)}`, { method: "DELETE" });
        toast(t("users.deleted"));
      } catch (error) {
        toast(error.message);
      }
      await draw();
    }

    function mails(prefs) {
      const kinds = ["digest", "alerts"].filter((key) => prefs[key]).map((key) => t("users.mail." + key));
      return kinds.length ? kinds.join(", ") : t("users.mail.none");
    }

    async function draw() {
      let users;
      try {
        users = (await api("/admin/users")).items || [];
      } catch (error) {
        list.replaceChildren(el("p", { class: "error" }, error.message));
        return;
      }
      list.replaceChildren(el("table", { class: "users" }, [
        el("thead", {}, el("tr", {}, [t("users.email"), t("users.status"), "2FA", t("users.mails"), t("users.admin"), ""].map((text) => el("th", {}, text)))),
        el("tbody", {}, users.map((user) => el("tr", { class: user.enabled ? "" : "disabled" }, [
          el("td", {}, [user.email, user.self ? el("span", { class: "muted" }, " " + t("users.you")) : null]),
          el("td", {}, user.enabled ? t("users.state." + user.status) : t("users.disabled")),
          el("td", { class: user.mfa ? "" : "muted" }, [el("span", { class: "narrow-only" }, "2FA: "), t(user.sso ? "users.mfaSso" : user.mfa ? "users.mfaOn" : "users.mfaOff")]),
          el("td", { class: "muted" }, [el("span", { class: "narrow-only" }, t("users.mails") + ": "), mails(user.prefs)]),
          el("td", {}, el("label", { class: "admin-toggle" }, [
            el("input", {
              type: "checkbox",
              checked: user.admin,
              // AWS SSO: the groups in IAM Identity Center decide.
              disabled: user.self || user.sso,
              title: user.sso ? t("users.adminSso") : null,
              "aria-label": t("users.adminOf", { email: user.email }),
              onchange: (event) => update(user, { admin: event.target.checked }),
            }),
            el("span", { class: "narrow-only" }, t("users.admin")),
          ])),
          el("td", { class: "row-actions" }, user.self ? null : [
            user.sso ? null : el("button", { type: "button", onclick: () => update(user, { reset_password: true }, t("users.resetPasswordConfirm", { email: user.email })) }, t("users.resetPassword")),
            user.mfa ? el("button", { type: "button", onclick: () => update(user, { reset_mfa: true }, t("users.resetMfaConfirm", { email: user.email })) }, t("users.resetMfa")) : null,
            el("button", { type: "button", onclick: () => update(user, { enabled: !user.enabled }) }, t(user.enabled ? "users.disable" : "users.enable")),
            el("button", { type: "button", class: "danger", onclick: () => remove(user) }, t("users.delete")),
          ]),
        ]))),
      ]));
    }
    draw();
    email.focus();
  }

  function secretNote(email, password, invited) {
    return el("div", { class: "secret-note" }, [
      el("p", {}, t(invited ? "users.invited" : "users.newPassword", { email })),
      el("p", {}, [t("users.temporaryPassword") + " ", el("code", {}, password)]),
    ]);
  }

  // The version of the site and the releases of the demo repository that came
  // after it, with their notes. The API reads them from GitHub and keeps them
  // an hour.
  async function showVersion(part) {
    part.replaceChildren(loading());
    let info;
    try {
      info = await api("/version");
    } catch (error) {
      part.replaceChildren(el("p", { class: "error" }, error.message));
      return;
    }
    const link = (href, text) => el("a", { href, target: "_blank", rel: "noopener" }, text);
    let status;
    if (info.error) status = el("p", { class: "error" }, t("version.unavailable"));
    else if (!info.repository) status = el("p", { class: "muted" }, t("version.off"));
    else if (!info.latest) status = el("p", { class: "muted" }, t("version.none"));
    else if (!info.newer.length) status = el("p", { class: "totp-status" }, [el("span", { class: "badge ok" }, t("version.current")), " ", t("version.newest")]);
    else status = el("p", { class: "totp-status" }, [el("span", { class: "badge warn" }, t("version.behind")), " ", t("version.newer", { count: info.newer.length })]);
    fill(part, [
      el("dl", { class: "version-facts" }, [
        el("dt", {}, t("version.title")),
        el("dd", {}, info.version && info.url ? link(info.url + "/tag/" + encodeURIComponent(info.version), info.version) : info.version || "—"),
        el("dt", {}, t("version.repository")),
        el("dd", {}, info.repository ? link(info.url, info.repository) : "—"),
      ]),
      status,
      info.newer.length ? el("ul", { class: "releases" }, info.newer.map((release) => el("li", {}, [
        el("strong", {}, link(release.url, release.title)),
        release.date ? el("span", { class: "muted" }, " · " + release.date) : null,
        release.notes ? el("p", { class: "release-notes" }, release.notes) : null,
      ]))) : null,
    ]);
  }

  function passwordForm() {
    const error = el("p", { class: "error", role: "alert" });
    const current = el("input", { type: "password", autocomplete: "current-password", required: true });
    const next = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: "12" });
    const again = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: "12" });
    const button = el("button", { class: "primary", type: "submit" }, t("password.submit"));
    const form = el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        error.textContent = "";
        if (next.value !== again.value) {
          error.textContent = t("password.mismatch");
          return;
        }
        button.disabled = true;
        try {
          const { user } = await currentUser();
          await cognito((done) => user.changePassword(current.value, next.value, done));
          form.reset();
          toast(t("password.changed"));
        } catch (failure) {
          error.textContent = authMessage(failure);
        }
        button.disabled = false;
      },
    }, [
      el("label", {}, [t("password.current"), current]),
      el("label", {}, [t("password.new"), next]),
      el("label", {}, [t("password.again"), again]),
      el("p", { class: "muted small" }, t("password.rules")),
      el("div", { class: "actions" }, [button]),
      error,
    ]);
    return form;
  }

  // Two-factor sign-in with an authenticator app (TOTP): Cognito gives a
  // secret, the app shows codes, one code confirms the pairing.
  async function showMfa(part) {
    part.replaceChildren(loading());
    let enabled;
    try {
      const { user } = await currentUser();
      const data = await cognito((done) => user.getUserData(done, { bypassCache: true }));
      enabled = (data.UserMFASettingList || []).includes("SOFTWARE_TOKEN_MFA");
    } catch (error) {
      part.replaceChildren(el("p", { class: "error" }, authMessage(error)));
      return;
    }
    if (enabled) {
      part.replaceChildren(
        el("p", {}, [el("span", { class: "badge ok" }, t("mfa.on")), t("mfa.onText")]),
        el("div", { class: "actions" }, el("button", {
          type: "button",
          class: "danger",
          onclick: async () => {
            if (!window.confirm(t("mfa.disableConfirm"))) return;
            try {
              const { user } = await currentUser();
              await cognito((done) => user.setUserMfaPreference(null, { PreferredMfa: false, Enabled: false }, done));
              toast(t("mfa.disabled"));
            } catch (error) {
              toast(authMessage(error));
            }
            showMfa(part);
          },
        }, t("mfa.disable"))));
      return;
    }
    part.replaceChildren(
      el("p", { class: "muted" }, t("mfa.offText")),
      el("div", { class: "actions" }, el("button", { type: "button", class: "primary", onclick: () => pairApp(part) }, t("mfa.enable"))));
  }

  async function pairApp(part) {
    let user;
    let secret;
    try {
      user = (await currentUser()).user;
      secret = await new Promise((resolve, reject) => user.associateSoftwareToken({
        associateSecretCode: resolve,
        onFailure: reject,
      }));
    } catch (error) {
      toast(authMessage(error));
      return;
    }
    const uri = `otpauth://totp/${encodeURIComponent(ISSUER + ":" + state.me.email)}?secret=${secret}&issuer=${encodeURIComponent(ISSUER)}`;
    const qr = window.qrcode(0, "M");
    qr.addData(uri);
    qr.make();
    const error = el("p", { class: "error", role: "alert" });
    const code = el("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9]{6}", maxlength: "6", required: true });
    const button = el("button", { class: "primary", type: "submit" }, t("mfa.submit"));
    part.replaceChildren(el("form", {
      class: "pair",
      onsubmit: async (event) => {
        event.preventDefault();
        error.textContent = "";
        button.disabled = true;
        try {
          await new Promise((resolve, reject) => user.verifySoftwareToken(code.value.trim(), ISSUER, { onSuccess: resolve, onFailure: reject }));
          await cognito((done) => user.setUserMfaPreference(null, { PreferredMfa: true, Enabled: true }, done));
          toast(t("mfa.enabled"));
          showMfa(part);
        } catch (failure) {
          error.textContent = authMessage(failure);
          button.disabled = false;
        }
      },
    }, [
      el("p", {}, t("mfa.step1")),
      el("img", { class: "qr", src: qr.createDataURL(5, 2), alt: t("mfa.qrAlt"), width: "205", height: "205" }),
      el("p", { class: "mono secret" }, secret),
      el("label", {}, [t("mfa.step2"), code]),
      el("div", { class: "actions" }, [button]),
      error,
    ]));
    code.focus();
  }

  async function boot() {
    if (!window.AmazonCognitoIdentity || !config.userPoolId || !config.clientId) {
      root.textContent = t("noAuth");
      return;
    }
    const query = new URLSearchParams(location.search);
    if (config.ssoProvider && (query.has("code") || query.has("error"))) {
      let failure;
      try {
        failure = await finishSso(query);
      } catch (error) {
        failure = error.message;
      }
      if (failure) {
        showLogin(t("login.ssoFailed", { error: failure }));
        return;
      }
    } else if (config.ssoProvider && query.get("login") === "sso") {
      // The tile of the application in the AWS access portal opens the site
      // with ?login=sso: the user has just signed in there.
      history.replaceState(null, "", "/" + location.hash);
      const signedIn = await currentSession().then(() => true, () => false);
      if (!signedIn) {
        startSso();
        return;
      }
    }
    try {
      await currentSession();
      await loadLanguage();
      state.me = await api("/me");
    } catch (error) {
      if (!document.querySelector(".login")) showLogin();
      return;
    }
    if (!listening) window.addEventListener("hashchange", show);
    listening = true;
    show();
  }

  setLanguage(known(storedLanguage()) ? storedLanguage() : browserLanguage());
  boot();
})();
