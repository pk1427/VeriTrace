import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "VeriTrace",
  description: "Consent-gated face verification + blockchain evidence.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col font-sans">
        <header className="border-b border-zinc-200 px-6 py-4">
          <nav className="mx-auto flex max-w-3xl items-center gap-6 text-sm">
            <Link href="/">Home</Link>
            <Link href="/enroll">Enroll</Link>
            <Link href="/scan">Scan</Link>
            <Link href="/verify">Verify</Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
