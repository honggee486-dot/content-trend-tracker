const SCHEMA_VERSION = "1.0";
const SOURCE_NAME = "content-trend-tracker";
const REPORT_SCHEMA_VERSION = "1.1";

const payloadInput = document.getElementById("payload");
const readClipboardButton = document.getElementById("readClipboard");
const diagnoseEditorButton = document.getElementById("diagnoseEditor");
const fillEditorButton = document.getElementById("fillEditor");
const summary = document.getElementById("summary");
const summaryTitle = document.getElementById("summaryTitle");
const summaryPlatform = document.getElementById("summaryPlatform");
const summaryExpiry = document.getElementById("summaryExpiry");
const reportSection = document.getElementById("reportSection");
const compatibilityReport = document.getElementById("compatibilityReport");
const copyCompatibilityReportButton = document.getElementById(
  "copyCompatibilityReport"
);
const statusBox = document.getElementById("status");

let currentPayload = null;
let currentCompatibilityReport = "";

const FIELD_LABELS = {
  title: "제목",
  body: "본문",
  tags: "태그",
  meta_description: "검색 설명",
};

function setStatus(message, isError = false) {
  statusBox.textContent = String(message || "");
  statusBox.style.color = isError ? "#b3261e" : "";
}

function canonicalize(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalize(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys
      .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function hostMatches(hostname, patterns) {
  const host = String(hostname || "").toLowerCase().replace(/\.$/, "");
  return patterns.some((patternValue) => {
    const pattern = String(patternValue || "").toLowerCase().replace(/\.$/, "");
    if (!pattern) {
      return false;
    }
    if (pattern.startsWith("*.")) {
      const suffix = pattern.slice(1);
      return host.endsWith(suffix) && host !== suffix.slice(1);
    }
    return host === pattern;
  });
}

async function validatePayload(text, currentHostname = "") {
  let payload;
  try {
    payload = JSON.parse(String(text || ""));
  } catch {
    throw new Error("전달 JSON을 읽을 수 없습니다.");
  }

  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("전달 데이터는 JSON 객체여야 합니다.");
  }
  if (payload.schema_version !== SCHEMA_VERSION) {
    throw new Error("지원하지 않는 전달 스키마입니다.");
  }
  if (payload.source !== SOURCE_NAME) {
    throw new Error("콘텐츠 트렌드 트래커가 만든 전달 데이터가 아닙니다.");
  }

  const { checksum, ...unsigned } = payload;
  const expected = `sha256:${await sha256Hex(canonicalize(unsigned))}`;
  if (checksum !== expected) {
    throw new Error("전달 데이터 체크섬이 일치하지 않습니다.");
  }

  const expiresAt = Date.parse(payload.expires_at);
  if (!Number.isFinite(expiresAt) || Date.now() >= expiresAt) {
    throw new Error("전달 데이터의 10분 유효시간이 지났습니다.");
  }

  const safety = payload.safety || {};
  if (
    safety.requires_user_action !== true ||
    safety.may_submit !== false ||
    safety.contains_credentials !== false ||
    safety.stores_browser_session !== false
  ) {
    throw new Error("전달 데이터의 안전 계약이 올바르지 않습니다.");
  }

  const target = payload.target || {};
  const content = payload.content || {};
  const patterns = target.allowed_host_patterns;
  if (!Array.isArray(patterns) || patterns.length === 0) {
    throw new Error("허용된 블로그 편집기 호스트가 없습니다.");
  }
  if (currentHostname && !hostMatches(currentHostname, patterns)) {
    throw new Error("현재 탭은 이 전달 데이터가 허용한 블로그 편집기가 아닙니다.");
  }
  if (!String(content.title || "").trim()) {
    throw new Error("입력할 제목이 없습니다.");
  }
  if (!String(content.body || "").trim()) {
    throw new Error("입력할 본문이 없습니다.");
  }
  if (!Array.isArray(content.tags)) {
    throw new Error("태그 형식이 올바르지 않습니다.");
  }
  if (!Array.isArray(content.image_slots) || content.image_slots.length !== 3) {
    throw new Error("이미지 슬롯은 정확히 3개여야 합니다.");
  }

  return payload;
}

function showSummary(payload) {
  summary.hidden = false;
  summaryTitle.textContent = payload.content.title;
  summaryPlatform.textContent = `대상: ${
    payload.target.profile_name || payload.target.platform
  }`;
  summaryExpiry.textContent = `만료: ${new Date(
    payload.expires_at
  ).toLocaleTimeString()}`;
  fillEditorButton.disabled = false;
}

async function getCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || typeof tab.id !== "number" || !tab.url) {
    throw new Error("현재 활성 탭을 확인할 수 없습니다.");
  }
  return tab;
}

async function parseInputForCurrentTab() {
  const tab = await getCurrentTab();
  const hostname = new URL(tab.url).hostname;
  const payload = await validatePayload(payloadInput.value, hostname);
  return { tab, payload };
}

async function installContentScript(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content.js"],
  });
}

async function sendEditorMessage(tabId, message) {
  await installContentScript(tabId);
  return chrome.tabs.sendMessage(tabId, message);
}

function formatDiagnosticLines(diagnostics) {
  if (!diagnostics) {
    return ["편집기 진단 정보를 받지 못했습니다."];
  }

  const lines = [
    `편집기 유형: ${diagnostics.adapter || "generic"}`,
    `탐색 문서: ${Number(diagnostics.accessible_documents || 0)}개`,
    `접근 불가 iframe: ${Number(diagnostics.blocked_iframe_count || 0)}개`,
    `구조 후보: ${Number(diagnostics.candidate_control_count || 0)}개${
      diagnostics.candidate_controls_truncated ? " · 최대 40개만 포함" : ""
    }`,
  ];

  const matches = diagnostics.matches || {};
  for (const fieldName of Object.keys(FIELD_LABELS)) {
    const match = matches[fieldName] || {};
    if (match.found) {
      lines.push(
        `${FIELD_LABELS[fieldName]}: 후보 발견 · ${match.frame_path || "top"} · ${
          match.selector || "선택자 미상"
        }`
      );
    } else {
      lines.push(`${FIELD_LABELS[fieldName]}: 후보 미발견`);
    }
  }
  return lines;
}

function sanitizeCandidateControls(source) {
  const controls = Array.isArray(source?.candidate_controls)
    ? source.candidate_controls
    : [];
  return controls.slice(0, 40).map((item) => ({
    frame_path: String(item?.frame_path || ""),
    tag_name: String(item?.tag_name || ""),
    input_type: String(item?.input_type || ""),
    id: String(item?.id || ""),
    name: String(item?.name || ""),
    role: String(item?.role || ""),
    placeholder: String(item?.placeholder || ""),
    aria_label: String(item?.aria_label || ""),
    data_placeholder: String(item?.data_placeholder || ""),
    class_names: Array.isArray(item?.class_names)
      ? item.class_names.slice(0, 8).map((value) => String(value || ""))
      : [],
    contenteditable: Boolean(item?.contenteditable),
  }));
}

function sanitizeDiagnostics(diagnostics) {
  const source = diagnostics || {};
  const sourceMatches = source.matches || {};
  const matches = {};
  for (const fieldName of Object.keys(FIELD_LABELS)) {
    const match = sourceMatches[fieldName] || {};
    matches[fieldName] = {
      found: Boolean(match.found),
      selector: String(match.selector || ""),
      frame_path: String(match.frame_path || ""),
      tag_name: String(match.tag_name || ""),
      contenteditable: Boolean(match.contenteditable),
    };
  }
  const candidateControls = sanitizeCandidateControls(source);
  return {
    adapter: String(source.adapter || "generic"),
    accessible_documents: Number(source.accessible_documents || 0),
    blocked_iframe_count: Number(source.blocked_iframe_count || 0),
    blocked_iframes: Array.isArray(source.blocked_iframes)
      ? source.blocked_iframes.map((item) => ({
          frame_path: String(item?.frame_path || ""),
          reason: String(item?.reason || ""),
        }))
      : [],
    matches,
    candidate_controls: candidateControls,
    candidate_control_count: Math.max(
      candidateControls.length,
      Number(source.candidate_control_count || 0)
    ),
    candidate_controls_truncated:
      Boolean(source.candidate_controls_truncated) ||
      Number(source.candidate_control_count || 0) > candidateControls.length,
  };
}

function buildCompatibilityReport({ tab, action, response, payload = null }) {
  let hostname = "";
  try {
    hostname = new URL(tab.url).hostname;
  } catch {
    hostname = "";
  }

  const fields = response?.fields || {};
  return {
    schema_version: REPORT_SCHEMA_VERSION,
    report_type: "chrome_editor_compatibility",
    source: SOURCE_NAME,
    generated_at: new Date().toISOString(),
    page: {
      hostname,
    },
    expected_platform: String(payload?.target?.platform || ""),
    action: String(action || "diagnose"),
    result: {
      ok: response?.ok === true,
      fields: {
        title: Boolean(fields.title),
        body: Boolean(fields.body),
        tags: Boolean(fields.tags),
        meta_description: Boolean(fields.meta_description),
      },
      diagnostics: sanitizeDiagnostics(response?.diagnostics),
    },
    safety: {
      includes_editor_values: false,
      includes_payload_content: false,
      includes_credentials: false,
      includes_url_query_or_hash: false,
      stores_browser_session: false,
      may_submit: false,
    },
  };
}

function showCompatibilityReport(report) {
  currentCompatibilityReport = JSON.stringify(report, null, 2);
  compatibilityReport.value = currentCompatibilityReport;
  reportSection.hidden = false;
  reportSection.open = true;
  copyCompatibilityReportButton.disabled = false;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    compatibilityReport.focus();
    compatibilityReport.select();
    const copied = document.execCommand("copy");
    compatibilityReport.setSelectionRange(0, 0);
    if (!copied) {
      throw new Error("클립보드 복사가 차단됐습니다. 보고서 칸에서 직접 복사하세요.");
    }
  }
}

async function copyCompatibilityReport() {
  if (!currentCompatibilityReport) {
    setStatus("먼저 입력칸 진단 또는 편집기 입력을 실행하세요.", true);
    return;
  }
  copyCompatibilityReportButton.disabled = true;
  try {
    await copyText(currentCompatibilityReport);
    const prior = statusBox.textContent.trim();
    setStatus(
      [prior, "호환성 보고서를 복사했습니다. 원고와 로그인 정보는 포함되지 않습니다."]
        .filter(Boolean)
        .join("\n")
    );
  } catch (error) {
    setStatus(error.message || String(error), true);
  } finally {
    copyCompatibilityReportButton.disabled = false;
  }
}

async function diagnoseCurrentEditor() {
  diagnoseEditorButton.disabled = true;
  try {
    const tab = await getCurrentTab();
    const response = await sendEditorMessage(tab.id, {
      type: "CTT_DIAGNOSE_EDITOR",
    });
    if (response) {
      showCompatibilityReport(
        buildCompatibilityReport({
          tab,
          action: "diagnose",
          response,
          payload: currentPayload,
        })
      );
    }
    if (!response || response.ok !== true) {
      throw new Error(response?.message || "편집기 진단 결과를 확인할 수 없습니다.");
    }
    setStatus(
      [response.message, ...formatDiagnosticLines(response.diagnostics)].join("\n")
    );
  } catch (error) {
    setStatus(error.message || String(error), true);
  } finally {
    diagnoseEditorButton.disabled = false;
  }
}

async function loadFromClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    payloadInput.value = text;
    const { payload } = await parseInputForCurrentTab();
    currentPayload = payload;
    showSummary(payload);
    setStatus(
      "전달 데이터를 확인했습니다. 입력칸 진단 후 현재 편집기에 입력할 수 있습니다."
    );
  } catch (error) {
    currentPayload = null;
    fillEditorButton.disabled = !payloadInput.value.trim();
    summary.hidden = true;
    setStatus(error.message || String(error), true);
  }
}

async function fillCurrentEditor() {
  fillEditorButton.disabled = true;
  try {
    const { tab, payload } = await parseInputForCurrentTab();
    currentPayload = payload;
    showSummary(payload);
    const response = await sendEditorMessage(tab.id, {
      type: "CTT_FILL_EDITOR",
      payload,
    });
    if (response) {
      showCompatibilityReport(
        buildCompatibilityReport({
          tab,
          action: "fill",
          response,
          payload,
        })
      );
    }
    if (!response || response.ok !== true) {
      const diagnosticLines = formatDiagnosticLines(response?.diagnostics);
      throw new Error(
        [
          response?.message || "편집기 입력 결과를 확인할 수 없습니다.",
          ...diagnosticLines,
        ].join("\n")
      );
    }

    const lines = [
      response.message,
      `제목: ${response.fields.title ? "입력됨" : "입력칸 미발견"}`,
      `본문: ${response.fields.body ? "입력됨" : "입력칸 미발견"}`,
      `태그: ${response.fields.tags ? "입력됨" : "입력칸 미발견"}`,
      `검색 설명: ${
        response.fields.meta_description ? "입력됨" : "입력칸 미발견"
      }`,
      ...formatDiagnosticLines(response.diagnostics),
      "이미지 3개는 프로그램의 배치 안내를 보며 직접 업로드하세요.",
    ];
    setStatus(lines.join("\n"));
  } catch (error) {
    setStatus(error.message || String(error), true);
  } finally {
    fillEditorButton.disabled = !payloadInput.value.trim();
  }
}

payloadInput.addEventListener("input", () => {
  currentPayload = null;
  fillEditorButton.disabled = !payloadInput.value.trim();
  summary.hidden = true;
  setStatus("JSON을 직접 붙여넣었다면 입력칸 진단 후 현재 편집기에 입력하세요.");
});

readClipboardButton.addEventListener("click", loadFromClipboard);
diagnoseEditorButton.addEventListener("click", diagnoseCurrentEditor);
fillEditorButton.addEventListener("click", fillCurrentEditor);
copyCompatibilityReportButton.addEventListener("click", copyCompatibilityReport);
