async function withCurrentWindow(action: "minimize" | "toggleMaximize" | "close") {
  if (!("__TAURI_INTERNALS__" in window)) {
    return;
  }

  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  const currentWindow = getCurrentWindow();
  await currentWindow[action]();
}

export function DesktopTitlebar() {
  return (
    <header className="desktop-titlebar" aria-label="Desktop titlebar">
      <div
        className="desktop-titlebar-drag"
        data-tauri-drag-region
        onDoubleClick={() => void withCurrentWindow("toggleMaximize")}
      >
        <div className="desktop-titlebar-brand" data-tauri-drag-region>
          <span className="desktop-titlebar-mark" aria-hidden="true" />
          <span data-tauri-drag-region>Yuanbao Agent</span>
          <small data-tauri-drag-region>Workbench</small>
        </div>
      </div>
      <div className="desktop-titlebar-controls" aria-label="Window controls">
        <button type="button" aria-label="Minimize window" onClick={() => void withCurrentWindow("minimize")}>
          {"\u2212"}
        </button>
        <button type="button" aria-label="Maximize window" onClick={() => void withCurrentWindow("toggleMaximize")}>
          {"\u25a1"}
        </button>
        <button type="button" aria-label="Close window" onClick={() => void withCurrentWindow("close")}>
          {"\u00d7"}
        </button>
      </div>
    </header>
  );
}
