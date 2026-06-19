import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Network Digital Twin Lab",
  description: "Base platform for AI network digital twin experiments."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
