(() => {
  if (globalThis.__CTT_EDITOR_ASSISTANT_INSTALLED__) {
    return;
  }
  globalThis.__CTT_EDITOR_ASSISTANT_INSTALLED__ = true;

  const FIELD_NAMES = ["title", "body", "tags", "meta_description"];
  const CANDIDATE_LIMIT = 40;
  const CANDIDATE_SELECTOR = [
    'input:not([type="hidden"]):not([type="password"]):not([type="file"])',
    "textarea",
    '[contenteditable="true"]',
    '[role="textbox"]',
  ].join(", ");
  const EXCLUDED_INPUT_TYPES = new Set([
    "hidden",
    "password",
    "file",
    "button",
    "submit",
    "reset",
    "checkbox",
    "radio",
    "color",
    "range",
    "date",
    "datetime-local",
    "month",
    "time",
    "week",
  ]);

  const SELECTORS = {
    naver_blog: {
      title: [
        "textarea.se-title-text",
        "input.se-title-text",
        '[contenteditable="true"][data-placeholder*="제목"]',
        '[contenteditable="true"][aria-label*="제목"]',
        'textarea[placeholder*="제목"]',
        'input[placeholder*="제목"]',
      ],
      body: [
        '.se-main-container [contenteditable="true"]',
        '.se-section-text [contenteditable="true"]',
        '[contenteditable="true"][aria-label*="본문"]',
        '[contenteditable="true"][data-placeholder*="내용"]',
      ],
      tags: [
        'input[placeholder*="태그"]',
        'input[aria-label*="태그"]',
      ],
      meta_description: [],
    },
    tistory: {
      title: [
        "#post-title-inp",
        'textarea[placeholder*="제목"]',
        'input[placeholder*="제목"]',
      ],
      body: [
        ".ProseMirror",
        ".mce-content-body",
        ".CodeMirror-code",
        'textarea[aria-label*="본문"]',
        '[contenteditable="true"][role="textbox"]',
      ],
      tags: [
        'input[placeholder*="태그"]',
        'input[aria-label*="태그"]',
      ],
      meta_description: [
        'textarea[placeholder*="설명"]',
        'textarea[aria-label*="설명"]',
      ],
    },
    blogger: {
      title: [
        'input[aria-label*="Title"]',
        'input[aria-label*="제목"]',
        'input[placeholder*="Title"]',
        'input[placeholder*="제목"]',
      ],
      body: [
        '[contenteditable="true"][aria-label*="Post body"]',
        '[contenteditable="true"][aria-label*="본문"]',
        '.editable[contenteditable="true"]',
        '[contenteditable="true"][role="textbox"]',
      ],
      tags: [
        'input[aria-label*="Labels"]',
        'input[aria-label*="라벨"]',
        'input[placeholder*="Labels"]',
        'input[placeholder*="라벨"]',
      ],
      meta_description: [
        'textarea[aria-label*="Search description"]',
        'textarea[aria-label*="검색 설명"]',
        'textarea[placeholder*="Search description"]',
      ],
    },
    generic: {
      title: [
        'textarea[name*="title" i]',
        'input[name*="title" i]',
        'textarea[placeholder*="제목"]',
        'input[placeholder*="제목"]',
      ],
      body: [
        'textarea[name*="body" i]',
        'textarea[name*="content" i]',
        '[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"]',
      ],
      tags: [
        'input[name*="tag" i]',
        'input[placeholder*="태그"]',
        'input[placeholder*="tag" i]',
      ],
      meta_description: [
        'textarea[name*="description" i]',
        'textarea[placeholder*="설명"]',
        'textarea[placeholder*="description" i]',
      ],
    },
  };

  function hostMatches(hostname, patterns) {
    const host = String(hostname || "").toLowerCase().replace(/\.$/, "");
    return patterns.some((patternValue) => {
      const pattern = String(patternValue || "")
        .toLowerCase()
        .replace(/\.$/, "");
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

  function adapterCode(hostname) {
    const host = String(hostname || "").toLowerCase();
    if (host === "blog.naver.com") {
      return "naver_blog";
    }
    if (host === "www.tistory.com" || host.endsWith(".tistory.com")) {
      return "tistory";
    }
    if (host === "blogger.com" || host.endsWith(".blogger.com")) {
      return "blogger";
    }
    return "generic";
  }

  function collectDocumentContexts(rootDocument = document) {
    const contexts = [];
    const blockedFrames = [];
    const seenDocuments = new Set();

    function visit(currentDocument, framePath) {
      if (!currentDocument || seenDocuments.has(currentDocument)) {
        return;
      }
      seenDocuments.add(currentDocument);
      contexts.push({ document: currentDocument, framePath });

      let frames = [];
      try {
        frames = [...currentDocument.querySelectorAll("iframe, frame")];
      } catch {
        return;
      }

      frames.forEach((frameElement, index) => {
        const childPath = `${framePath}/frame[${index}]`;
        try {
          const childDocument = frameElement.contentDocument;
          if (!childDocument) {
            blockedFrames.push({
              frame_path: childPath,
              reason: "not_ready_or_cross_origin",
            });
            return;
          }
          visit(childDocument, childPath);
        } catch {
          blockedFrames.push({
            frame_path: childPath,
            reason: "cross_origin_or_blocked",
          });
        }
      });
    }

    visit(rootDocument, "top");
    return { contexts, blockedFrames };
  }

  function isVisible(element) {
    if (!element) {
      return false;
    }
    const view = element.ownerDocument?.defaultView;
    const style = view?.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return (
      Boolean(style) &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 0 &&
      rect.height > 0
    );
  }

  function cleanAttribute(value, maxLength = 120) {
    return String(value || "")
      .replace(/[\r\n\t]+/g, " ")
      .replace(/\s{2,}/g, " ")
      .trim()
      .slice(0, maxLength);
  }

  function candidateClassNames(element) {
    return [...(element.classList || [])]
      .map((value) => cleanAttribute(value, 80))
      .filter((value) => /^[A-Za-z0-9_-]+$/.test(value))
      .slice(0, 8);
  }

  function describeCandidate(element, framePath) {
    const tagName = String(element.tagName || "").toLowerCase();
    const inputType =
      tagName === "input"
        ? cleanAttribute(element.getAttribute("type") || "text", 32).toLowerCase()
        : "";
    return {
      frame_path: cleanAttribute(framePath, 160),
      tag_name: tagName,
      input_type: inputType,
      id: cleanAttribute(element.getAttribute("id"), 120),
      name: cleanAttribute(element.getAttribute("name"), 120),
      role: cleanAttribute(element.getAttribute("role"), 80),
      placeholder: cleanAttribute(element.getAttribute("placeholder"), 160),
      aria_label: cleanAttribute(element.getAttribute("aria-label"), 160),
      data_placeholder: cleanAttribute(
        element.getAttribute("data-placeholder"),
        160
      ),
      class_names: candidateClassNames(element),
      contenteditable: Boolean(element.isContentEditable),
    };
  }

  function collectCandidateInventory(contexts, limit = CANDIDATE_LIMIT) {
    const candidateControls = [];
    let candidateControlCount = 0;

    for (const context of contexts) {
      let elements = [];
      try {
        elements = context.document.querySelectorAll(CANDIDATE_SELECTOR);
      } catch {
        continue;
      }
      for (const element of elements) {
        const tagName = String(element.tagName || "").toLowerCase();
        const inputType = String(element.getAttribute("type") || "text").toLowerCase();
        if (
          !isVisible(element) ||
          element.disabled ||
          element.readOnly ||
          (tagName === "input" && EXCLUDED_INPUT_TYPES.has(inputType))
        ) {
          continue;
        }
        candidateControlCount += 1;
        if (candidateControls.length < limit) {
          candidateControls.push(describeCandidate(element, context.framePath));
        }
      }
    }

    return {
      candidate_controls: candidateControls,
      candidate_control_count: candidateControlCount,
      candidate_controls_truncated: candidateControlCount > candidateControls.length,
    };
  }

  function combinedSelectors(adapter, fieldName) {
    return [...new Set([...(adapter[fieldName] || []), ...SELECTORS.generic[fieldName]])];
  }

  function findFirst(selectors, contexts) {
    for (const selector of selectors) {
      for (const context of contexts) {
        let elements = [];
        try {
          elements = context.document.querySelectorAll(selector);
        } catch {
          continue;
        }
        for (const element of elements) {
          if (isVisible(element) && !element.disabled && !element.readOnly) {
            return {
              element,
              selector,
              frame_path: context.framePath,
              tag_name: element.tagName.toLowerCase(),
              contenteditable: Boolean(element.isContentEditable),
            };
          }
        }
      }
    }
    return null;
  }

  function describeMatch(match) {
    if (!match) {
      return {
        found: false,
        selector: "",
        frame_path: "",
        tag_name: "",
        contenteditable: false,
      };
    }
    return {
      found: true,
      selector: match.selector,
      frame_path: match.frame_path,
      tag_name: match.tag_name,
      contenteditable: match.contenteditable,
    };
  }

  function diagnoseEditor(adapterName, scan) {
    const adapter = SELECTORS[adapterName] || SELECTORS.generic;
    const matches = {};
    for (const fieldName of FIELD_NAMES) {
      matches[fieldName] = describeMatch(
        findFirst(combinedSelectors(adapter, fieldName), scan.contexts)
      );
    }
    return {
      adapter: adapterName,
      accessible_documents: scan.contexts.length,
      blocked_iframe_count: scan.blockedFrames.length,
      blocked_iframes: scan.blockedFrames,
      matches,
      ...collectCandidateInventory(scan.contexts),
    };
  }

  function dispatchChanges(element, value) {
    const view = element.ownerDocument?.defaultView || window;
    const InputEventConstructor = view.InputEvent || InputEvent;
    const EventConstructor = view.Event || Event;
    element.dispatchEvent(
      new InputEventConstructor("input", {
        bubbles: true,
        inputType: "insertText",
        data: value,
      })
    );
    element.dispatchEvent(new EventConstructor("change", { bubbles: true }));
    element.dispatchEvent(new EventConstructor("blur", { bubbles: true }));
  }

  function setFormValue(element, value) {
    const tagName = element.tagName.toLowerCase();
    const view = element.ownerDocument?.defaultView || window;
    if (tagName === "input" || tagName === "textarea") {
      const prototype =
        tagName === "input"
          ? view.HTMLInputElement.prototype
          : view.HTMLTextAreaElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      if (descriptor?.set) {
        descriptor.set.call(element, value);
      } else {
        element.value = value;
      }
      element.focus();
      dispatchChanges(element, value);
      return true;
    }

    if (element.isContentEditable) {
      const ownerDocument = element.ownerDocument;
      element.focus();
      const selection = view.getSelection();
      const range = ownerDocument.createRange();
      range.selectNodeContents(element);
      selection.removeAllRanges();
      selection.addRange(range);
      let inserted = false;
      try {
        inserted = ownerDocument.execCommand("insertText", false, value);
      } catch {
        inserted = false;
      }
      if (!inserted || !String(element.innerText || "").trim()) {
        element.textContent = value;
      }
      dispatchChanges(element, value);
      selection.removeAllRanges();
      return true;
    }

    return false;
  }

  function fillField(selectors, value, contexts) {
    const clean = String(value || "");
    const match = findFirst(selectors, contexts);
    const diagnostic = describeMatch(match);
    if (!clean.trim() || !match) {
      return { ...diagnostic, filled: false };
    }
    return { ...diagnostic, filled: setFormValue(match.element, clean) };
  }

  function buildFillDiagnostics(adapterName, scan, results) {
    const matches = {};
    for (const fieldName of FIELD_NAMES) {
      const result = results[fieldName] || {};
      matches[fieldName] = {
        found: Boolean(result.found),
        selector: String(result.selector || ""),
        frame_path: String(result.frame_path || ""),
        tag_name: String(result.tag_name || ""),
        contenteditable: Boolean(result.contenteditable),
      };
    }
    return {
      adapter: adapterName,
      accessible_documents: scan.contexts.length,
      blocked_iframe_count: scan.blockedFrames.length,
      blocked_iframes: scan.blockedFrames,
      matches,
      ...collectCandidateInventory(scan.contexts),
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (
      !message ||
      !["CTT_DIAGNOSE_EDITOR", "CTT_FILL_EDITOR"].includes(message.type)
    ) {
      return;
    }

    try {
      const adapterName = adapterCode(location.hostname);
      const scan = collectDocumentContexts(document);

      if (message.type === "CTT_DIAGNOSE_EDITOR") {
        const diagnostics = diagnoseEditor(adapterName, scan);
        const foundCount = Object.values(diagnostics.matches).filter(
          (item) => item.found
        ).length;
        sendResponse({
          ok: true,
          diagnostics,
          message:
            foundCount > 0
              ? `${foundCount}개 입력 영역 후보를 찾았습니다. 값은 읽지 않았습니다.`
              : `지원 가능한 입력 영역 후보를 찾지 못했습니다. 구조 후보 ${diagnostics.candidate_control_count}개를 보고서에 포함했습니다.`,
        });
        return;
      }

      const payload = message.payload || {};
      const safety = payload.safety || {};
      const target = payload.target || {};
      const content = payload.content || {};
      const patterns = target.allowed_host_patterns || [];

      if (
        safety.requires_user_action !== true ||
        safety.may_submit !== false ||
        safety.contains_credentials !== false ||
        safety.stores_browser_session !== false
      ) {
        throw new Error("전달 데이터의 안전 계약이 올바르지 않습니다.");
      }
      if (!hostMatches(location.hostname, patterns)) {
        throw new Error("현재 페이지가 전달 데이터의 허용 호스트와 다릅니다.");
      }

      const adapter = SELECTORS[adapterName] || SELECTORS.generic;
      const results = {
        title: fillField(
          combinedSelectors(adapter, "title"),
          content.title,
          scan.contexts
        ),
        body: fillField(
          combinedSelectors(adapter, "body"),
          content.body,
          scan.contexts
        ),
        tags: fillField(
          combinedSelectors(adapter, "tags"),
          Array.isArray(content.tags) ? content.tags.join(", ") : "",
          scan.contexts
        ),
        meta_description: fillField(
          combinedSelectors(adapter, "meta_description"),
          content.meta_description,
          scan.contexts
        ),
      };

      const fields = Object.fromEntries(
        FIELD_NAMES.map((fieldName) => [
          fieldName,
          Boolean(results[fieldName]?.filled),
        ])
      );
      const filledCount = Object.values(fields).filter(Boolean).length;
      sendResponse({
        ok: filledCount > 0,
        fields,
        diagnostics: buildFillDiagnostics(adapterName, scan, results),
        message:
          filledCount > 0
            ? `${filledCount}개 입력 영역에 값을 넣었습니다. 저장·발행 전 내용을 확인하세요.`
            : "현재 페이지에서 지원 가능한 편집기 입력 영역을 찾지 못했습니다.",
      });
    } catch (error) {
      sendResponse({
        ok: false,
        fields: {
          title: false,
          body: false,
          tags: false,
          meta_description: false,
        },
        diagnostics: null,
        message: error.message || String(error),
      });
    }
  });
})();
