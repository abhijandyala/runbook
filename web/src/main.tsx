import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { RunbookProvider } from "./store";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RunbookProvider>
      <App />
    </RunbookProvider>
  </StrictMode>
);
