import type { Metadata } from "next";
import {
  ArticleLayout,
  EvidenceNote,
  SourceList,
} from "../article-layout";
import { absoluteArticleUrl, getArticle, siteOrigin } from "../article-data";

const article = getArticle("face-shadow-threshold-map");

export const metadata: Metadata = {
  title: `${article.title} | Quick SDF Paint`,
  description: article.description,
  alternates: { canonical: absoluteArticleUrl(article.slug) },
  openGraph: {
    title: article.title,
    description: article.description,
    type: "article",
    url: absoluteArticleUrl(article.slug),
    publishedTime: article.published,
    modifiedTime: article.modified,
    images: [{
      url: `${siteOrigin}${article.image}`,
      width: 1200,
      height: 630,
      alt: "角度別マスク、Signed Distance、切替値という生成段階と7つの入力キー",
    }],
  },
  twitter: {
    card: "summary_large_image",
    title: article.title,
    description: article.description,
    images: [`${siteOrigin}${article.image}`],
  },
};

const toc = [
  { id: "why", label: "なぜ顔だけ別に制御するのか" },
  { id: "stored-value", label: "保存するのは切替角" },
  { id: "four-stages", label: "制作から表示までの4段階" },
  { id: "comparison", label: "ほかの方式との使い分け" },
  { id: "monotonic", label: "1画素1回の切替という制約" },
  { id: "suitable", label: "適している条件" },
  { id: "limitations", label: "単独では扱えないもの" },
  { id: "decisions", label: "制作前に決める6項目" },
  { id: "terminology", label: "実際に使われている呼称" },
  { id: "summary", label: "まとめ" },
] as const;

export default function FaceShadowThresholdMapArticle() {
  return (
    <ArticleLayout
      article={article}
      toc={toc}
      lead="顔影スレッショルドマップは、完成した影の明るさではなく、顔の各画素が光の向きのどこでLightへ切り替わるかを保存するデータです。個別製品の用語から離れ、制作・補間・格納・シェーダー評価の4段階として整理します。"
    >
      <EvidenceNote title="本記事の整理方法">
        <p>複数の公開実装を、角度別マスクの作成、境界補間、切替値への圧縮、ランタイム評価という同じデータフローへ整理しました。さらに法線編集、固定マスク、複数画像切替との違いを、アーティストが選択できる比較表としてまとめています。</p>
      </EvidenceNote>

      <h2 id="why">なぜ顔だけ別の陰影制御が必要になるのか</h2>
      <p>一般的なセルシェーディングでは、表面法線<code>N</code>とライト方向<code>L</code>の内積<code>N·L</code>を計算し、一定値を境にLightとShadowを切り替えます。形状に自然に追従するため、身体や衣服には扱いやすい方法です。</p>
      <p>顔では「立体として正しい陰影」と「二次元のキャラクターデザインとして読みやすい影」が一致しないことがあります。鼻、唇、まぶた、頬には細かな法線変化があり、連続階調なら目立たない変化も、二値化すると細い影、分断された島、波打つ境界として現れます。表情でメッシュが変形すれば、境界も変わります。</p>
      <p><a href="https://media.gdcvault.com/gdc2024/Slides/GDC%2Bslide%2Bpresentations/Tanaka_Kosuke_3D_Toon_Rendering.pdf">『Hi-Fi RUSH』のGDC 2024講演資料</a>では、基本的なキャラクター陰影に<code>N·L</code>を用いながら、顔影はUV上のthreshold mapで制御しています。<a href="https://cgworld.jp/article/202306-hifirush01.html">CGWORLDの制作記事</a>でも、表情変化の大きい顔で意図した影を維持するためのFace Threshold Mapが紹介されています。</p>
      <p>これは法線計算が誤っているという話ではありません。法線が答えるのは「この立体へ光を当てた結果」であり、顔影スレッショルドマップが答えるのは「この光方向で見せたい図形」です。</p>

      <h2 id="stored-value">保存するのは「影の濃さ」ではなく「切替角」</h2>
      <p>顔のUV上に画素A、B、Cがあり、光を横から正面へ動かす場面を考えます。Aは早くLightになり、Bは途中で、Cは正面近くまでShadowに残るとします。完成テクスチャには、その三つの切替時点をそれぞれ異なる値として保存します。</p>
      <pre><code>{`Light = currentLightAngle >= thresholdMap(UV)`}</code></pre>
      <p>実装によって白黒、比較方向、0と1の意味は反転します。しかし本質は、各画素が光角度上の境界を一つ持つことです。中間のグレーは「半分だけ明るい画素」ではなく、「この角度で状態が切り替わる画素」を意味します。影色、境界のぼかし、影の濃さは、その判定結果へシェーダー側で別に適用できます。</p>
      <p>一般的なShadow Mapとも役割が異なります。Shadow Mapはライトから見た奥行きで、そのフレームにおける幾何学的な遮蔽を判定します。顔影スレッショルドマップはUVへ固定した制作データで、髪や手が顔を遮ったかどうかは見ていません。実際のシェーダーでは、顔の主要な明暗境界、リアルタイム影、AO、固定マスクを重ねて使えます。</p>

      <h2 id="four-stages">制作から表示までを4段階で捉える</h2>
      <div className="process-flow" role="list" aria-label="顔影スレッショルドマップの処理段階">
        <div role="listitem"><span>01 Authoring</span><strong>角度別マスクで意図を定義</strong></div>
        <div role="listitem"><span>02 Interpolation</span><strong>離れた境界の間を補間</strong></div>
        <div role="listitem"><span>03 Encoding</span><strong>画素ごとの切替角へ圧縮</strong></div>
        <div role="listitem"><span>04 Evaluation</span><strong>現在の光角度と比較</strong></div>
      </div>

      <h3>1. Authoring：代表角度で望む影を決める</h3>
      <p>顔に対するライトの代表角度をいくつか選び、各角度でLightにしたい領域を白、Shadowにしたい領域を黒として定義します。法線から下描きを作っても、最初から手描きしても構いません。大切なのは、自動計算を完成結果とせず、アーティストが輪郭を直接修正できることです。</p>
      <p>必要な枚数に普遍的な正解はありません。<a href="https://cgworld.jp/article/202306-hifirush01.html">『Hi-Fi RUSH』の公開制作記事</a>ではDCC上でライトを等間隔に回し、複数の二値画像を生成・修正する工程が示される一方、<a href="https://erichu33.github.io/ASPDocs/en/articles/face-shadow-map-creation-and-baking-workflow.html">Anime Shading Plusの公開手順</a>は9枚を例にしています。キーを増やせば局所制御は増えますが、制作量と不整合の可能性も増えます。粗い間隔から始め、境界の経路を変えたい区間だけ追加する方が管理しやすくなります。</p>

      <h3>2. Interpolation：白黒ではなく境界の移動を補う</h3>
      <p>角度キーを順番に切り替えるだけでは、影は段階的に飛びます。画像のRGB値を線形に混ぜてもグレーは作れますが、二値化した結果では差分領域が途中で一斉に反転し、輪郭が滑らかに進まない場合があります。</p>
      <p>SDFを使う方法では、各マスクについて境界までの距離を求めます。隣接する二つの境界からの距離比によって、その画素が区間内のどこで切り替わるかを推定します。<a href="https://nagakagachi.hatenablog.com/entry/2024/03/02/140704">SDF Based Transition Blending for Shadow Threshold Map</a>と<a href="https://github.com/akasaki1211/sdf_shadow_threshold_map">公開ツール実装</a>で、この考え方を確認できます。</p>
      <pre><code>{`transition = distanceFromA / (distanceFromA + distanceFromB)`}</code></pre>
      <p>SDFは有効な補間方法ですが、このデータ表現の必須条件ではありません。入力が単純なら累積マスクなど別方式でも切替値を作れます。SDFが担当するのは、離れた輪郭の途中位置を距離から求める部分です。</p>

      <h3>3. Encoding：状態列を一つの値へ圧縮する</h3>
      <p>角度別マスクを<code>M(u, v, θ)</code>とします。ある画素が角度の進行中に一度だけShadowからLightへ変わるなら、その履歴は一つの切替角<code>T(u, v)</code>で表せます。</p>
      <pre><code>{`M(u, v, θ) = θ >= T(u, v)`}</code></pre>
      <p>複数画像をランタイムへ持ち込まず、「いつ変わるか」だけを一枚に残せるのはこのためです。左右を別チャンネルへ保存する、左右対称ならUVを反転する、といった格納方法はシェーダーごとに異なります。</p>

      <h3>4. Evaluation：頭部に対する光方向と比較する</h3>
      <p>シェーダーはライト方向を頭部のローカル座標へ変換し、顔のForwardとRightを基準に横方向の値を求めます。左右のどちらから光が来るかに応じてチャンネルまたはUV方向を選び、テクスチャ値と比較します。頭部座標を使うことで、キャラクターや頭が回転しても顔に対する相対方向を保てます。</p>

      <h2 id="comparison">ほかの顔影制御とどう使い分けるか</h2>
      <p>方式の優劣ではなく、アーティストへ何を直接編集させるかが異なります。以下は、複数方式を同じ評価軸へ置いた本記事独自の整理です。</p>
      <table>
        <caption>顔影制御方式の比較</caption>
        <thead><tr><th scope="col">方式</th><th scope="col">編集するもの</th><th scope="col">強み</th><th scope="col">主な制約</th></tr></thead>
        <tbody>
          <tr><th scope="row">通常法線＋N·L</th><td>形状・法線</td><td>形状と自然に連動し、一般ライティングと統合しやすい</td><td>二値化した顔では細かな法線変化が目立つ</td></tr>
          <tr><th scope="row">編集法線・法線転写</th><td>調整した法線場</td><td>追加の切替テクスチャが不要</td><td>欲しい二次元境界を法線から逆算する必要がある</td></tr>
          <tr><th scope="row">Proxy／平滑化法線</th><td>単純化した法線場</td><td>顔全体の陰影を整理しやすい</td><td>鼻や頬ごとの具体的な輪郭を描きにくい</td></tr>
          <tr><th scope="row">固定マスク</th><td>UV上の白黒領域</td><td>常に暗くする場所などを直接指定できる</td><td>ライトが動いても境界が移動しない</td></tr>
          <tr><th scope="row">複数マスク切替</th><td>角度別の完成画像</td><td>各キーの形を直接確認できる</td><td>画像数、段差、ブレンド時の輪郭を管理する必要がある</td></tr>
          <tr><th scope="row">スレッショルドマップ</th><td>画素ごとの切替角</td><td>描いた輪郭の角度変化を一枚へまとめられる</td><td>一画素につき基本的に一回の切替しか表せない</td></tr>
        </tbody>
      </table>
      <p>身体や衣服まで置き換える必要はありません。形状に沿った反応が望ましい場所は法線を使い、特に輪郭を設計したい顔だけをスレッショルドマップへ分離する構成が現実的です。</p>

      <h2 id="monotonic">一画素一回の切替という制約</h2>
      <div className="state-sequence">
        <div><span>一つの閾値で表せる</span><strong>一方向の状態列</strong><code>S S S L L L L</code></div>
        <div><span>一つの閾値では表せない</span><strong>往復する状態列</strong><code>S L S L L S L</code></div>
      </div>
      <p>一度Lightになった画素が後の角度でShadowへ戻る場合、その履歴を単一の切替値へ無損失に圧縮できません。二度以上の切替が必要なら、追加チャンネル、複数マップ、二次元の光方向データなど、表現自体を拡張する必要があります。</p>
      <p>そのため角度別マスクは、独立した完成イラストの集まりではなく、一方向へ変化する同じ影境界として設計します。制作ツールのMonotonic Guardや書き出し時の修復は、この表現上の契約を守るための処理です。</p>

      <h2 id="suitable">この方式が適している条件</h2>
      <ul>
        <li>物理的な正しさより、顔影を図形として整えて見せたい</li>
        <li>主に一つの代表ライト方向を使う</li>
        <li>頭部を基準にした左右方向の変化が重要</li>
        <li>光角度に対してLight領域が一方向へ増減する</li>
        <li>顔に安定したUVがある</li>
        <li>アーティストが法線より白黒の輪郭を直接修正したい</li>
        <li>ランタイムで多数の角度画像を保持したくない</li>
      </ul>
      <p>特に「各代表角度で完成形を描けること」と「ランタイムでは一枚として扱えること」の間をつなぐ点が特徴です。</p>

      <h2 id="limitations">一枚のスレッショルドマップだけでは扱えないもの</h2>
      <h3>上下方向の大きなライト移動</h3>
      <p>水平方向だけを一つの値に圧縮した実装では、上下方向を独立して表現できません。上下にもライトが大きく動く場合は、別パラメーター、複数マップ、通常法線陰影との合成が必要です。</p>
      <h3>髪や手が落とす幾何学的な影</h3>
      <p>遮蔽物を見ていないため、髪が額へ落とす影や手が顔を横切る影はリアルタイムShadow Mapなど別の可視性計算で扱います。マップへ鼻影らしい図形を描くことはできますが、それは物理的な投影ではなく光角度へ関連付けたデザインです。</p>
      <h3>複数ライトと近距離Point Light</h3>
      <p>一つの代表方向を前提とする方式では、複数方向からの寄与を一つの閾値だけで表せません。近距離のPoint Lightでは顔の場所ごとにライト方向も変わるため、頭部中心からの一方向近似には限界があります。</p>
      <h3>極端な表情変形</h3>
      <p>UVベースなので通常の表情変形には追従しますが、描いた境界自体もメッシュと一緒に伸びます。大きな口開けなどで不足する場合は、表情別補正や適用範囲マスクを検討します。</p>

      <h2 id="decisions">制作前に決める6項目</h2>
      <ol>
        <li><strong>顔の座標系：</strong>Forward、Right、Upを明示する。</li>
        <li><strong>角度の意味：</strong>0度と90度、値が増える方向、白黒の意味を決める。</li>
        <li><strong>左右の扱い：</strong>UV反転で共有するか、左右を別データとして持つか決める。</li>
        <li><strong>キー密度：</strong>枚数を均等に増やさず、境界の経路が変わる区間へ置く。</li>
        <li><strong>一方向の変化：</strong>各画素が角度列で一度だけ切り替わる状態を保つ。</li>
        <li><strong>出力仕様：</strong>チャンネル、反転、bit depth、色空間を記録する。</li>
      </ol>
      <p>この六つが曖昧だと、影形状自体が正しくても、シェーダー上では左右反転、角度ずれ、階調劣化として現れます。</p>

      <h2 id="terminology">実際に使われている呼称</h2>
      <p>この分野には統一名称がなく、似た表記でも完成テクスチャ、生成方法、シェーダー機能のどれを指すかが異なります。以下は各資料・実装が実際に使っている表記であり、同義語の標準一覧ではありません。</p>
      <table>
        <caption>公開資料と実装で使われている名称</caption>
        <thead><tr><th scope="col">資料・実装</th><th scope="col">原文の表記</th><th scope="col">主に指しているもの</th></tr></thead>
        <tbody>
          <tr><th scope="row">Hi-Fi RUSH GDC講演</th><td>threshold map</td><td>光角度と比較する完成テクスチャ</td></tr>
          <tr><th scope="row">CGWORLD</th><td>Face Threshold Map</td><td>Hi-Fi RUSHの顔影制御</td></tr>
          <tr><th scope="row">ながむしメモ</th><td>Shadow Threshold Map</td><td>SDF距離比で生成する切替値マップ</td></tr>
          <tr><th scope="row">akasaki1211</th><td>sdf_shadow_threshold_map</td><td>SDF補間による生成ツール</td></tr>
          <tr><th scope="row">Anime Shading Plus</th><td>Face Shadow Map／SDF-based face shadow map</td><td>シェーダー入力と生成工程</td></tr>
          <tr><th scope="row">lilToon</th><td>SDF Face Shadow／Shadow SDF mode</td><td>マテリアル・シェーダー機能</td></tr>
          <tr><th scope="row">PotaToon</th><td>Face SDF</td><td>シェーダー内の顔陰影機能</td></tr>
          <tr><th scope="row">Natane Toon Shader</th><td>SDF Shadow Map／SDF Shadow Texture</td><td>シェーダー機能と入力テクスチャ</td></tr>
        </tbody>
      </table>
      <p>本サイトでは出力の役割を示すため「顔影スレッショルドマップ」と表記し、SDFは角度別マスクの境界を補間する生成手法として説明します。<code>Face SDF</code>を採用する製品は実在しますが、すべての方式を指す一般名称とは扱いません。</p>

      <h2 id="summary">まとめ</h2>
      <p>顔影スレッショルドマップは、完成した顔影の色を保存する画像ではなく、各画素が光角度のどこでLightとShadowを切り替えるかを保存するデータです。</p>
      <ol>
        <li>代表角度ごとに望ましい白黒マスクを作る</li>
        <li>離散した境界の移動を補間する</li>
        <li>各画素の切替角を一つの値へ変換する</li>
        <li>頭部に対する現在の光角度と比較する</li>
      </ol>
      <p>SDFは2番目を実現する一つの手段です。何でも解決する陰影方式ではなく、主に横方向へ変化する顔影を、アーティストが輪郭として設計するための限定されたデータ表現です。</p>

      <SourceList>
        <h3>主な参考資料</h3>
        <ul>
          <li><a href="https://media.gdcvault.com/gdc2024/Slides/GDC%2Bslide%2Bpresentations/Tanaka_Kosuke_3D_Toon_Rendering.pdf">3D Toon Rendering in Hi-Fi RUSH — GDC 2024 slides</a></li>
          <li><a href="https://cgworld.jp/article/202306-hifirush01.html">CGWORLD：Hi-Fi RUSH キャラクター・モーション・エフェクト編</a></li>
          <li><a href="https://nagakagachi.hatenablog.com/entry/2024/03/02/140704">SDF Based Transition Blending for Shadow Threshold Map</a></li>
          <li><a href="https://github.com/akasaki1211/sdf_shadow_threshold_map">akasaki1211/sdf_shadow_threshold_map</a></li>
          <li><a href="https://erichu33.github.io/ASPDocs/en/articles/face-shadow-map-creation-and-baking-workflow.html">Anime Shading Plus：Face Shadow Map Creation & Baking Workflow</a></li>
          <li><a href="https://github.com/lilxyzw/lilToon/blob/master/Assets/lilToon/CHANGELOG.md">lilToon changelog</a></li>
          <li><a href="https://potatoon.dev/en/features/material-settings">PotaToon material settings</a></li>
          <li><a href="https://github.com/natane010/natane_toon_shader/blob/v1.1.5/Website/en/params/lighting/sdf-shadow.html">Natane Toon Shader：SDF Shadow</a></li>
        </ul>
      </SourceList>
    </ArticleLayout>
  );
}
