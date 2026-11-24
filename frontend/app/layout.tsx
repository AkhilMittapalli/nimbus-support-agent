import type { Metadata, Viewport } from "next";
import { Fraunces, Geist_Mono, Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ variable: "--font-ui", subsets: ["latin"] });
const fraunces = Fraunces({ variable: "--font-display", subsets: ["latin"], weight: ["500", "600"] });
const geistMono = Geist_Mono({ variable: "--font-code", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Nimbus Support Agent",
  description: "Customer support AI agent with streaming chat, live voice, tool orchestration and observability.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#eef5fd" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1626" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} ${fraunces.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="h-full overflow-hidden">{children}</body>
    </html>
  );
}
