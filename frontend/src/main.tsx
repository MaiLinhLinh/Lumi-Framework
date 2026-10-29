import { createRoot } from "react-dom/client";
import { App } from "./App";
import { installErrorReporting } from "./services/diagnostics";
import "./ui/app.css";
import "./ui/planning.css";

installErrorReporting();
createRoot(document.getElementById("root")!).render(<App />);
