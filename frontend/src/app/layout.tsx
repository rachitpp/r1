import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Codebase Onboarding Assistant",
  description: "Ask questions about any public Python GitHub repo.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
