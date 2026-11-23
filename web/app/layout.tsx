import "./globals.css";
import type { ReactNode } from "react";
import TopBar from "@/components/TopBar";

export const metadata = { title: "Studio", description: "AI multicam podcast editor" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <TopBar />
        <main>{children}</main>
      </body>
    </html>
  );
}
