const API_PATHS = {
  fetchTotals: "/admin/fetches/totals",
  userFetchStats: "/admin/users/fetch-stats",
  requests: "/admin/requests",
  requestStatusCounts: "/admin/requests/status-counts",
  requestTrace: (requestId) => `/admin/requests/${encodeURIComponent(requestId)}/trace`,
  auditVerify: "/audit/verify",
  policyRules: "/admin/policy/rules",
  restorePolicyRules: "/admin/policy/rules/restore-defaults",
};
const ELEMENT_IDS = {
  closeTrace: "close-trace",
  loadMoreRequests: "load-more-requests",
  loadMoreRetrievals: "load-more-retrievals",
  knownRelations: "known-relations",
  newRuleId: "new-rule-id",
  policyError: "policy-error",
  policyJson: "policy-json",
  policyJsonInput: "policy-json-input",
  policyRuleForm: "policy-rule-form",
  policyRuleList: "policy-rule-list",
  policyTiles: "policy-tiles",
  refreshStatus: "refresh-status",
  requestTable: "request-table",
  restorePolicy: "restore-policy",
  requestUserFilter: "request-user-filter",
  retrievalList: "retrieval-list",
  retrievalListEmpty: "retrieval-list-empty",
  retrievalListTitle: "retrieval-list-title",
  retrievalRange: "retrieval-range",
  savePolicyJson: "save-policy-json",
  signIn: "sign-in",
  signInError: "sign-in-error",
  signInForm: "sign-in-form",
  signOut: "sign-out",
  statTiles: "stat-tiles",
  statusDonut: "status-donut",
  tokenInput: "token-input",
  tooltip: "tooltip",
  totalsChart: "totals-chart",
  traceBody: "trace-body",
  tracePanel: "trace-panel",
  userDetail: "user-detail",
  userList: "user-list",
  userListEmpty: "user-list-empty",
  userSearch: "user-search",
  userSort: "user-sort",
  viewPolicy: "view-policy",
  viewRequests: "view-requests",
  viewRetrievals: "view-retrievals",
  viewUsers: "view-users",
};
const REPORT_TOKEN_HEADER = "X-Report-Token";
const REPORT_TOKEN_STORAGE_KEY = "aware.reportToken";
const HTTP_UNAUTHORIZED = 401;
const HTTP_CONFLICT = 409;
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

const VIEW_USERS = "users";
const VIEW_REQUESTS = "requests";
const VIEW_RETRIEVALS = "retrievals";
const VIEW_POLICY = "policy";
const VIEW_SECTION_IDS = {
  [VIEW_USERS]: ELEMENT_IDS.viewUsers, [VIEW_REQUESTS]: ELEMENT_IDS.viewRequests, [VIEW_RETRIEVALS]: ELEMENT_IDS.viewRetrievals, [VIEW_POLICY]: ELEMENT_IDS.viewPolicy,
};
const DEFAULT_VIEW = VIEW_USERS;
const AUTO_REFRESH_MILLISECONDS = 15000;
const REQUEST_PAGE_SIZE = 25;
const MAX_REQUEST_PAGE_SIZE = 500;
const USER_RECENT_REQUEST_COUNT = 8;
const ALL_OPTION_VALUE = "";

const HOUR_MILLISECONDS = 3600 * 1000;
const DAY_MILLISECONDS = 24 * HOUR_MILLISECONDS;
const BUCKET_HOUR = "hour";
const BUCKET_DAY = "day";
const TIME_RANGES = [
  { id: "6h", label: "Last 6 hours", bucketCount: 6, bucket: BUCKET_HOUR },
  { id: "24h", label: "Last 24 hours", bucketCount: 24, bucket: BUCKET_HOUR },
  { id: "7d", label: "Last 7 days", bucketCount: 7, bucket: BUCKET_DAY },
];
const DEFAULT_TIME_RANGE_ID = "24h";
// The donut has no time axis, so its ranges are plain trailing windows and can be as short as a live demo needs.
const RETRIEVAL_TIME_RANGES = [
  { id: "30s", label: "Last 30 seconds", milliseconds: 30 * 1000 },
  { id: "5m", label: "Last 5 minutes", milliseconds: 5 * 60 * 1000 },
  { id: "1h", label: "Last hour", milliseconds: HOUR_MILLISECONDS },
  { id: "24h", label: "Last 24 hours", milliseconds: DAY_MILLISECONDS },
  { id: "7d", label: "Last 7 days", milliseconds: 7 * DAY_MILLISECONDS },
];
// Seeded demo traffic is hours old, so a 30-second default would open on an empty donut.
const DEFAULT_RETRIEVAL_TIME_RANGE_ID = "24h";

// Denial share at which a user is flagged red rather than yellow; any denial at all is yellow.
const CRITICAL_DENIAL_SHARE = 0.25;
const USER_STATUS = {
  critical: { className: "status-critical", label: "high denial share" },
  warning: { className: "status-warning", label: "some fetches denied" },
  good: { className: "status-good", label: "no denials" },
};

const USER_SORT_OPTIONS = [
  { id: "denial-share", label: "highest denial share", compare: (a, b) => getDenialShare(b) - getDenialShare(a) || b.denied - a.denied },
  { id: "most-denied", label: "most denied fetches", compare: (a, b) => b.denied - a.denied },
  { id: "most-fetches", label: "most fetches", compare: (a, b) => getFetchCount(b) - getFetchCount(a) },
  { id: "latest-activity", label: "latest activity", compare: (a, b) => Date.parse(b.last_fetch_at) - Date.parse(a.last_fetch_at) },
  { id: "name", label: "name", compare: (a, b) => getUserDisplayName(a).localeCompare(getUserDisplayName(b)) },
];

const OUTCOME_ANSWERED = "answered";
const OUTCOME_REFUSED = "refused";
const OUTCOME_FAILED_CLOSED = "failed_closed";
const DENIED_AT_CHECKPOINT_1 = "checkpoint_1";
const DENIED_AT_CHECKPOINT_2 = "checkpoint_2";
const OUTCOME_FILTER_OPTIONS = [
  { value: ALL_OPTION_VALUE, label: "All outcomes" },
  { value: OUTCOME_ANSWERED, label: "Answered" },
  { value: OUTCOME_REFUSED, label: "Refused" },
  { value: OUTCOME_FAILED_CLOSED, label: "Failed closed" },
];
const DENIED_AT_FILTER_OPTIONS = [
  { value: ALL_OPTION_VALUE, label: "Anywhere or nowhere" },
  { value: DENIED_AT_CHECKPOINT_1, label: "Checkpoint 1 (prompt)" },
  { value: DENIED_AT_CHECKPOINT_2, label: "Checkpoint 2 (data)" },
];
const ALL_USERS_LABEL = "All users";
const EMPTY_CELL_TEXT = "—";
const REQUEST_OUTCOME_BADGES = {
  refused: { className: "badge badge-critical", label: "Refused" },
  failedClosed: { className: "badge badge-neutral", label: "Failed closed" },
  dataBlocked: { className: "badge badge-warning", label: "Answered, data blocked" },
  answered: { className: "badge badge-good", label: "Answered" },
};
const DENIED_AT_LABELS = { [DENIED_AT_CHECKPOINT_1]: "Checkpoint 1", [DENIED_AT_CHECKPOINT_2]: "Checkpoint 2" };

const FETCH_SERIES = [
  { key: "checkpoint1", label: "Blocked at checkpoint 1 (prompt)", segmentClass: "segment-checkpoint-1", keyClass: "key-checkpoint-1" },
  { key: "checkpoint2", label: "Blocked at checkpoint 2 (data)", segmentClass: "segment-checkpoint-2", keyClass: "key-checkpoint-2" },
  { key: "passed", label: "Passed", segmentClass: "segment-passed", keyClass: "key-passed" },
];
const SERIES_COLOR_VARIABLES = { checkpoint1: "--status-critical", checkpoint2: "--status-warning", passed: "--status-good" };

const BADGE_CLASSES = { good: "badge badge-good", warning: "badge badge-warning", critical: "badge badge-critical", neutral: "badge badge-neutral" };
const STEP_OUTCOME_BADGES = { passed: BADGE_CLASSES.good, denied: BADGE_CLASSES.critical, failed: BADGE_CLASSES.warning, info: BADGE_CLASSES.neutral };

const CHART_LAYOUT = { width: 620, height: 240, marginTop: 24, marginRight: 52, marginBottom: 30, marginLeft: 40, maxBarWidth: 30, barWidthShare: 0.6, segmentGap: 2, cornerRadius: 4, emptyBucketHeight: 2, maxXLabels: 8 };
const SHARE_TICKS = [0, 20, 40, 60, 80, 100];
const COUNT_TICK_TARGET = 5;
const SHARE_AXIS_TITLE = "%";
const COUNT_AXIS_TITLE = "fetches";
const TIME_AXIS_TITLES = { [BUCKET_HOUR]: "Time (h)", [BUCKET_DAY]: "Day" };
const TOOLTIP_OFFSET_PIXELS = 14;

const TEXT = {
  noUserSelected: "Select a user to see their fetches over time.",
  noRequests: "No requests match these filters.",
  noUserRequests: "No requests in this range.",
  recentRequests: "Recent requests",
  showDataTable: "Show data table",
  loadFailed: "Could not load data: ",
  refreshedAt: "Updated ",
  chainIntact: "intact",
  chainBroken: "BROKEN",
  auditChain: "Audit chain",
  eventsChecked: " events checked",
  brokenAt: "first broken event: ",
  fetchAttempts: "Fetch attempts",
  passed: "Passed",
  deniedCheckpoint1: "Blocked at checkpoint 1",
  deniedCheckpoint2: "Blocked at checkpoint 2",
  ofAttempts: "% of attempts",
  denied: "% denied",
  fetches: " fetches",
  detail: "detail",
};
const USER_COLUMN_HEADER = "User";
const NUMERIC_REQUEST_COLUMN_HEADERS = new Set(["Fetches passed", "Fetches denied"]);
const REQUEST_TABLE_HEADERS = ["Time", USER_COLUMN_HEADER, "Outcome", "Denied at", ...NUMERIC_REQUEST_COLUMN_HEADERS, "Reason"];
const BUCKET_TABLE_HEADERS = ["Bucket", ...FETCH_SERIES.map((series) => series.label)];
const TRACE_SUMMARY_LABELS = { requestId: "Request id", user: "User", time: "Time", outcome: "Outcome", reason: "Reason" };
const DURATION_UNIT = " ms";

const REQUEST_STATUS_PASSED = "passed";
const REQUEST_STATUS_PARTIALLY_PASSED = "partially_passed";
const REQUEST_STATUS_BLOCKED = "blocked";
// Clockwise from twelve o'clock, as the slices are drawn.
const REQUEST_STATUSES = [
  { value: REQUEST_STATUS_BLOCKED, label: "Blocked", icon: "✕", className: "blocked", markerClass: "status-critical" },
  { value: REQUEST_STATUS_PASSED, label: "Passed", icon: "✓", className: "passed", markerClass: "status-good" },
  { value: REQUEST_STATUS_PARTIALLY_PASSED, label: "Partially passed", icon: "?", className: "partially-passed", markerClass: "status-warning" },
];
const ALL_REQUESTS_TITLE = "All requests";
const DETAILS_BUTTON_TEXT = "Details";
const TOTAL_LABEL = "Total";
const DONUT_LAYOUT = { size: 170, radius: 62, sliceGap: 2 };
const ACTIVATION_KEYS = new Set(["Enter", " "]);
const DONUT_FOCUS_KEY_ATTRIBUTE = "data-focus-key";
const DONUT_CENTER_FOCUS_KEY = "center";

const VIOLATION_PREDICATE = "violation";
const KNOWS_PREDICATE = "knows";
const RECIPIENT_VARIABLE = "?u";
const NEW_RULE_SUBJECT_VARIABLE = "?s";
const NEW_RULE_VALUE_VARIABLE_PREFIX = "?v";
const VARIABLE_PREFIX = "?";
// Constants that need no quoting in Datalog notation.
const PLAIN_DATALOG_CONSTANT = /^[a-z][a-z0-9_]*$/;
const KNOWS_RELATION_TERM_INDEX = 2;
const JSON_INDENT = 2;
const RULE_KIND_BADGES = {
  blocking: { className: BADGE_CLASSES.critical, label: "blocks" },
  derivation: { className: BADGE_CLASSES.neutral, label: "derives knowledge" },
};
const POLICY_TEXT = {
  version: "Policy version",
  revision: "revision ",
  rules: "Rules",
  blockingAndDerived: (blocking, derived) => `${blocking} blocking · ${derived} derived`,
  enabledBlocking: "Enabled blocking rules",
  ofBlocking: (total) => `of ${total} blocking`,
  enabled: "Enabled",
  deleteRule: "Delete",
  combines: "Combines",
  confirmDelete: (ruleId) => `Delete rule ${ruleId}? Restore defaults brings back the shipped rules.`,
  confirmRestore: "Replace all rules with the shipped defaults?",
  invalidJson: "That is not valid JSON: ",
  notAList: "The JSON must be a list of rules.",
  staleRevision: "Someone else changed the rules in the meantime; the list now shows their version. Make your change again.",
};

const state = {
  view: DEFAULT_VIEW,
  timeRangeId: DEFAULT_TIME_RANGE_ID,
  userStats: [],
  selectedUserId: null,
  userSearch: "",
  userSortId: USER_SORT_OPTIONS[0].id,
  requestFilters: { userId: ALL_OPTION_VALUE, outcome: ALL_OPTION_VALUE, deniedAt: ALL_OPTION_VALUE },
  requestItems: [],
  requestNextCursor: null,
  userDetailLoadNumber: 0,
  retrievalTimeRangeId: DEFAULT_RETRIEVAL_TIME_RANGE_ID,
  // null shows every request; set by clicking a slice, cleared by clicking the centre.
  selectedRequestStatus: null,
  retrievalItems: [],
  retrievalNextCursor: null,
  retrievalLoadNumber: 0,
  // Used only when sessionStorage is blocked.
  fallbackToken: "",
  dataTableOpen: false,
  // The last rules document from the server; edits are sent against its revision.
  policy: null,
};

class UnauthorizedError extends Error {}

// A request the server understood and refused (400 invalid policy, 409 stale revision); `message` is its reason.
class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ---------- API ----------

function getStoredToken() {
  try {
    return sessionStorage.getItem(REPORT_TOKEN_STORAGE_KEY) || "";
  } catch {
    return state.fallbackToken || "";
  }
}

function storeToken(token) {
  state.fallbackToken = token;
  try {
    if (token) sessionStorage.setItem(REPORT_TOKEN_STORAGE_KEY, token);
    else sessionStorage.removeItem(REPORT_TOKEN_STORAGE_KEY);
  } catch {
    // Storage can be blocked; the in-memory copy keeps this tab working.
  }
}

async function fetchJson(path, parameters = {}) {
  const url = new URL(path, window.location.origin);
  for (const [name, value] of Object.entries(parameters)) {
    if (value !== null && value !== undefined && value !== ALL_OPTION_VALUE) url.searchParams.set(name, value);
  }
  const response = await fetch(url, { headers: { [REPORT_TOKEN_HEADER]: getStoredToken() } });
  if (response.status === HTTP_UNAUTHORIZED) throw new UnauthorizedError();
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

async function sendJson(method, path, body) {
  const response = await fetch(new URL(path, window.location.origin), {
    method,
    headers: { [REPORT_TOKEN_HEADER]: getStoredToken(), "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === HTTP_UNAUTHORIZED) throw new UnauthorizedError();
  if (!response.ok) throw new ApiError(response.status, await getErrorDetail(response));
  return response.json();
}

async function getErrorDetail(response) {
  const text = await response.text();
  try {
    return JSON.parse(text).detail ?? text;
  } catch {
    return text;
  }
}

function fetchUserFetchStats(queryWindow) {
  return fetchJson(API_PATHS.userFetchStats, { start: queryWindow.start, end: queryWindow.end });
}

function fetchFetchTotals(queryWindow, userId) {
  return fetchJson(API_PATHS.fetchTotals, { start: queryWindow.start, end: queryWindow.end, bucket: queryWindow.bucket, user_id: userId });
}

function fetchRequestPage(queryWindow, filters, cursor, limit) {
  return fetchJson(API_PATHS.requests, {
    start: queryWindow.start, end: queryWindow.end, user_id: filters.userId, outcome: filters.outcome, denied_at: filters.deniedAt, status: filters.status, cursor, limit,
  });
}

function fetchRequestStatusCounts(queryWindow) {
  return fetchJson(API_PATHS.requestStatusCounts, { start: queryWindow.start, end: queryWindow.end });
}

function fetchRequestTrace(requestId) {
  return fetchJson(API_PATHS.requestTrace(requestId));
}

function fetchPolicyRules() {
  return fetchJson(API_PATHS.policyRules);
}

function savePolicyRules(rules, revision) {
  return sendJson("PUT", API_PATHS.policyRules, { revision, rules });
}

function restorePolicyRules() {
  return sendJson("POST", API_PATHS.restorePolicyRules);
}

function fetchAuditChain() {
  return fetchJson(API_PATHS.auditVerify);
}

// ---------- Time ranges ----------

function getTimeRange() {
  return TIME_RANGES.find((range) => range.id === state.timeRangeId) || TIME_RANGES[0];
}

function floorToBucket(moment, bucket) {
  const floored = new Date(moment);
  floored.setUTCMinutes(0, 0, 0);
  if (bucket === BUCKET_DAY) floored.setUTCHours(0);
  return floored;
}

// The window starts on a bucket boundary so the chart shows exactly bucketCount bars, the last one in progress.
function getQueryWindow() {
  const range = getTimeRange();
  const now = new Date();
  const bucketMilliseconds = range.bucket === BUCKET_DAY ? DAY_MILLISECONDS : HOUR_MILLISECONDS;
  const start = new Date(floorToBucket(now, range.bucket).getTime() - (range.bucketCount - 1) * bucketMilliseconds);
  return { start: start.toISOString(), end: now.toISOString(), bucket: range.bucket };
}

function getRetrievalQueryWindow() {
  const range = RETRIEVAL_TIME_RANGES.find((entry) => entry.id === state.retrievalTimeRangeId) || RETRIEVAL_TIME_RANGES[0];
  const now = Date.now();
  return { start: new Date(now - range.milliseconds).toISOString(), end: new Date(now).toISOString() };
}

// ---------- Derived values ----------

function getFetchCount(userStats) {
  return userStats.passed + userStats.denied;
}

function getDenialShare(userStats) {
  const total = getFetchCount(userStats);
  return total === 0 ? 0 : userStats.denied / total;
}

function getUserStatus(userStats) {
  const share = getDenialShare(userStats);
  if (share >= CRITICAL_DENIAL_SHARE) return USER_STATUS.critical;
  if (share > 0) return USER_STATUS.warning;
  return USER_STATUS.good;
}

function getUserDisplayName(user) {
  return user.user_name || user.user_email || user.user_id;
}

function getPercentText(share) {
  return `${Math.round(share * 100)}`;
}

function getBucketCounts(rawBucket) {
  return {
    start: new Date(rawBucket.bucket_start),
    passed: rawBucket.passed,
    checkpoint1: rawBucket.denied_at_checkpoint_1,
    checkpoint2: rawBucket.denied_at_checkpoint_2,
  };
}

function getBucketTotal(bucketCounts) {
  return bucketCounts.passed + bucketCounts.checkpoint1 + bucketCounts.checkpoint2;
}

function getSeriesTotals(buckets) {
  const totals = { passed: 0, checkpoint1: 0, checkpoint2: 0 };
  for (const bucket of buckets) for (const series of FETCH_SERIES) totals[series.key] += bucket[series.key];
  return totals;
}

function getVisibleUsers() {
  const search = state.userSearch.trim().toLowerCase();
  const sortOption = USER_SORT_OPTIONS.find((option) => option.id === state.userSortId) || USER_SORT_OPTIONS[0];
  return state.userStats
    .filter((user) => !search || [user.user_name, user.user_email, user.user_id].some((field) => (field || "").toLowerCase().includes(search)))
    .sort(sortOption.compare);
}

function getRequestOutcomeBadge(summary) {
  if (summary.outcome === OUTCOME_REFUSED) return REQUEST_OUTCOME_BADGES.refused;
  if (summary.outcome === OUTCOME_FAILED_CLOSED) return REQUEST_OUTCOME_BADGES.failedClosed;
  if (summary.denied_at === DENIED_AT_CHECKPOINT_2) return REQUEST_OUTCOME_BADGES.dataBlocked;
  return REQUEST_OUTCOME_BADGES.answered;
}

function getNiceAxisMaximum(value) {
  if (value <= COUNT_TICK_TARGET) return Math.max(value, 1);
  const roughStep = value / COUNT_TICK_TARGET;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= roughStep);
  return Math.ceil(value / step) * step;
}

function getCountTicks(maximum) {
  const step = maximum <= COUNT_TICK_TARGET ? 1 : maximum / COUNT_TICK_TARGET;
  return Array.from({ length: Math.round(maximum / step) + 1 }, (_, index) => Math.round(index * step));
}

// ---------- Formatting ----------

function formatDateTime(isoText) {
  return new Date(isoText).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatBucketLabel(start, bucket) {
  if (bucket === BUCKET_DAY) return start.toLocaleDateString(undefined, { weekday: "short", day: "numeric", timeZone: "UTC" });
  return start.toLocaleTimeString(undefined, { hour: "2-digit", hourCycle: "h23" });
}

function formatBucketRange(start, bucket) {
  if (bucket === BUCKET_DAY) return start.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });
  const end = new Date(start.getTime() + HOUR_MILLISECONDS);
  return `${start.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} – ${end.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

// ---------- DOM helpers ----------

function createElement(tagName, { className, text, attributes } = {}, children = []) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  for (const [name, value] of Object.entries(attributes || {})) element.setAttribute(name, value);
  for (const child of children) if (child) element.append(child);
  return element;
}

function createSvgElement(tagName, attributes = {}) {
  const element = document.createElementNS(SVG_NAMESPACE, tagName);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  return element;
}

function fillSelect(select, options, selectedValue) {
  select.replaceChildren(...options.map((option) => createElement("option", { text: option.label, attributes: { value: option.value } })));
  select.value = selectedValue;
}

function buildTable(headers, rows, numericColumns = new Set()) {
  const headRow = createElement("tr", {}, headers.map((header, index) => createElement("th", { text: header, className: numericColumns.has(index) ? "number" : undefined })));
  const table = createElement("table", {}, [createElement("thead", {}, [headRow]), createElement("tbody", {}, rows)]);
  return createElement("div", { className: "table-scroll" }, [table]);
}

function buildCell(content, className) {
  const cell = createElement("td", { className });
  cell.append(content instanceof Node ? content : document.createTextNode(String(content)));
  return cell;
}

function buildBadge(badge) {
  return createElement("span", { className: badge.className, text: badge.label });
}

// ---------- Tooltip ----------

const tooltip = document.getElementById(ELEMENT_IDS.tooltip);

function showTooltip(title, rows, anchorX, anchorY) {
  const rowElements = rows.map((row) => {
    const key = createElement("span", { className: "tooltip-key", attributes: { style: `background: var(${SERIES_COLOR_VARIABLES[row.key]})` } });
    return createElement("div", { className: "tooltip-row" }, [key, createElement("strong", { text: String(row.value) }), createElement("span", { text: row.label })]);
  });
  tooltip.replaceChildren(createElement("div", { className: "tooltip-title", text: title }), ...rowElements);
  tooltip.hidden = false;
  const left = Math.min(anchorX + TOOLTIP_OFFSET_PIXELS, window.innerWidth - tooltip.offsetWidth - TOOLTIP_OFFSET_PIXELS);
  tooltip.style.left = `${Math.max(0, left)}px`;
  tooltip.style.top = `${Math.max(0, anchorY - tooltip.offsetHeight - TOOLTIP_OFFSET_PIXELS)}px`;
}

function hideTooltip() {
  tooltip.hidden = true;
}

// ---------- Chart ----------

function buildTopRoundedBarPath(x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height);
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
}

function getSegmentGeometry(bucketCounts, scaleValue, baselineY) {
  const values = FETCH_SERIES.map((series) => ({ series, value: bucketCounts[series.key] })).filter((entry) => entry.value > 0);
  const segments = [];
  let segmentBottom = baselineY;
  values.forEach((entry, index) => {
    const fullHeight = scaleValue(entry.value);
    const isTopSegment = index === values.length - 1;
    // The gap is carved out of the lower segment so the stack's total height stays true to the data.
    const height = isTopSegment ? fullHeight : Math.max(fullHeight - CHART_LAYOUT.segmentGap, 1);
    segments.push({ series: entry.series, top: segmentBottom - height, height, isTopSegment });
    segmentBottom -= fullHeight;
  });
  return segments;
}

function buildYAxis(svg, ticks, axisMaximum, plot) {
  for (const tick of ticks) {
    const y = plot.bottom - (tick / axisMaximum) * plot.height;
    if (tick > 0) svg.append(createSvgElement("line", { class: "gridline", x1: plot.left, x2: plot.right, y1: y, y2: y }));
    const label = createSvgElement("text", { class: "tick-label", x: plot.left - 8, y: y + 3, "text-anchor": "end" });
    label.textContent = String(tick);
    svg.append(label);
  }
}

function buildXAxisLabels(svg, buckets, bucket, slotCenter, plot) {
  const labelStep = Math.ceil(buckets.length / CHART_LAYOUT.maxXLabels);
  buckets.forEach((bucketCounts, index) => {
    if ((buckets.length - 1 - index) % labelStep !== 0) return;
    const label = createSvgElement("text", { class: "tick-label", x: slotCenter(index), y: plot.bottom + 16, "text-anchor": "middle" });
    label.textContent = formatBucketLabel(bucketCounts.start, bucket);
    svg.append(label);
  });
}

function buildAxisTitles(svg, valueAxisTitle, timeAxisTitle, plot) {
  const valueTitle = createSvgElement("text", { class: "axis-title", x: plot.left, y: plot.top - 12, "text-anchor": "middle" });
  valueTitle.textContent = valueAxisTitle;
  const timeTitle = createSvgElement("text", { class: "axis-title", x: plot.right + 6, y: plot.bottom + 16 });
  timeTitle.textContent = timeAxisTitle;
  svg.append(valueTitle, timeTitle);
}

function buildBarGroup(bucketCounts, geometry, bucket) {
  const group = createSvgElement("g", { class: "bar-group", tabindex: "0" });
  const total = getBucketTotal(bucketCounts);
  group.setAttribute("aria-label", `${formatBucketRange(bucketCounts.start, bucket)}: ${FETCH_SERIES.map((series) => `${bucketCounts[series.key]} ${series.label}`).join(", ")}`);
  group.append(createSvgElement("rect", { class: "bar-hit", x: geometry.slotLeft, y: geometry.plotTop, width: geometry.slotWidth, height: geometry.plotHeight }));
  if (total === 0) {
    group.append(createSvgElement("rect", { class: "empty-bucket", x: geometry.barLeft, y: geometry.baselineY - CHART_LAYOUT.emptyBucketHeight, width: geometry.barWidth, height: CHART_LAYOUT.emptyBucketHeight }));
  }
  for (const segment of getSegmentGeometry(bucketCounts, geometry.scaleValue, geometry.baselineY)) {
    const path = segment.isTopSegment
      ? buildTopRoundedBarPath(geometry.barLeft, segment.top, geometry.barWidth, segment.height, CHART_LAYOUT.cornerRadius)
      : `M${geometry.barLeft},${segment.top}h${geometry.barWidth}v${segment.height}h${-geometry.barWidth}Z`;
    group.append(createSvgElement("path", { class: `bar-segment ${segment.series.segmentClass}`, d: path }));
  }
  const tooltipRows = [...FETCH_SERIES].reverse().map((series) => ({ key: series.key, label: series.label, value: bucketCounts[series.key] }));
  const tooltipTitle = formatBucketRange(bucketCounts.start, bucket);
  group.addEventListener("pointermove", (event) => showTooltip(tooltipTitle, tooltipRows, event.clientX, event.clientY));
  group.addEventListener("pointerleave", hideTooltip);
  group.addEventListener("focus", () => {
    const bounds = group.getBoundingClientRect();
    showTooltip(tooltipTitle, tooltipRows, bounds.right, bounds.top + bounds.height / 2);
  });
  group.addEventListener("blur", hideTooltip);
  return group;
}

// asShare: each bar is 100% of that bucket's attempts, which compares users with very different volumes.
function buildStackedBarChart(buckets, bucket, asShare) {
  const layout = CHART_LAYOUT;
  const plot = {
    left: layout.marginLeft, right: layout.width - layout.marginRight,
    top: layout.marginTop, bottom: layout.height - layout.marginBottom,
  };
  plot.height = plot.bottom - plot.top;
  const maximumTotal = Math.max(0, ...buckets.map(getBucketTotal));
  const axisMaximum = asShare ? 100 : getNiceAxisMaximum(maximumTotal);
  const ticks = asShare ? SHARE_TICKS : getCountTicks(axisMaximum);
  const slotWidth = (plot.right - plot.left) / Math.max(buckets.length, 1);
  const barWidth = Math.min(layout.maxBarWidth, slotWidth * layout.barWidthShare);
  const slotCenter = (index) => plot.left + slotWidth * (index + 0.5);

  const svg = createSvgElement("svg", { viewBox: `0 0 ${layout.width} ${layout.height}`, role: "group" });
  buildYAxis(svg, ticks, axisMaximum, plot);
  buckets.forEach((bucketCounts, index) => {
    const total = getBucketTotal(bucketCounts);
    const scaleValue = (value) => (asShare ? (total === 0 ? 0 : (value / total) * 100) : value) / axisMaximum * plot.height;
    svg.append(buildBarGroup(bucketCounts, {
      slotLeft: plot.left + slotWidth * index, slotWidth, plotTop: plot.top, plotHeight: plot.height,
      barLeft: slotCenter(index) - barWidth / 2, barWidth, baselineY: plot.bottom, scaleValue,
    }, bucket));
  });
  svg.append(createSvgElement("line", { class: "axis-line", x1: plot.left, x2: plot.left, y1: plot.top - 4, y2: plot.bottom }));
  svg.append(createSvgElement("line", { class: "axis-line", x1: plot.left, x2: plot.right, y1: plot.bottom, y2: plot.bottom }));
  buildXAxisLabels(svg, buckets, bucket, slotCenter, plot);
  buildAxisTitles(svg, asShare ? SHARE_AXIS_TITLE : COUNT_AXIS_TITLE, TIME_AXIS_TITLES[bucket], plot);
  return createElement("div", { className: "chart" }, [svg]);
}

function buildSeriesLegend(totals) {
  return createElement("ul", { className: "legend" }, [...FETCH_SERIES].reverse().map((series) => createElement("li", {}, [
    createElement("span", { className: `legend-key ${series.keyClass}` }),
    createElement("span", { className: "legend-value", text: String(totals[series.key]) }),
    createElement("span", { text: series.label }),
  ])));
}

function buildBucketDataTable(buckets, bucket) {
  const rows = buckets.map((bucketCounts) => createElement("tr", {}, [
    buildCell(formatBucketRange(bucketCounts.start, bucket)),
    ...FETCH_SERIES.map((series) => buildCell(bucketCounts[series.key], "number")),
  ]));
  const numericColumns = new Set(FETCH_SERIES.map((_, index) => index + 1));
  const details = createElement("details", { className: "data-table-toggle" }, [
    createElement("summary", { text: TEXT.showDataTable }),
    buildTable(BUCKET_TABLE_HEADERS, rows, numericColumns),
  ]);
  // Auto-refresh rebuilds the chart, so the open state lives in state rather than in the element.
  details.open = state.dataTableOpen;
  details.addEventListener("toggle", () => { state.dataTableOpen = details.open; });
  return details;
}

// ---------- Users view ----------

function buildUserListItem(user) {
  const status = getUserStatus(user);
  const button = createElement("button", { attributes: { type: "button", "aria-current": String(user.user_id === state.selectedUserId), title: status.label } }, [
    createElement("span", { className: `status-marker ${status.className}`, attributes: { "aria-label": status.label } }),
    createElement("span", { className: "user-name", text: getUserDisplayName(user) }),
    createElement("span", { className: "user-meta", text: `${getPercentText(getDenialShare(user))}${TEXT.denied} · ${getFetchCount(user)}${TEXT.fetches}` }),
  ]);
  button.addEventListener("click", () => selectUser(user.user_id));
  return createElement("li", {}, [button]);
}

function renderUserList() {
  const visibleUsers = getVisibleUsers();
  document.getElementById(ELEMENT_IDS.userList).replaceChildren(...visibleUsers.map(buildUserListItem));
  document.getElementById(ELEMENT_IDS.userListEmpty).hidden = visibleUsers.length > 0;
}

function buildUserRequestTable(requestPage) {
  if (requestPage.items.length === 0) return createElement("p", { className: "muted", text: TEXT.noUserRequests });
  return buildRequestTable(requestPage.items, false);
}

function buildUserDetail(user, buckets, requestPage, bucket) {
  const status = getUserStatus(user);
  const header = createElement("div", { className: "detail-header" }, [
    createElement("span", { className: `status-marker ${status.className}`, attributes: { "aria-label": status.label } }),
    createElement("div", {}, [
      createElement("div", { className: "user-name", text: getUserDisplayName(user) }),
      createElement("div", { className: "detail-subtitle", text: [user.user_email, user.user_id].filter(Boolean).join(" · ") }),
    ]),
  ]);
  return createElement("div", {}, [
    header,
    buildSeriesLegend(getSeriesTotals(buckets)),
    buildStackedBarChart(buckets, bucket, true),
    buildBucketDataTable(buckets, bucket),
    createElement("h2", { text: TEXT.recentRequests, attributes: { style: "margin-top: 1.25rem" } }),
    buildUserRequestTable(requestPage),
  ]);
}

async function loadUserDetail() {
  const container = document.getElementById(ELEMENT_IDS.userDetail);
  const user = state.userStats.find((entry) => entry.user_id === state.selectedUserId);
  if (!user) {
    container.replaceChildren(createElement("p", { className: "muted", text: TEXT.noUserSelected }));
    return;
  }
  // Clicking through users quickly must not let a slow earlier response overwrite the current one.
  const loadNumber = ++state.userDetailLoadNumber;
  const queryWindow = getQueryWindow();
  const userFilters = { userId: user.user_id, outcome: ALL_OPTION_VALUE, deniedAt: ALL_OPTION_VALUE };
  const [rawBuckets, requestPage] = await Promise.all([
    fetchFetchTotals(queryWindow, user.user_id),
    fetchRequestPage(queryWindow, userFilters, null, USER_RECENT_REQUEST_COUNT),
  ]);
  if (loadNumber !== state.userDetailLoadNumber) return;
  hideTooltip();
  container.replaceChildren(buildUserDetail(user, rawBuckets.map(getBucketCounts), requestPage, queryWindow.bucket));
}

async function loadUsersView() {
  state.userStats = await fetchUserFetchStats(getQueryWindow());
  const selectionStillListed = state.userStats.some((user) => user.user_id === state.selectedUserId);
  if (!selectionStillListed) state.selectedUserId = getVisibleUsers()[0]?.user_id ?? null;
  renderUserList();
  await loadUserDetail();
}

function selectUser(userId) {
  state.selectedUserId = userId;
  renderUserList();
  runAndReportErrors(loadUserDetail);
}

// ---------- Requests view ----------

function buildRequestRow(summary, showUser) {
  const row = createElement("tr", { className: "clickable", attributes: { tabindex: "0" } }, [
    buildCell(formatDateTime(summary.occurred_at), "time-cell"),
    showUser ? buildCell(summary.user_name || summary.user_id) : null,
    buildCell(buildBadge(getRequestOutcomeBadge(summary))),
    buildCell(DENIED_AT_LABELS[summary.denied_at] || EMPTY_CELL_TEXT),
    buildCell(summary.passed_fetches, "number"),
    buildCell(summary.denied_fetches, "number"),
    buildCell(summary.reason || EMPTY_CELL_TEXT, "reason-cell"),
  ]);
  const openTrace = () => runAndReportErrors(() => openTracePanel(summary.request_id));
  row.addEventListener("click", openTrace);
  row.addEventListener("keydown", (event) => { if (event.key === "Enter") openTrace(); });
  return row;
}

function buildRequestTable(items, showUser) {
  const headers = showUser ? REQUEST_TABLE_HEADERS : REQUEST_TABLE_HEADERS.filter((header) => header !== USER_COLUMN_HEADER);
  const numericColumns = new Set(headers.flatMap((header, index) => (NUMERIC_REQUEST_COLUMN_HEADERS.has(header) ? [index] : [])));
  return buildTable(headers, items.map((summary) => buildRequestRow(summary, showUser)), numericColumns);
}

function buildStatTile(label, value, detail, keyClass) {
  const labelChildren = [keyClass ? createElement("span", { className: `legend-key ${keyClass}` }) : null, createElement("span", { text: label })];
  return createElement("div", { className: "stat-tile" }, [
    createElement("div", { className: "stat-label" }, labelChildren),
    createElement("div", { className: "stat-value", text: String(value) }),
    createElement("div", { className: "stat-detail", text: detail }),
  ]);
}

function buildChainTile(chain) {
  const badge = chain.intact ? { className: BADGE_CLASSES.good, label: TEXT.chainIntact } : { className: BADGE_CLASSES.critical, label: TEXT.chainBroken };
  const detail = chain.intact ? `${chain.checked}${TEXT.eventsChecked}` : `${TEXT.brokenAt}${chain.first_broken}`;
  return createElement("div", { className: "stat-tile" }, [
    createElement("div", { className: "stat-label", text: TEXT.auditChain }),
    createElement("div", { className: "stat-value" }, [buildBadge(badge)]),
    createElement("div", { className: "stat-detail", text: detail }),
  ]);
}

function getShareDetail(part, total) {
  return total === 0 ? EMPTY_CELL_TEXT : `${getPercentText(part / total)}${TEXT.ofAttempts}`;
}

function renderStatTiles(totals, chain) {
  const attempts = totals.passed + totals.checkpoint1 + totals.checkpoint2;
  document.getElementById(ELEMENT_IDS.statTiles).replaceChildren(
    buildStatTile(TEXT.fetchAttempts, attempts, getTimeRange().label),
    buildStatTile(TEXT.passed, totals.passed, getShareDetail(totals.passed, attempts), "key-passed"),
    buildStatTile(TEXT.deniedCheckpoint1, totals.checkpoint1, getShareDetail(totals.checkpoint1, attempts), "key-checkpoint-1"),
    buildStatTile(TEXT.deniedCheckpoint2, totals.checkpoint2, getShareDetail(totals.checkpoint2, attempts), "key-checkpoint-2"),
    buildChainTile(chain),
  );
}

function renderRequestTable() {
  const container = document.getElementById(ELEMENT_IDS.requestTable);
  if (state.requestItems.length === 0) container.replaceChildren(createElement("p", { className: "muted empty", text: TEXT.noRequests }));
  else container.replaceChildren(buildRequestTable(state.requestItems, true));
  document.getElementById(ELEMENT_IDS.loadMoreRequests).hidden = state.requestNextCursor === null;
}

function renderRequestUserFilter() {
  const options = [{ value: ALL_OPTION_VALUE, label: ALL_USERS_LABEL }, ...state.userStats.map((user) => ({ value: user.user_id, label: getUserDisplayName(user) }))];
  const select = document.getElementById(ELEMENT_IDS.requestUserFilter);
  // Refilling an open dropdown on every auto-refresh would close it under the presenter's cursor.
  const unchanged = options.length === select.options.length && options.every((option, index) => select.options[index].value === option.value);
  if (unchanged) return;
  const selectedUserId = options.some((option) => option.value === state.requestFilters.userId) ? state.requestFilters.userId : ALL_OPTION_VALUE;
  fillSelect(select, options, selectedUserId);
}

async function loadRequestsView() {
  const queryWindow = getQueryWindow();
  // A refresh keeps however many rows were already loaded, so "Load more" progress is not lost.
  const limit = Math.min(Math.max(state.requestItems.length, REQUEST_PAGE_SIZE), MAX_REQUEST_PAGE_SIZE);
  const [rawBuckets, userStats, requestPage, chain] = await Promise.all([
    fetchFetchTotals(queryWindow, state.requestFilters.userId),
    fetchUserFetchStats(queryWindow),
    fetchRequestPage(queryWindow, state.requestFilters, null, limit),
    fetchAuditChain(),
  ]);
  const buckets = rawBuckets.map(getBucketCounts);
  state.userStats = userStats;
  state.requestItems = requestPage.items;
  state.requestNextCursor = requestPage.next_cursor;
  renderRequestUserFilter();
  renderStatTiles(getSeriesTotals(buckets), chain);
  hideTooltip();
  document.getElementById(ELEMENT_IDS.totalsChart).replaceChildren(
    buildSeriesLegend(getSeriesTotals(buckets)), buildStackedBarChart(buckets, queryWindow.bucket, false), buildBucketDataTable(buckets, queryWindow.bucket),
  );
  renderRequestTable();
}

async function loadMoreRequests() {
  const requestPage = await fetchRequestPage(getQueryWindow(), state.requestFilters, state.requestNextCursor, REQUEST_PAGE_SIZE);
  state.requestItems = [...state.requestItems, ...requestPage.items];
  state.requestNextCursor = requestPage.next_cursor;
  renderRequestTable();
}

function resetRequestPaging() {
  state.requestItems = [];
  state.requestNextCursor = null;
}

// ---------- Retrievals view ----------

function getStatusCountTotal(statusCounts) {
  return REQUEST_STATUSES.reduce((total, requestStatus) => total + statusCounts[requestStatus.value], 0);
}

function getStatusShareText(count, total) {
  return `${total === 0 ? 0 : getPercentText(count / total)}% | ${count}`;
}

function getRequestStatus(value) {
  return REQUEST_STATUSES.find((requestStatus) => requestStatus.value === value);
}

function bindActivation(element, { focusKey, label, isPressed }, onActivate) {
  element.setAttribute(DONUT_FOCUS_KEY_ATTRIBUTE, focusKey);
  element.setAttribute("tabindex", "0");
  element.setAttribute("role", "button");
  element.setAttribute("aria-label", label);
  element.setAttribute("aria-pressed", String(isPressed));
  element.addEventListener("click", onActivate);
  element.addEventListener("keydown", (event) => {
    if (!ACTIVATION_KEYS.has(event.key)) return;
    event.preventDefault();
    onActivate();
  });
}

// Each slice is a dashed circle stroke: unlike an SVG arc, it still draws when one status holds 100%.
function buildDonutSlices(statusCounts, total) {
  const circumference = 2 * Math.PI * DONUT_LAYOUT.radius;
  const nonEmptyStatuses = REQUEST_STATUSES.filter((requestStatus) => statusCounts[requestStatus.value] > 0);
  const gap = nonEmptyStatuses.length > 1 ? DONUT_LAYOUT.sliceGap : 0;
  let offset = 0;
  return nonEmptyStatuses.map((requestStatus) => {
    const count = statusCounts[requestStatus.value];
    const length = (count / total) * circumference;
    const isSelected = state.selectedRequestStatus === requestStatus.value;
    const isDimmed = state.selectedRequestStatus !== null && !isSelected;
    const slice = createSvgElement("circle", {
      class: `donut-slice slice-${requestStatus.className}${isSelected ? " selected" : ""}${isDimmed ? " dimmed" : ""}`,
      cx: 0, cy: 0, r: DONUT_LAYOUT.radius,
      "stroke-dasharray": `${Math.max(length - gap, 1)} ${circumference}`,
      "stroke-dashoffset": -offset,
    });
    const activation = { focusKey: `slice-${requestStatus.value}`, label: `${requestStatus.label}: ${getStatusShareText(count, total)}`, isPressed: isSelected };
    bindActivation(slice, activation, () => selectRequestStatus(requestStatus.value));
    offset += length;
    return slice;
  });
}

function buildDonutCenter(total) {
  const center = createSvgElement("g", { class: `donut-center${state.selectedRequestStatus === null ? " selected" : ""}` });
  center.append(createSvgElement("circle", { cx: 0, cy: 0, r: DONUT_LAYOUT.radius - 20 }));
  const lines = [`${TOTAL_LABEL}:`, total === 0 ? "0" : `100% | ${total}`];
  lines.forEach((line, index) => {
    const text = createSvgElement("text", { x: 0, y: (index - 0.5) * 14 + 4, "text-anchor": "middle" });
    text.textContent = line;
    center.append(text);
  });
  const activation = { focusKey: DONUT_CENTER_FOCUS_KEY, label: `${ALL_REQUESTS_TITLE}: ${total}`, isPressed: state.selectedRequestStatus === null };
  bindActivation(center, activation, () => selectRequestStatus(null));
  return center;
}

function buildDonutChart(statusCounts, total) {
  const half = DONUT_LAYOUT.size / 2;
  const svg = createSvgElement("svg", { viewBox: `${-half} ${-half} ${DONUT_LAYOUT.size} ${DONUT_LAYOUT.size}`, role: "group" });
  // Rotated so the first slice starts at twelve o'clock instead of three.
  const ring = createSvgElement("g", { transform: "rotate(-90)" });
  if (total === 0) ring.append(createSvgElement("circle", { class: "donut-empty", cx: 0, cy: 0, r: DONUT_LAYOUT.radius }));
  ring.append(...buildDonutSlices(statusCounts, total));
  svg.append(ring, buildDonutCenter(total));
  return svg;
}

function buildDonutLabel(requestStatus, count, total) {
  const isSelected = state.selectedRequestStatus === requestStatus.value;
  const detailsButton = createElement("button", { className: "details-button", text: DETAILS_BUTTON_TEXT, attributes: { type: "button", "aria-pressed": String(isSelected), [DONUT_FOCUS_KEY_ATTRIBUTE]: `details-${requestStatus.value}` } });
  detailsButton.addEventListener("click", () => selectRequestStatus(requestStatus.value));
  return createElement("div", { className: `donut-label ${requestStatus.className}` }, [
    createElement("div", { className: "donut-label-head" }, [
      createElement("span", { className: "donut-label-icon", text: requestStatus.icon, attributes: { "aria-hidden": "true" } }),
      createElement("div", {}, [
        createElement("div", { text: `${requestStatus.label}:` }),
        createElement("div", { className: "donut-label-share", text: getStatusShareText(count, total) }),
      ]),
    ]),
    detailsButton,
  ]);
}

// Every click and auto-refresh rebuilds the donut; without this, keyboard focus would fall back to the page.
function getFocusedDonutKey(container) {
  return container.contains(document.activeElement) ? document.activeElement.getAttribute(DONUT_FOCUS_KEY_ATTRIBUTE) : null;
}

function renderStatusDonut(statusCounts) {
  const total = getStatusCountTotal(statusCounts);
  const container = document.getElementById(ELEMENT_IDS.statusDonut);
  const focusedKey = getFocusedDonutKey(container);
  container.replaceChildren(
    buildDonutChart(statusCounts, total),
    ...REQUEST_STATUSES.map((requestStatus) => buildDonutLabel(requestStatus, statusCounts[requestStatus.value], total)),
  );
  if (focusedKey) container.querySelector(`[${DONUT_FOCUS_KEY_ATTRIBUTE}="${focusedKey}"]`)?.focus();
}

function buildRetrievalListItem(summary) {
  const requestStatus = getRequestStatus(summary.status);
  const prompt = summary.prompt || EMPTY_CELL_TEXT;
  const button = createElement("button", { attributes: { type: "button", title: summary.prompt || "" } }, [
    createElement("span", { className: `status-marker ${requestStatus.markerClass}`, attributes: { "aria-label": requestStatus.label } }),
    createElement("span", { className: "retrieval-user", text: summary.user_name || summary.user_id }),
    createElement("span", { className: "retrieval-time", text: formatDateTime(summary.occurred_at) }),
    createElement("span", { className: "retrieval-prompt", text: prompt }),
  ]);
  button.addEventListener("click", () => runAndReportErrors(() => openTracePanel(summary.request_id)));
  return createElement("li", {}, [button]);
}

function renderRetrievalList() {
  const requestStatus = getRequestStatus(state.selectedRequestStatus);
  document.getElementById(ELEMENT_IDS.retrievalListTitle).textContent = requestStatus ? requestStatus.label : ALL_REQUESTS_TITLE;
  document.getElementById(ELEMENT_IDS.retrievalList).replaceChildren(...state.retrievalItems.map(buildRetrievalListItem));
  document.getElementById(ELEMENT_IDS.retrievalListEmpty).hidden = state.retrievalItems.length > 0;
  document.getElementById(ELEMENT_IDS.loadMoreRetrievals).hidden = state.retrievalNextCursor === null;
}

async function loadRetrievalsView() {
  // Clicking between slices quickly must not let a slow earlier response overwrite the current one.
  const loadNumber = ++state.retrievalLoadNumber;
  const queryWindow = getRetrievalQueryWindow();
  const limit = Math.min(Math.max(state.retrievalItems.length, REQUEST_PAGE_SIZE), MAX_REQUEST_PAGE_SIZE);
  const [statusCounts, requestPage] = await Promise.all([
    fetchRequestStatusCounts(queryWindow),
    fetchRequestPage(queryWindow, { status: state.selectedRequestStatus }, null, limit),
  ]);
  if (loadNumber !== state.retrievalLoadNumber) return;
  state.retrievalItems = requestPage.items;
  state.retrievalNextCursor = requestPage.next_cursor;
  renderStatusDonut(statusCounts);
  renderRetrievalList();
}

async function loadMoreRetrievals() {
  // A slice clicked while this page loads replaces the list; rows of the old status must not be appended to it.
  const loadNumber = state.retrievalLoadNumber;
  const requestPage = await fetchRequestPage(getRetrievalQueryWindow(), { status: state.selectedRequestStatus }, state.retrievalNextCursor, REQUEST_PAGE_SIZE);
  if (loadNumber !== state.retrievalLoadNumber) return;
  state.retrievalItems = [...state.retrievalItems, ...requestPage.items];
  state.retrievalNextCursor = requestPage.next_cursor;
  renderRetrievalList();
}

function resetRetrievalPaging() {
  state.retrievalItems = [];
  state.retrievalNextCursor = null;
  document.getElementById(ELEMENT_IDS.retrievalList).scrollTop = 0;
}

function selectRequestStatus(requestStatus) {
  state.selectedRequestStatus = requestStatus;
  resetRetrievalPaging();
  runAndReportErrors(loadRetrievalsView);
}

// ---------- Trace panel ----------

function getStepDepths(steps) {
  const depthByStepId = new Map();
  for (const step of steps) depthByStepId.set(step.step_id, step.parent_step_id ? (depthByStepId.get(step.parent_step_id) ?? 0) + 1 : 0);
  return depthByStepId;
}

function buildTraceStep(step, depth) {
  const head = createElement("div", { className: "trace-step-head" }, [
    createElement("span", { className: "trace-step-kind", text: step.kind.replaceAll("_", " ") }),
    createElement("span", { className: "trace-step-name", text: step.name }),
    buildBadge({ className: STEP_OUTCOME_BADGES[step.outcome] || BADGE_CLASSES.neutral, label: step.outcome }),
    createElement("span", { className: "trace-step-duration", text: `${Math.round(step.duration_ms)}${DURATION_UNIT}` }),
  ]);
  const hasDetail = step.detail && Object.keys(step.detail).length > 0;
  return createElement("li", { className: "trace-step", attributes: { style: `margin-left: ${depth * 1.2}rem` } }, [
    head,
    step.reason ? createElement("div", { className: "trace-step-reason", text: step.reason }) : null,
    hasDetail ? createElement("details", {}, [createElement("summary", { text: TEXT.detail }), createElement("pre", { text: JSON.stringify(step.detail, null, 2) })]) : null,
  ]);
}

function buildTraceSummary(summary) {
  const entries = [
    [TRACE_SUMMARY_LABELS.requestId, document.createTextNode(summary.request_id)],
    [TRACE_SUMMARY_LABELS.user, document.createTextNode(summary.user_name || summary.user_id)],
    [TRACE_SUMMARY_LABELS.time, document.createTextNode(formatDateTime(summary.occurred_at))],
    [TRACE_SUMMARY_LABELS.outcome, buildBadge(getRequestOutcomeBadge(summary))],
    [TRACE_SUMMARY_LABELS.reason, document.createTextNode(summary.reason || EMPTY_CELL_TEXT)],
  ];
  return createElement("dl", { className: "trace-summary" }, entries.flatMap(([label, value]) => [createElement("dt", { text: label }), createElement("dd", {}, [value])]));
}

async function openTracePanel(requestId) {
  const trace = await fetchRequestTrace(requestId);
  const depths = getStepDepths(trace.steps);
  document.getElementById(ELEMENT_IDS.traceBody).replaceChildren(
    buildTraceSummary(trace.summary),
    createElement("ol", { className: "trace-steps" }, trace.steps.map((step) => buildTraceStep(step, depths.get(step.step_id)))),
  );
  document.getElementById(ELEMENT_IDS.tracePanel).hidden = false;
  document.getElementById(ELEMENT_IDS.closeTrace).focus();
}

function closeTracePanel() {
  document.getElementById(ELEMENT_IDS.tracePanel).hidden = true;
}

// ---------- Policy view ----------

function isBlockingRule(policyRule) {
  return policyRule.rule.head.predicate === VIOLATION_PREDICATE;
}

function formatDatalogTerm(term) {
  if (term.startsWith(VARIABLE_PREFIX)) {
    const name = term.slice(VARIABLE_PREFIX.length);
    return name.charAt(0).toUpperCase() + name.slice(1);
  }
  return PLAIN_DATALOG_CONSTANT.test(term) ? term : `'${term.replaceAll("'", "\\'")}'`;
}

function formatDatalogAtom(atom) {
  return `${atom.predicate}(${atom.terms.map(formatDatalogTerm).join(", ")})`;
}

function formatDatalogRule(rule) {
  return `${formatDatalogAtom(rule.head)} :-\n    ${rule.body.map(formatDatalogAtom).join(",\n    ")}.`;
}

function getRuleRelations(rule) {
  return rule.body
    .filter((atom) => atom.predicate === KNOWS_PREDICATE)
    .map((atom) => atom.terms[KNOWS_RELATION_TERM_INDEX])
    .filter((relation) => relation && !relation.startsWith(VARIABLE_PREFIX));
}

function getKnownRelations(policyRules) {
  return [...new Set(policyRules.flatMap((policyRule) => getRuleRelations(policyRule.rule)))].sort();
}

function buildBlockingRule(ruleId, relations) {
  const body = relations.map((relation, index) => ({
    predicate: KNOWS_PREDICATE, terms: [RECIPIENT_VARIABLE, NEW_RULE_SUBJECT_VARIABLE, relation, `${NEW_RULE_VALUE_VARIABLE_PREFIX}${index + 1}`],
  }));
  // The rule id doubles as the violation's reason, so denials and traces name the rule that fired.
  return {
    rule_id: ruleId, enabled: true,
    rule: { name: ruleId, head: { predicate: VIOLATION_PREDICATE, terms: [RECIPIENT_VARIABLE, NEW_RULE_SUBJECT_VARIABLE, ruleId] }, body },
  };
}

function buildPolicyTile(label, value, detail, valueClassName) {
  return createElement("div", { className: "stat-tile" }, [
    createElement("div", { className: "stat-label", text: label }),
    createElement("div", { className: valueClassName ? `stat-value ${valueClassName}` : "stat-value", text: String(value) }),
    createElement("div", { className: "stat-detail", text: detail }),
  ]);
}

function renderPolicyTiles(policy) {
  const blockingRules = policy.rules.filter(isBlockingRule);
  const enabledBlockingCount = blockingRules.filter((policyRule) => policyRule.enabled).length;
  document.getElementById(ELEMENT_IDS.policyTiles).replaceChildren(
    buildPolicyTile(POLICY_TEXT.version, policy.version, `${POLICY_TEXT.revision}${policy.revision}`, "stat-value-text"),
    buildPolicyTile(POLICY_TEXT.rules, policy.rules.length, POLICY_TEXT.blockingAndDerived(blockingRules.length, policy.rules.length - blockingRules.length)),
    buildPolicyTile(POLICY_TEXT.enabledBlocking, enabledBlockingCount, POLICY_TEXT.ofBlocking(blockingRules.length)),
  );
}

function buildRuleToggle(policyRule) {
  const checkbox = createElement("input", { attributes: { type: "checkbox", role: "switch" } });
  checkbox.checked = policyRule.enabled;
  checkbox.addEventListener("change", () => {
    runPolicyEdit((rules) => rules.map((rule) => (rule.rule_id === policyRule.rule_id ? { ...rule, enabled: checkbox.checked } : rule)));
  });
  return createElement("label", { className: "switch" }, [checkbox, createElement("span", { className: "switch-track", attributes: { "aria-hidden": "true" } }), createElement("span", { text: POLICY_TEXT.enabled })]);
}

function buildRuleDeleteButton(policyRule) {
  const button = createElement("button", { className: "link-button", text: POLICY_TEXT.deleteRule, attributes: { type: "button" } });
  button.addEventListener("click", () => {
    if (!window.confirm(POLICY_TEXT.confirmDelete(policyRule.rule_id))) return;
    runPolicyEdit((rules) => rules.filter((rule) => rule.rule_id !== policyRule.rule_id));
  });
  return button;
}

function buildRuleItem(policyRule) {
  const blocking = isBlockingRule(policyRule);
  const markerClass = policyRule.enabled ? (blocking ? "status-critical" : "status-good") : "status-disabled";
  const relations = getRuleRelations(policyRule.rule);
  const header = createElement("div", { className: "rule-header" }, [
    createElement("span", { className: `status-marker ${markerClass}` }),
    createElement("span", { className: "rule-name", text: policyRule.rule_id }),
    buildBadge(RULE_KIND_BADGES[blocking ? "blocking" : "derivation"]),
    createElement("span", { className: "rule-actions" }, [buildRuleToggle(policyRule), buildRuleDeleteButton(policyRule)]),
  ]);
  const relationChips = relations.length === 0 ? null : createElement("div", { className: "rule-relations" }, [
    createElement("span", { className: "rule-relations-label", text: POLICY_TEXT.combines }),
    ...relations.map((relation) => createElement("span", { className: "chip", text: relation })),
  ]);
  return createElement("li", { className: policyRule.enabled ? "rule-item" : "rule-item disabled" }, [
    header, relationChips, createElement("pre", { className: "datalog", text: formatDatalogRule(policyRule.rule) }),
  ]);
}

function renderKnownRelations(policy) {
  document.getElementById(ELEMENT_IDS.knownRelations).replaceChildren(
    ...getKnownRelations(policy.rules).map((relation) => createElement("option", { attributes: { value: relation } })),
  );
}

function fillPolicyJsonEditor() {
  document.getElementById(ELEMENT_IDS.policyJsonInput).value = JSON.stringify(state.policy.rules, null, JSON_INDENT);
}

function renderPolicy(policy) {
  state.policy = policy;
  renderPolicyTiles(policy);
  document.getElementById(ELEMENT_IDS.policyRuleList).replaceChildren(...policy.rules.map(buildRuleItem));
  renderKnownRelations(policy);
}

function showPolicyError(message) {
  const errorElement = document.getElementById(ELEMENT_IDS.policyError);
  errorElement.textContent = message;
  errorElement.hidden = !message;
}

async function loadPolicyView() {
  const policy = await fetchPolicyRules();
  // Re-rendering an unchanged list on every auto-refresh would steal keyboard focus from the toggles.
  if (state.policy && state.policy.revision === policy.revision) return;
  renderPolicy(policy);
}

async function applyPolicyChange(sendChange) {
  showPolicyError("");
  try {
    renderPolicy(await sendChange());
    return true;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.status === HTTP_CONFLICT) {
      renderPolicy(await fetchPolicyRules());
      showPolicyError(POLICY_TEXT.staleRevision);
    } else {
      // A rejected change leaves the file as it was; re-render so toggles snap back to the saved state.
      renderPolicy(state.policy);
      showPolicyError(error.message);
    }
    return false;
  }
}

function runPolicyEdit(editRules) {
  return runAndReportErrors(() => applyPolicyChange(() => savePolicyRules(editRules(state.policy.rules), state.policy.revision)));
}

function addRuleFromForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const ruleId = document.getElementById(ELEMENT_IDS.newRuleId).value.trim();
  const relations = [...form.querySelectorAll(".new-rule-relation")].map((input) => input.value.trim()).filter(Boolean);
  runAndReportErrors(async () => {
    const saved = await applyPolicyChange(() => savePolicyRules([...state.policy.rules, buildBlockingRule(ruleId, relations)], state.policy.revision));
    if (saved) form.reset();
  });
}

function savePolicyJson() {
  let rules;
  try {
    rules = JSON.parse(document.getElementById(ELEMENT_IDS.policyJsonInput).value);
  } catch (error) {
    showPolicyError(`${POLICY_TEXT.invalidJson}${error.message}`);
    return;
  }
  if (!Array.isArray(rules)) {
    showPolicyError(POLICY_TEXT.notAList);
    return;
  }
  runAndReportErrors(async () => {
    if (await applyPolicyChange(() => savePolicyRules(rules, state.policy.revision))) fillPolicyJsonEditor();
  });
}

function restoreDefaultPolicy() {
  if (!window.confirm(POLICY_TEXT.confirmRestore)) return;
  runAndReportErrors(async () => {
    if (await applyPolicyChange(restorePolicyRules)) fillPolicyJsonEditor();
  });
}

function bindPolicyControls() {
  document.getElementById(ELEMENT_IDS.policyRuleForm).addEventListener("submit", addRuleFromForm);
  document.getElementById(ELEMENT_IDS.savePolicyJson).addEventListener("click", savePolicyJson);
  document.getElementById(ELEMENT_IDS.restorePolicy).addEventListener("click", restoreDefaultPolicy);
  // Filled on opening, not on refresh, so an auto-refresh never overwrites an edit in progress.
  document.getElementById(ELEMENT_IDS.policyJson).addEventListener("toggle", (event) => {
    if (event.currentTarget.open && state.policy) fillPolicyJsonEditor();
  });
}

// ---------- Navigation, auth, refresh ----------

const VIEW_LOADERS = { [VIEW_USERS]: loadUsersView, [VIEW_REQUESTS]: loadRequestsView, [VIEW_RETRIEVALS]: loadRetrievalsView, [VIEW_POLICY]: loadPolicyView };

function getViewFromLocation() {
  const view = window.location.hash.replace("#", "");
  return Object.hasOwn(VIEW_SECTION_IDS, view) ? view : DEFAULT_VIEW;
}

function showView(view) {
  state.view = view;
  for (const [sectionView, sectionId] of Object.entries(VIEW_SECTION_IDS)) document.getElementById(sectionId).hidden = view !== sectionView;
  for (const link of document.querySelectorAll(".nav-link")) link.classList.toggle("active", link.dataset.view === view);
}

function loadCurrentView() {
  return VIEW_LOADERS[state.view]();
}

function showSignIn(rejected) {
  document.getElementById(ELEMENT_IDS.signInError).hidden = !rejected;
  document.getElementById(ELEMENT_IDS.signIn).hidden = false;
  document.getElementById(ELEMENT_IDS.tokenInput).focus();
}

function renderRefreshTime() {
  document.getElementById(ELEMENT_IDS.refreshStatus).textContent = `${TEXT.refreshedAt}${new Date().toLocaleTimeString()}`;
}

async function runAndReportErrors(action) {
  try {
    await action();
    renderRefreshTime();
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      const hadToken = Boolean(getStoredToken());
      storeToken("");
      showSignIn(hadToken);
      return;
    }
    console.error(error);
    document.getElementById(ELEMENT_IDS.refreshStatus).textContent = `${TEXT.loadFailed}${error.message}`;
  }
}

function refreshCurrentView() {
  if (!getStoredToken() || document.hidden) return;
  runAndReportErrors(loadCurrentView);
}

function bindFilters() {
  const rangeOptions = TIME_RANGES.map((range) => ({ value: range.id, label: range.label }));
  for (const select of document.querySelectorAll(".range-select")) {
    fillSelect(select, rangeOptions, state.timeRangeId);
    select.addEventListener("change", () => {
      state.timeRangeId = select.value;
      for (const other of document.querySelectorAll(".range-select")) other.value = select.value;
      resetRequestPaging();
      refreshCurrentView();
    });
  }
  const sortSelect = document.getElementById(ELEMENT_IDS.userSort);
  fillSelect(sortSelect, USER_SORT_OPTIONS.map((option) => ({ value: option.id, label: option.label })), state.userSortId);
  sortSelect.addEventListener("change", () => { state.userSortId = sortSelect.value; renderUserList(); });
  document.getElementById(ELEMENT_IDS.userSearch).addEventListener("input", (event) => { state.userSearch = event.target.value; renderUserList(); });

  const requestFilterSelects = [
    { id: "request-user-filter", filterKey: "userId", options: null },
    { id: "request-outcome-filter", filterKey: "outcome", options: OUTCOME_FILTER_OPTIONS },
    { id: "request-denied-at-filter", filterKey: "deniedAt", options: DENIED_AT_FILTER_OPTIONS },
  ];
  for (const { id, filterKey, options } of requestFilterSelects) {
    const select = document.getElementById(id);
    if (options) fillSelect(select, options, ALL_OPTION_VALUE);
    select.addEventListener("change", () => {
      state.requestFilters[filterKey] = select.value;
      resetRequestPaging();
      refreshCurrentView();
    });
  }
  document.getElementById(ELEMENT_IDS.loadMoreRequests).addEventListener("click", () => runAndReportErrors(loadMoreRequests));

  const retrievalRangeSelect = document.getElementById(ELEMENT_IDS.retrievalRange);
  fillSelect(retrievalRangeSelect, RETRIEVAL_TIME_RANGES.map((range) => ({ value: range.id, label: range.label })), state.retrievalTimeRangeId);
  retrievalRangeSelect.addEventListener("change", () => {
    state.retrievalTimeRangeId = retrievalRangeSelect.value;
    resetRetrievalPaging();
    refreshCurrentView();
  });
  document.getElementById(ELEMENT_IDS.loadMoreRetrievals).addEventListener("click", () => runAndReportErrors(loadMoreRetrievals));
}

function bindChrome() {
  window.addEventListener("hashchange", () => { showView(getViewFromLocation()); refreshCurrentView(); });
  document.getElementById(ELEMENT_IDS.closeTrace).addEventListener("click", closeTracePanel);
  document.getElementById(ELEMENT_IDS.tracePanel).addEventListener("click", (event) => { if (event.target.id === ELEMENT_IDS.tracePanel) closeTracePanel(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeTracePanel(); });
  document.getElementById(ELEMENT_IDS.signOut).addEventListener("click", () => { storeToken(""); showSignIn(false); });
  document.getElementById(ELEMENT_IDS.signInForm).addEventListener("submit", (event) => {
    event.preventDefault();
    storeToken(document.getElementById(ELEMENT_IDS.tokenInput).value.trim());
    document.getElementById(ELEMENT_IDS.signIn).hidden = true;
    refreshCurrentView();
  });
  document.addEventListener("visibilitychange", refreshCurrentView);
}

function start() {
  bindFilters();
  bindPolicyControls();
  bindChrome();
  showView(getViewFromLocation());
  if (getStoredToken()) refreshCurrentView();
  else showSignIn(false);
  setInterval(refreshCurrentView, AUTO_REFRESH_MILLISECONDS);
}

start();
