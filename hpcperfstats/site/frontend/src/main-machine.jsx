import { applyBokehResizeObserverDeferral } from "./patch-resize-observer-for-bokeh.js";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import AppMachine from "./AppMachine.jsx";
import "./fonts/open-sans.css";
import "./bootswatch-spacelab.scss";
import "./index.css";

applyBokehResizeObserverDeferral();

function init() {
  const rootEl = document.getElementById("root");
  if (!rootEl) return;
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <BrowserRouter basename="/machine">
        <AppMachine />
      </BrowserRouter>
    </React.StrictMode>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
