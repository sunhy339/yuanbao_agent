import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { maybeRunTauriProviderFlowE2e } from "./e2e/tauriProviderFlow";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

window.setTimeout(() => {
  void maybeRunTauriProviderFlowE2e();
}, 0);
