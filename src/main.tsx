import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { PassengerPage } from "./Passenger";
import "@fontsource-variable/inter";
import "./styles.css";

try {
  document.documentElement.dataset.theme =
    localStorage.getItem("stranded-theme") || "light";
} catch {
  /* Use the default theme when storage is unavailable. */
}

const token = window.location.pathname.startsWith("/p/")
  ? window.location.pathname.split("/")[2]
  : null;
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {token ? <PassengerPage token={token} /> : <App />}
  </React.StrictMode>,
);
