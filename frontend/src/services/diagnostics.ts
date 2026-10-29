export function reportDiagnostic(phase: string, message: string, source = "", line = 0) {
  void fetch("/api/client-debug", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phase, message, source, line }),
  }).catch(() => {});
}

export function installErrorReporting() {
  window.addEventListener("error", event => reportDiagnostic("window_error", event.message, event.filename, event.lineno));
  window.addEventListener("unhandledrejection", event => reportDiagnostic("unhandled_rejection", String(event.reason)));
}
