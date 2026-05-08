import React from "react";
import { Link } from "react-router-dom";

export default function AuthShell({ title, subtitle, children, footer, testId }) {
  return (
    <div className="min-h-screen grid md:grid-cols-2 bg-canvas">
      <aside className="hidden md:flex relative bg-ink text-white overflow-hidden">
        <div className="absolute inset-0 piano-bars opacity-20" />
        <div className="relative z-10 flex flex-col justify-between p-12 w-full">
          <Link to="/" className="flex items-center gap-3" data-testid="auth-brand">
            <span className="block w-8 h-8 bg-white" style={{ backgroundImage: "repeating-linear-gradient(90deg, #FFF 0 4px, transparent 4px 14px)" }} />
            <span className="font-display font-bold text-xl tracking-tight">Scorelib</span>
          </Link>
          <div>
            <h2 className="font-display font-black text-5xl leading-[0.95] tracking-tighter mb-4">
              La tua libreria<br />di spartiti.<br />Sempre con te.
            </h2>
            <p className="overline text-white/70">UPLOAD · INDICIZZA · SUONA</p>
          </div>
          <div className="text-mono text-xs text-white/50">© {new Date().getFullYear()} Scorelib</div>
        </div>
      </aside>
      <main className="flex items-center justify-center p-6 md:p-16">
        <div className="w-full max-w-md" data-testid={testId}>
          <h1 className="font-display font-black text-4xl tracking-tighter mb-2">{title}</h1>
          {subtitle && <p className="text-[#525252] mb-8">{subtitle}</p>}
          {children}
          {footer && <div className="mt-8 text-sm text-[#525252]">{footer}</div>}
        </div>
      </main>
    </div>
  );
}
