import React from "react";
import api from "@/lib/api";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    // Log to backend logs (best-effort, don't crash if API is down)
    try {
      api.post("/logs/client-error", {
        message: String(error?.message || error),
        stack: String(error?.stack || ""),
        component_stack: String(info?.componentStack || ""),
        url: window.location.href,
      }).catch(() => {});
    } catch (_) { /* ignore */ }
    // eslint-disable-next-line no-console
    console.error("UI Error:", error, info);
  }
  reset = () => {
    this.setState({ error: null });
    // Soft refresh of the current route by replacing the same path
    if (typeof window !== "undefined") {
      // No reload — let user try again
    }
  };
  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center p-6 bg-canvas" data-testid="error-boundary">
          <div className="max-w-md w-full border border-rule rounded-md p-6 bg-white">
            <p className="overline mb-2">ERRORE INTERFACCIA</p>
            <h1 className="font-display font-black text-3xl tracking-tighter mb-2">Qualcosa è andato storto.</h1>
            <p className="text-[#525252] text-sm mb-4">L'errore è stato registrato nei log. Puoi riprovare oppure ricaricare la pagina.</p>
            <pre className="text-mono text-xs text-red-600 bg-red-50 border border-red-200 rounded-sm p-2 max-h-32 overflow-auto whitespace-pre-wrap mb-4">{String(this.state.error?.message || this.state.error)}</pre>
            <div className="flex gap-2">
              <button onClick={this.reset} className="btn-primary !py-2 text-sm" data-testid="error-retry-btn">Riprova</button>
              <button onClick={() => window.location.reload()} className="btn-ghost border border-rule rounded-sm px-4 py-2 text-sm" data-testid="error-reload-btn">Ricarica</button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
