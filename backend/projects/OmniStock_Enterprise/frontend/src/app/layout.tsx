import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Sidebar } from "@/components/layout/sidebar";
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
  title: "OmniStock Enterprise",
  description: "Enterprise Inventory Management System",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-slate-950`}
      >
        <div className="flex h-screen">
          <div className="fixed inset-y-0 left-0 z-50">
            <Sidebar />
          </div>
          <main className="ml-64 flex-1 overflow-auto p-8 bg-slate-900">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
