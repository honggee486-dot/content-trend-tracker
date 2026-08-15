from __future__ import annotations

from functools import wraps
from typing import Any, Callable


TREND_CANDIDATE_CONTAINER_KEY = "trend_candidate_list"
_SCROLL_BRIDGE_MARKER = "__cttTrendCandidateScrollBridgeV1"


def build_trend_candidate_scroll_bridge_html() -> str:
    """Return the zero-height browser bridge used to preserve candidate scroll position."""
    return r"""
<script>
(() => {
    const parentWindow = window.parent;
    const bridgeKey = "__cttTrendCandidateScrollBridgeV1";
    const stateKey = "ctt:trend-candidate-scroll:v1";
    const selector = ".st-key-trend_candidate_list";

    const previousBridge = parentWindow[bridgeKey];
    if (previousBridge && typeof previousBridge.destroy === "function") {
        previousBridge.destroy();
    }

    let scroller = null;
    let observer = null;
    let bindScheduled = false;
    let restoreTimers = [];

    const readState = () => {
        try {
            const raw = parentWindow.sessionStorage.getItem(stateKey);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (parsed && typeof parsed === "object") {
                    return parsed;
                }
            }
        } catch (_) {
            // sessionStorage can be unavailable in hardened browser modes.
        }
        const fallback = parentWindow.__cttTrendCandidateScrollStateV1;
        return fallback && typeof fallback === "object" ? fallback : null;
    };

    const writeState = (state) => {
        parentWindow.__cttTrendCandidateScrollStateV1 = state;
        try {
            parentWindow.sessionStorage.setItem(stateKey, JSON.stringify(state));
        } catch (_) {
            // Keep the in-memory fallback when sessionStorage is unavailable.
        }
    };

    const saveState = () => {
        const previous = readState() || {};
        writeState({
            pageY: Math.max(0, Math.round(parentWindow.scrollY || parentWindow.pageYOffset || 0)),
            listY: scroller
                ? Math.max(0, Math.round(scroller.scrollTop || 0))
                : Math.max(0, Number(previous.listY) || 0),
            savedAt: Date.now(),
        });
    };

    const findScroller = (root) => {
        if (!root) {
            return null;
        }
        const candidates = [root, ...root.querySelectorAll("div")];
        for (const element of candidates) {
            const style = parentWindow.getComputedStyle(element);
            const overflowY = String(style.overflowY || "").toLowerCase();
            const scrollableOverflow = ["auto", "scroll", "overlay"].includes(overflowY);
            if (scrollableOverflow && element.scrollHeight > element.clientHeight + 2) {
                return element;
            }
        }
        return null;
    };

    const restoreState = () => {
        const state = readState();
        if (!state) {
            return;
        }
        if (scroller && Number.isFinite(Number(state.listY))) {
            scroller.scrollTop = Math.max(0, Number(state.listY));
        }
        if (Number.isFinite(Number(state.pageY))) {
            parentWindow.scrollTo({
                top: Math.max(0, Number(state.pageY)),
                left: parentWindow.scrollX || 0,
                behavior: "auto",
            });
        }
    };

    const scheduleRestore = () => {
        restoreTimers.forEach((timer) => parentWindow.clearTimeout(timer));
        restoreTimers = [0, 60, 180, 450].map((delay) =>
            parentWindow.setTimeout(() => {
                const root = parentWindow.document.querySelector(selector);
                const nextScroller = findScroller(root);
                if (nextScroller && nextScroller !== scroller) {
                    if (scroller) {
                        scroller.removeEventListener("scroll", saveState);
                    }
                    scroller = nextScroller;
                    scroller.addEventListener("scroll", saveState, { passive: true });
                }
                restoreState();
            }, delay)
        );
    };

    const bindCandidateList = () => {
        const root = parentWindow.document.querySelector(selector);
        if (!root) {
            return false;
        }
        const nextScroller = findScroller(root);
        if (nextScroller && nextScroller !== scroller) {
            if (scroller) {
                scroller.removeEventListener("scroll", saveState);
            }
            scroller = nextScroller;
            scroller.addEventListener("scroll", saveState, { passive: true });
        }
        scheduleRestore();
        return true;
    };

    const scheduleBind = () => {
        if (bindScheduled) {
            return;
        }
        bindScheduled = true;
        parentWindow.requestAnimationFrame(() => {
            bindScheduled = false;
            bindCandidateList();
        });
    };

    parentWindow.addEventListener("scroll", saveState, { passive: true });
    parentWindow.addEventListener("beforeunload", saveState);
    observer = new parentWindow.MutationObserver(scheduleBind);
    observer.observe(parentWindow.document.documentElement, {
        childList: true,
        subtree: true,
    });
    bindCandidateList();

    parentWindow[bridgeKey] = {
        destroy: () => {
            saveState();
            restoreTimers.forEach((timer) => parentWindow.clearTimeout(timer));
            restoreTimers = [];
            if (observer) {
                observer.disconnect();
                observer = null;
            }
            if (scroller) {
                scroller.removeEventListener("scroll", saveState);
                scroller = null;
            }
            parentWindow.removeEventListener("scroll", saveState);
            parentWindow.removeEventListener("beforeunload", saveState);
        },
    };
})();
</script>
""".strip()


def _default_html_renderer(body: str) -> None:
    import streamlit.components.v1 as components

    components.html(body, height=0, scrolling=False)


def install_trend_candidate_scroll_runtime(
    st_module: Any,
    *,
    html_renderer: Callable[[str], None] | None = None,
) -> None:
    """Inject the scroll bridge immediately before the candidate list container is rendered."""
    target = getattr(st_module, "container", None)
    if not callable(target) or getattr(target, "_trend_candidate_scroll_runtime", False):
        return
    renderer = html_renderer or _default_html_renderer

    @wraps(target)
    def wrapped(*args, **kwargs):
        if str(kwargs.get("key") or "") == TREND_CANDIDATE_CONTAINER_KEY:
            renderer(build_trend_candidate_scroll_bridge_html())
        return target(*args, **kwargs)

    wrapped._trend_candidate_scroll_runtime = True  # type: ignore[attr-defined]
    st_module.container = wrapped
