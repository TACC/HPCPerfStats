import { applyBokehResizeObserverDeferral } from "./patch-resize-observer-for-bokeh.js";
import { ensureBokehLoaded } from "./bokehInit";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import AppMachine from "./AppMachine.jsx";
import AppPub from "./AppPub.jsx";
import "./fonts/open-sans.css";
import "./bootswatch-spacelab.scss";
import "./index.css";

// Before any chunk can load @bokeh/bokehjs, patch global ResizeObserver (idempotent).
applyBokehResizeObserverDeferral();

ensureBokehLoaded().catch((err) => {
  console.warn("Bokeh failed to load:", err);
});

function detectBasename() {
  if (typeof window === "undefined") return "/machine";
  const path = window.location.pathname || "";
  if (path === "/pub" || path.startsWith("/pub/")) return "/pub";
  return "/machine";
}

function init() {
  const rootEl = document.getElementById("root");
  if (!rootEl) return;
  const basename = detectBasename();
  const Shell = basename === "/pub" ? AppPub : AppMachine;
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <BrowserRouter basename={basename}>
        <Shell />
      </BrowserRouter>
    </React.StrictMode>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
