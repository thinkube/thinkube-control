import type { Metadata } from "next";
import { Poppins, Noto_Sans_Mono } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "next-themes";
import { TkToaster } from "thinkube-style/components/feedback";

const poppins = Poppins({
  weight: ["300", "400", "500", "600", "700"],
  subsets: ["latin"],
  variable: "--font-poppins",
  display: "swap",
});

const notoSansMono = Noto_Sans_Mono({
  weight: ["400", "500", "600", "700"],
  subsets: ["latin"],
  variable: "--font-noto-sans-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Thinkube Control",
  description: "Thinkube cluster management interface",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${poppins.variable} ${notoSansMono.variable}`}>
      <body className="antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="light"
          enableSystem={false}
          disableTransitionOnChange
        >
          {children}
          <TkToaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
