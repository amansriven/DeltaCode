import type { Metadata } from "next";
import { headers } from "next/headers";
import "@fontsource-variable/manrope";
import "@fontsource-variable/space-grotesk";
import "@fontsource-variable/jetbrains-mono";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("host") ?? "localhost:3000";
  const protocol = host.startsWith("localhost") || host.startsWith("127.0.0.1") ? "http" : "https";
  const origin = `${protocol}://${host}`;

  return {
    metadataBase: new URL(origin),
    title: "Delta Code",
    description:
      "The AI review bot for breaking API changes. Delta Code finds affected code, generates and verifies migrations, and opens evidence-rich draft pull requests.",
    applicationName: "Delta Code",
    icons: {
      icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
      shortcut: "/favicon.svg",
      apple: "/brand/delta-code-badge.png",
    },
    keywords: [
      "AI code review",
      "API migration automation",
      "Dependabot for APIs",
      "GitHub review bot",
      "GPT-4o",
    ],
    openGraph: {
      type: "website",
      title: "Delta Code — The AI review bot for breaking API changes.",
      description:
        "From official provider change to verified migration draft PR, with GPT-4o intelligence and deterministic evidence.",
      images: [
        {
          url: `${origin}/og-v2.png`,
          width: 1731,
          height: 909,
          alt: "Delta Code — The AI review bot for breaking API changes.",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "Delta Code — The AI review bot for breaking API changes.",
      description:
        "From official provider change to verified migration draft PR.",
      images: [`${origin}/og-v2.png`],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <body>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        <div id="main-content">{children}</div>
      </body>
    </html>
  );
}
