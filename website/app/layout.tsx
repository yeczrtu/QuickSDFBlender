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
  title: "Quick SDF Paint | 顔影スレッショルドマップ作成",
  description:
    "角度別の白黒マスクをペイントし、SDF距離補間を使って、光の向きに応じた影の切替値を16-bit RGBAテクスチャにまとめるBlenderアドオンです。",
  keywords: [
    "Quick SDF Paint",
    "Blender",
    "トゥーンレンダリング",
    "顔影スレッショルドマップ",
    "face-shadow threshold map",
    "SDF Face Shadow",
    "Shadow SDF mode",
    "Face SDF",
    "FaceSDF textures",
    "SDF_FaceShadow",
    "sdf shadow mask",
    "Shadow Threshold Map",
    "Face Shadow Map",
    "SDF-based face shadow map",
    "face SDF shadow",
    "SDF Shadow Map",
    "SDF Shadow Texture",
    "Face Threshold Map",
    "SDF interpolation",
  ],
  icons: {
    icon: [{ url: `${basePath}/favicon.svg`, type: "image/svg+xml", sizes: "48x48" }],
  },
  alternates: { canonical: siteUrl },
  openGraph: {
    title: "Quick SDF Paint | 顔影スレッショルドマップ作成",
    description:
      "角度別の白黒マスクから、トゥーンレンダリング用の16-bit RGBA顔影スレッショルドマップを作成します。",
    type: "website",
    url: siteUrl,
    images: [
      {
        url: socialImage,
        width: 2048,
        height: 1080,
        alt: "Quick SDF Paintで顔影スレッショルドマップを編集している画面",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Quick SDF Paint | 顔影スレッショルドマップ作成",
    description:
      "角度別の白黒マスクから16-bit RGBA顔影スレッショルドマップを作成します。",
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
