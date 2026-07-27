import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Codebase Onboarding Assistant",
  description: "Ask questions about any public Python GitHub repo.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <Providers>
          <header className="border-b">
            <div className="mx-auto flex h-12 max-w-6xl items-center px-4">
              <Link href="/" className="text-sm font-semibold tracking-tight">
                Codebase Onboarding Assistant
              </Link>
            </div>
          </header>
          {children}
        </Providers>
      </body>
    </html>
  );
}
