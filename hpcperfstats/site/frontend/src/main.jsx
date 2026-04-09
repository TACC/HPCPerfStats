import { ensureBokehLoaded } from "./bokehInit";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./fonts/open-sans.css";
import "./bootswatch-spacelab.scss";
import "./index.css";

ensureBokehLoaded().catch((err) => {
  console.warn("Bokeh failed to load:", err);
});

function init() {
  const rootEl = document.getElementById("root");
  if (!rootEl) return;
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <BrowserRouter basename="/machine">
        <App />
      </BrowserRouter>
    </React.StrictMode>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
