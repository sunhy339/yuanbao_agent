import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Copy, Maximize2, Minus, Plus, X } from "lucide-react";

type MermaidModule = {
  default?: {
    initialize: (config: Record<string, unknown>) => void;
    render: (id: string, code: string) => Promise<{ svg: string }>;
  };
  initialize?: (config: Record<string, unknown>) => void;
  render?: (id: string, code: string) => Promise<{ svg: string }>;
};

let initialized = false;
let idCounter = 0;

function mermaidApi(module: MermaidModule) {
  return module.default ?? module;
}

function loadMermaid() {
  return import("mermaid") as Promise<MermaidModule>;
}

function loadDomPurify() {
  return import("dompurify") as Promise<{ default: { sanitize: (value: string, options?: Record<string, unknown>) => string } }>;
}

function sanitizeSvg(svg: string, sanitizer?: { sanitize: (value: string, options?: Record<string, unknown>) => string }) {
  if (!sanitizer) return svg;
  return sanitizer.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } });
}

function clampZoom(value: number) {
  return Math.min(2.5, Math.max(0.5, value));
}

export const MERMAID_DIAGRAM_START =
  /^(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline|requirementDiagram|quadrantChart|xychart-beta|sankey-beta|block-beta|packet-beta|architecture|kanban)\b/i;

export function looksLikeMermaid(code: string) {
  const first = code
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean);
  return first ? MERMAID_DIAGRAM_START.test(first) : false;
}

export function shouldRenderMermaid(language: string, code: string) {
  const normalized = language.trim().toLowerCase();
  if (normalized === "mermaid") return true;
  if (!normalized || normalized === "text" || normalized === "plaintext" || normalized === "plain") {
    return looksLikeMermaid(code);
  }
  return false;
}

export const CleanMermaid = memo(function CleanMermaid({ code }: { code: string }) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setSvg("");

    Promise.all([loadMermaid(), loadDomPurify()])
      .then(async ([mermaidModule, purifierModule]) => {
        const api = mermaidApi(mermaidModule);
        if (!initialized) {
          api.initialize?.({
            startOnLoad: false,
            theme: "default",
            securityLevel: "strict",
            suppressErrorRendering: true,
            fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
          });
          initialized = true;
        }
        const result = await api.render?.(`hc-mermaid-${++idCounter}`, code);
        if (!mountedRef.current || cancelled) return;
        setSvg(sanitizeSvg(result?.svg ?? "", purifierModule.default));
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!mountedRef.current || cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [code]);

  const previewZoomLabel = useMemo(() => `${Math.round(zoom * 100)}%`, [zoom]);

  if (loading) {
    return (
      <figure className="hc-mermaid" data-state="loading">
        <figcaption>
          <span>Mermaid</span>
          <em>正在渲染图表...</em>
        </figcaption>
        <div className="hc-mermaid-placeholder" />
      </figure>
    );
  }

  if (error || !svg) {
    return (
      <figure className="hc-mermaid" data-state="error">
        <figcaption>
          <span>Mermaid</span>
          <em>图表渲染失败</em>
        </figcaption>
        <pre>{error || "Mermaid did not return SVG output."}</pre>
      </figure>
    );
  }

  return (
    <>
      <figure className="hc-mermaid" data-state="ready">
        <figcaption>
          <span>Mermaid</span>
          <div>
            <button type="button" aria-label="复制 Mermaid 源码" onClick={() => void navigator.clipboard?.writeText(code)}>
              <Copy size={13} />
            </button>
            <button type="button" aria-label="预览 Mermaid 图表" onClick={() => setPreviewOpen(true)}>
              <Maximize2 size={13} />
            </button>
          </div>
        </figcaption>
        <button
          type="button"
          className="hc-mermaid-canvas"
          aria-label="打开 Mermaid 图表预览"
          onClick={() => setPreviewOpen(true)}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </figure>

      {previewOpen ? (
        <div className="hc-mermaid-modal" role="dialog" aria-modal="true" aria-label="Mermaid 图表预览">
          <div className="hc-mermaid-modal-card">
            <header>
              <strong>Mermaid 图表</strong>
              <div>
                <button type="button" aria-label="缩小 Mermaid 图表" onClick={() => setZoom((value) => clampZoom(value - 0.25))}>
                  <Minus size={14} />
                </button>
                <button type="button" aria-label={previewZoomLabel} onClick={() => setZoom(1)}>
                  {previewZoomLabel}
                </button>
                <button type="button" aria-label="放大 Mermaid 图表" onClick={() => setZoom((value) => clampZoom(value + 0.25))}>
                  <Plus size={14} />
                </button>
                <button type="button" aria-label="关闭 Mermaid 预览" onClick={() => setPreviewOpen(false)}>
                  <X size={14} />
                </button>
              </div>
            </header>
            <div className="hc-mermaid-modal-body">
              <div style={{ transform: `scale(${zoom})` }} dangerouslySetInnerHTML={{ __html: svg }} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
});
