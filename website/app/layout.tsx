import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const siteOrigin = "https://yeczrtu.github.io";
const basePath = "/QuickSDFBlender";
const siteUrl = `${siteOrigin}${basePath}/`;
const socialImage = `${siteOrigin}${basePath}/media/quick-sdf-studio-overview.png`;

export const metadata: Metadata = {
  metadataBase: new URL(siteOrigin),
  title: "Quick SDF Studio 操作ガイド",
  description:
    "Quick SDF Studioで角度別の顔影マスクを編集し、lilToon／liltoonUE向けの16-bit RGBA PNGを書き出す手順を説明します。",
  alternates: { canonical: siteUrl },
  openGraph: {
    title: "Quick SDF Studio 操作ガイド",
    description:
      "角度別の顔影マスクの作成、編集、確認、16-bit RGBA PNGの書き出し手順です。",
    type: "website",
    url: siteUrl,
    images: [
      {
        url: socialImage,
        width: 2048,
        height: 1080,
        alt: "Quick SDF Studioの編集画面",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Quick SDF Studio 操作ガイド",
    description:
      "角度別の顔影マスクを編集し、16-bit RGBA PNGを書き出す手順です。",
    images: [socialImage],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
