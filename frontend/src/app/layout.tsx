import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Mono, Instrument_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";

import { UserMenu } from "@/components/auth/user-menu";
import { ThemeToggle } from "@/components/theme-toggle";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

import { Providers } from "./providers";

// Three faces: a display serif for headings, a humanist grotesque for UI text,
// and a warm mono for code and figures. `variable` exposes each as a CSS custom
// property that tailwind.config.ts reads, so `font-sans` / `font-mono` /
// `font-display` resolve to these.
//
// Fraunces is a variable font with optical-size, softness and "wonk" axes — it
// is what gives headings a drawn, slightly idiosyncratic voice instead of the
// flat geometric sans that every generated page ships with.
const fontDisplay = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
  axes: ["SOFT", "WONK", "opsz"],
});
const fontSans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});
const fontMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

const SOURCE_URL = "https://github.com/rachitpp/r1";

export const metadata: Metadata = {
  title: "Codebase Onboarding Assistant",
  description: "Ask questions about any public Python GitHub repo.",
};

/**
 * Wordmark glyph: three nodes and two edges — the symbol graph, abbreviated.
 * Drawn as bare ink rather than set inside a filled rounded tile; the tile is
 * the house style of every generated dashboard and it reads as one.
 */
function LogoMark() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      aria-hidden
      className="size-5 shrink-0 text-primary"
    >
      <path d="M7 7h4a4 4 0 0 1 4 4v2a4 4 0 0 0 4 4h1" />
      <circle cx="5" cy="7" r="2" fill="currentColor" stroke="none" />
      <circle cx="5" cy="17" r="2" fill="currentColor" stroke="none" />
      <path d="M7 17h4" />
    </svg>
  );
}

function GithubIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden className="size-4">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${fontDisplay.variable} ${fontSans.variable} ${fontMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* Sets `.dark` on <html> before first paint. Without it every
            dark-mode visitor gets a full-screen white flash on every
            navigation, because the class can only be applied once React
            mounts. `suppressHydrationWarning` above is the cost: this script
            deliberately makes the client's <html> differ from the server's. */}
        <script
          dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }}
        />
      </head>
      <body className="min-h-screen antialiased">
        <Providers>
          <header className="sticky top-0 z-40 border-b border-border/70 bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/65">
            {/* Full-bleed on every route: the wordmark sits hard left and the
                source link hard right, rather than inside a centred column. */}
            <div className="flex h-12 w-full items-center justify-between gap-4 px-4 sm:px-6">
              <Link
                href="/"
                className="flex items-center gap-2.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                <LogoMark />
                <span className="display text-[15px] font-semibold">
                  Codebase Onboarding Assistant
                </span>
              </Link>
              <div className="flex items-center gap-1.5">
                <a
                  href={SOURCE_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <GithubIcon />
                  <span className="hidden sm:inline">Source</span>
                </a>
                <ThemeToggle />
                <UserMenu />
              </div>
            </div>
          </header>
          {children}
        </Providers>
      </body>
    </html>
  );
}
