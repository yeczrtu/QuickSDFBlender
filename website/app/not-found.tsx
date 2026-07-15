import type { Metadata } from "next";
import { basePath } from "./articles/article-data";

export const metadata: Metadata = {
  title: "ページが見つかりません | Quick SDF Paint",
  robots: { index: false, follow: false },
  alternates: { canonical: null },
  openGraph: null,
  twitter: null,
};

export default function NotFound() {
  return (
    <main className="not-found page-shell">
      <p className="article-category">404</p>
      <h1>ページが見つかりません</h1>
      <p>URLを確認するか、Quick SDF Paintの操作ガイドへ戻ってください。</p>
      <a href={`${basePath}/`}>操作ガイドへ戻る</a>
    </main>
  );
}
