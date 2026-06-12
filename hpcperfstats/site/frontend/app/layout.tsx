import type { Metadata, Viewport } from "next";
import AppProviders from "./providers";
import "@fontsource/open-sans/latin-400.css";
import "@fontsource/open-sans/latin-400-italic.css";
import "@fontsource/open-sans/latin-700.css";
import "@fontsource/open-sans/latin-700-italic.css";
import "@/bootswatch-spacelab.scss";
import "@/index.css";

export const metadata: Metadata = {
  title: "HPCPerfStats",
};

export const viewport: Viewport = {
  themeColor: "#446e9b",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
