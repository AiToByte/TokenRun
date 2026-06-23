import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TokenRun — Cockpit",
  description: "Industrial-grade AI task execution command tower",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="bg-[var(--color-surface)] text-[var(--color-text)] antialiased">
        <div className="flex h-screen">
          {/* Sidebar */}
          <aside className="w-64 border-r border-gray-200 bg-white p-4 flex flex-col">
            <h1 className="text-xl font-bold mb-6">
              <span className="text-[var(--color-accent)]">Token</span>Run
            </h1>
            <nav className="flex-1 space-y-2">
              <a href="/" className="block px-3 py-2 rounded hover:bg-gray-100">
                Dashboard
              </a>
              <a href="/missions" className="block px-3 py-2 rounded hover:bg-gray-100">
                Missions
              </a>
              <a href="/skills" className="block px-3 py-2 rounded hover:bg-gray-100">
                Skills
              </a>
            </nav>
            <div className="text-xs text-gray-400">v0.1.0</div>
          </aside>

          {/* Main content */}
          <main className="flex-1 overflow-auto">
            {/* Top bar */}
            <header className="h-14 border-b border-gray-200 bg-white px-6 flex items-center justify-between">
              <div className="text-sm text-gray-500">Cockpit Command Tower</div>
              <div className="flex items-center gap-4">
                <span className="text-xs px-2 py-1 bg-green-100 text-green-700 rounded">
                  API Connected
                </span>
              </div>
            </header>
            <div className="p-6">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
