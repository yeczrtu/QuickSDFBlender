import type { Metadata } from "next";
import study from "../../../public/research/threshold-study/results.json";
import {
  ArticleFigure,
  ArticleLayout,
  EvidenceNote,
  SourceList,
} from "../article-layout";
import {
  absoluteArticleUrl,
  articlePath,
  basePath,
  getArticle,
  siteOrigin,
} from "../article-data";

const article = getArticle("sdf-threshold-interpolation");
const research = basePath + "/research/threshold-study/";
const studyScriptUrl = "https://github.com/yeczrtu/QuickSDFBlender/blob/main/website/scripts/generate-threshold-study.mjs";
const studyResultsUrl = "https://github.com/yeczrtu/QuickSDFBlender/blob/main/website/public/research/threshold-study/results.json";

export const metadata: Metadata = {
  title: article.title + " | Quick SDF Paint",
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
      url: siteOrigin + article.image,
      width: 2592,
      height: 1552,
      alt: "同じ角度別マスクを最近傍、画素線形、正規化ボックスブラー、SDF距離比で補間した比較",
    }],
  },
  twitter: {
    card: "summary_large_image",
    title: article.title,
    description: article.description,
    images: [siteOrigin + article.image],
  },
};

const toc = [
  { id: "definition", label: "入力と出力を定義する" },
  { id: "methods", label: "4つの生成方法" },
  { id: "distance-ratio", label: "SDF距離比の導出" },
  { id: "experiment", label: "比較実験の条件" },
  { id: "results", label: "実測結果" },
  { id: "failures", label: "SDFが失敗する条件" },
  { id: "monotonicity", label: "単調性は補間前の条件" },
  { id: "precision", label: "8-bitと16-bit" },
  { id: "exact-edt", label: "exact EDTの意味" },
  { id: "conclusion", label: "結論" },
] as const;

type MethodKey = keyof typeof study.aggregate;
const methodLabels: Record<MethodKey, string> = {
  nearestKey: "最近傍キー",
  pixelLinear: "画素線形＋二値化",
  blurredCumulative: "初回Lightキー＋正規化ボックスブラー",
  sdfDistanceRatio: "SDF距離比（exact EDT）",
};

function percent(value: number) {
  return value.toFixed(2) + "%";
}

function productionScaleDegrees(value: number | undefined) {
  return typeof value === "number" ? value.toFixed(3) + "°" : "—";
}

export default function SdfThresholdInterpolationArticle() {
  return (
    <ArticleLayout
      article={article}
      toc={toc}
      lead="同じ7枚の二値マスクを、最近傍、画素線形、ボックスブラー、符号付き距離場（Signed Distance Field、SDF）の距離比でキー間へ復元しました。SDFが有効な条件だけでなく、凹形状、成分の出現、細線で起きる誤差も実測します。"
    >
      <EvidenceNote title="比較実験の条件">
        <p>512 × 512 px、7キー、数式で定義した6種類の形状を使う、再現可能な比較実験を新たに作成しました。1°～89°の89点を正解画像と比較し、全画像の画素不一致率、IoU、制作進行度に沿った変化量、切替位置の誤差を計測しています。</p>
        <p>掲載画像とJSONは、同じスクリプトと同じ入力条件から生成されます。</p>
      </EvidenceNote>

      <h2 id="definition">入力と出力を定義する</h2>
      <p>最初に4つの量を分けます。<code>L</code>は頭部ローカル座標の単位ライト方向ベクトル、<code>φ</code>は<code>L</code>から求める符号付き水平ライト角、<code>t</code>は<code>Light Starts</code>から<code>Full Light</code>までの制作進行度、<code>u(p)</code>は画素<code>p</code>がLight状態へ切り替わる正規化位置です。</p>
      <p>本実験では<code>t</code>を0～1へ正規化し、7枚のキーを<code>t = 0, 1/6, 2/6, …, 1</code>へ配置します。画面上の0°～90°は<code>t</code>を読みやすくするための制作目盛りであり、物理的なライト角<code>φ</code>そのものではありません。</p>
      <pre><code>i = 0, …, 6{"\n"}t_i = i / 6{"\n"}M_i(p) = 1  (Light){"\n"}M_i(p) = 0  (Shadow)</code></pre>
      <p>完成するスレッショルドマップの値を<code>u(p)</code>とすると、実行時に必要な基本判定は1つです。次の角括弧は、条件が真なら1、偽なら0を返すアイバーソン括弧（Iverson bracket）を表します。</p>
      <pre><code>Light(t, p) = [t ≥ u(p)]</code></pre>
      <p>実際のシェーダーでは、頭部ローカルの<code>L</code>から水平ライト角<code>φ</code>を求め、設定した開始角と終了角の間を制作進行度<code>t</code>へ写像して比較します。シェーダーによって値や比較方向を反転して保存する場合はありますが、情報の意味は同じです。</p>
      <p><code>u</code>は距離でも物理角でもなく、制作進行度上の切替位置です。SDFが持つ境界付近の距離や勾配は、完成テクスチャには残りません。</p>
      <p>Quick SDF Paint 0.7.1のlilToon向け既定出力では、<code>u</code>を反転して16-bitの値へ量子化します。</p>
      <pre><code>q_lilToon(p) = round((1 - u(p)) × 65535)</code></pre>
      <p><a href="https://media.gdcvault.com/gdc2024/Slides/GDC%2Bslide%2Bpresentations/Tanaka_Kosuke_3D_Toon_Rendering.pdf">『Hi-Fi RUSH』GDC資料</a>にも、固定角度の二値マスクをDCC上で生成・修正し、1枚のthreshold mapへまとめる制作工程があります。ただし公開資料だけから内部生成式の詳細までは判断できません。本記事の数式と結果は、公開された別実装と本記事の検証コードに基づきます。</p>

      <h2 id="methods">比較する4つの方法</h2>
      <h3>1. 最も近いキーを使う</h3>
      <pre><code>k = round(6t){"\n"}Light(t, p) = M_k(p)</code></pre>
      <p>作者が描いたキーの形を完全に保ちますが、隣のキーとの中間を越えるまで境界は止まり、差分領域が一度に切り替わります。キーを増やせば段差は細かくなる一方、画像数と修正箇所も増えます。</p>

      <h3>2. 二値画素を線形補間して0.5で切る</h3>
      <pre><code>V(p) = (1 - f) M_current(p) + f M_next(p){"\n"}Light = [V(p) ≥ 0.5]</code></pre>
      <p><code>M_current = 0</code>、<code>M_next = 1</code>の画素では、結果がLight状態になる条件は常に<code>f ≥ 0.5</code>です。二値結果として評価する限り、変化する全画素が区間中央で同時に反転するため、今回の最近傍キーと同じ結果になりました。</p>
      <p>グレーのまま影色を混ぜればフェードになりますが、輪郭が移動する二値影とは別の表現です。</p>

      <h3>3. 最初にLight状態になるキーを空間方向へぼかす</h3>
      <p>各画素が最初にLight状態になったキーの制作進行度を段階値として保存し、水平・垂直方向へ半径8 px、カーネル幅17 pxのボックスブラー（box blur）を適用しました。</p>
      <p>常時Shadow状態の画素は無効値として計算から除外します。有効な切替値と有効画素の重みを別々にぼかして除算し、正規化した後、常時Shadow状態の画素を元へ戻します。これにより番兵値自体は周囲へ混ざりません。</p>
      <p>実装は単純で段差も弱められますが、ぼかし幅は解像度へ依存し、細い領域や近接する輪郭の間で切替値が混ざります。これは一般的なブラー方式すべての代表ではなく、比較用に固定した1つの簡易ベースラインです。</p>

      <h3>4. SDFの距離比を使う</h3>
      <p>各二値マスクから、Light状態を正、Shadow状態を負とするSDFを作ります。この正負は本記事と検証コードで採用した符号規約です。逆に定義しても、式全体の符号を一貫して反転すれば結果は変わりません。</p>
      <p>距離は、後述する厳密ユークリッド距離変換（exact Euclidean distance transform、exact EDT）で求めます。この実装におけるSDFの絶対値は、最寄りの反対クラスの画素中心までの距離です。隣接キーでShadow状態からLight状態へ変わる画素について、2つの距離から区間内の切替位置を求めます。</p>

      <h2 id="distance-ratio">SDF距離比は何を計算しているのか</h2>
      <p>現在のキーではShadow状態、次のキーではLight状態である画素を考えます。現在のSDFは<code>S_current(p) &lt; 0</code>、次のSDFは<code>S_next(p) &gt; 0</code>です。それぞれの絶対距離を次のように定義します。</p>
      <pre><code>d_current = -S_current(p){"\n"}d_next = S_next(p)</code></pre>
      <p>区間内の遷移率<code>r(p)</code>と、制作進行度上の切替位置<code>u(p)</code>は次の比になります。</p>
      <pre><code>r(p) = d_current / (d_current + d_next){"\n"}u(p) = t_current + (t_next - t_current) r(p)</code></pre>
      <p>同じ値は、2つのSDFを線形補間したときのゼロ交差からも導けます。</p>
      <pre><code>(1 - f) S_current(p) + f S_next(p) = 0{"\n"}f = S_current(p) / (S_current(p) - S_next(p))</code></pre>
      <p>実行時に2枚のSDFを読む代わりに、ゼロを横切る制作進行度だけを1枚のスレッショルドマップへ保存している、と捉えられます。SDFはこの生成が終われば不要です。</p>
      <ArticleFigure
        src={research + "sdf-stages.png"}
        alt="30°のマスク、そのSDF、45°のマスク、画素ごとの切替値を並べた生成段階"
        caption="左から、30°の二値マスク、そのSDF、45°の二値マスク、7キーから生成した切替値。距離場は中間計算で、右端が最終的に必要な制作進行度上の切替データ。"
        width={2072}
        height={512}
      />

      <h2 id="experiment">比較実験の条件</h2>
      <p>手描き画像を選ぶと、特定の方式に都合のよい形を採用する余地があります。そこで制作進行度<code>t</code>に対して連続する正解形状を数式で定義し、0°、15°、30°、45°、60°、75°、90°の制作目盛りに対応する7枚だけを取り出しました。各方式にはこの7枚だけを渡し、元の連続式は与えません。</p>
      <p>この7キー構成は、比較条件を等間隔に揃えるための実験用です。Quick SDF Paint 0.7.1の新規プロジェクトが作る既定8キーとは異なり、製品のキー数を推奨するための実験ではありません。</p>
      <ul>
        <li>解像度：512 × 512 px</li>
        <li>中間評価：1°～89°の89点を1°刻み</li>
        <li>形状：直線、非線形な曲線、凹形状、成分の出現と結合、画像端、細線と分岐</li>
        <li>距離変換：最寄りの反対クラスの画素中心まで測るexact EDT</li>
        <li>評価：全画素不一致率、IoU、1°ごとの変化量、切替位置<code>u(p)</code>の制作目盛り換算MAE</li>
        <li>補正：結果を見た後のパラメーター調整なし</li>
      </ul>
      <p>円や単一点だけに限定せず、SDFが苦手とする形状の分離・結合と凹部も含めています。全白・全黒のキーから新しい成分が現れる区間では、距離比を定義できないため区間中央をフォールバックとしました。この選択も結果へ含まれます。</p>

      <h2 id="results">実測結果</h2>
      <p>次の画像は、凹形状を3つの評価位置で比較したものです。列は左から、正解、最近傍、画素線形、正規化ボックスブラー、SDF距離比です。最近傍と画素線形は一致し、ボックスブラーは形全体を太らせ、SDFはキー間で境界を進めています。</p>
      <ArticleFigure
        src={research + "method-comparison.png"}
        alt="凹形状の正解と、最近傍、画素線形、正規化ボックスブラー、SDF距離比の4方式を3つの評価位置で比較した画像"
        caption="行は22.5°、37.5°、52.5°。白＝数式上の正解、橙＝最近傍と画素線形、青＝正規化ボックスブラー、緑＝SDF距離比。"
        width={2592}
        height={1552}
      />

      <p>集計値は次のとおりです。進行度変化の標準偏差と最大1°変化量は、小さいほど隣接する評価点間の変化量が均等で、急な切替が少ないことを示します。ただし、まったく変化しない誤った結果でも小さくなるため、画素誤差やIoUと併せて評価します。</p>
      <p>切替位置の平均絶対誤差（MAE）は、正解の<code>u(p)</code>が0～1に収まる画素を対象に、各方式が推定した切替位置との差を0°～90°の制作目盛りへ換算した値です。物理的なライト角<code>φ</code>の誤差ではありません。</p>
      <div className="article-table-scroll">
        <table className="article-wide-table">
          <caption>6形状・89評価点を集計した4方式の比較</caption>
          <thead>
            <tr>
              <th scope="col">方式</th>
              <th scope="col">平均画素誤差</th>
              <th scope="col">平均IoU</th>
              <th scope="col">進行度変化の標準偏差</th>
              <th scope="col">最大1°変化量</th>
              <th scope="col">切替位置MAE（制作目盛り換算、°）</th>
            </tr>
          </thead>
          <tbody>
            {(Object.keys(methodLabels) as MethodKey[]).map((key) => {
              const metrics = study.aggregate[key] as (typeof study.aggregate)[MethodKey] & {
                meanTransitionAngleErrorDegrees?: number;
              };
              return (
                <tr key={key}>
                  <th scope="row">{methodLabels[key]}</th>
                  <td>{percent(metrics.meanPixelErrorPercent)}</td>
                  <td>{metrics.meanIoU.toFixed(3)}</td>
                  <td>{percent(metrics.temporalChangeStdDevPercent)}</td>
                  <td>{percent(metrics.peakOneDegreeChangePercent)}</td>
                  <td>{productionScaleDegrees(metrics.meanTransitionAngleErrorDegrees)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p>下表は6形状について、1°～89°の全画像で測った平均画素不一致率です。小さいほど、数式から直接作った正解画像に近いことを示します。</p>
      <table>
        <caption>6種類の形状における平均画素不一致率</caption>
        <thead><tr><th scope="col">検証形状</th>{(Object.keys(methodLabels) as MethodKey[]).map((key) => <th scope="col" key={key}>{methodLabels[key]}</th>)}</tr></thead>
        <tbody>
          {Object.entries(study.scenes).map(([sceneId, scene]) => (
            <tr key={sceneId}>
              <th scope="row">{scene.label}</th>
              {(Object.keys(methodLabels) as MethodKey[]).map((key) => <td key={key}>{percent(scene.methods[key].meanPixelErrorPercent)}</td>)}
            </tr>
          ))}
          <tr>
            <th scope="row">6形状の平均</th>
            {(Object.keys(methodLabels) as MethodKey[]).map((key) => <td key={key}><strong>{percent(study.aggregate[key].meanPixelErrorPercent)}</strong></td>)}
          </tr>
        </tbody>
      </table>
      <p>この実験ではSDF距離比が全体として最も低い平均誤差と高いIoUになりました。一方、結果は「SDFなら真の形状変化を復元できる」という証明ではありません。7枚の境界間を距離だけから推定した結果であり、形の意味的な対応は与えていません。</p>
      <p>最近傍と画素線形の値が完全に同じなのも重要です。二値化を前提にRGBを混ぜても、境界移動にはならないことを数式と実測の両方で確認できます。</p>

      <h2 id="failures">SDFが失敗する条件</h2>
      <p>SDFは「最寄りの反対クラスの画素中心までの距離」は保持していますが、「1枚目のどの部分が2枚目のどこへ移動したか」は保持していません。凹部、分岐、新しい成分の発生や結合では、最短距離がアーティストの意図した対応にならないことがあります。</p>
      <ArticleFigure
        src={research + "topology-comparison.png"}
        alt="円形の成分が途中で出現する形状を正解、正規化ボックスブラー、SDF距離比で比較した画像"
        caption="左から正解、正規化ボックスブラー、SDF距離比。見た目が近い区間でも、キーの一方に成分が存在しなければ距離比は定義できず、この検証では区間中央を使っている。"
        width={1552}
        height={1552}
      />
      <p>また、隣接する2枚ずつの補間は各キー位置では同じマスクに一致しますが、区間境界で境界の移動速度まで一致する保証はありません。この制約は<a href="https://nagakagachi.hatenablog.com/entry/2024/03/02/140704">公開されているSDF距離比の解説</a>でも指摘されています。</p>
      <p>実用上の利点は「必ず正解になること」ではなく、少ないキーから広い範囲の境界移動を自動生成しやすいことです。破綻する区間だけキーを追加し、アーティストが修正できるワークフローと組み合わせる必要があります。</p>

      <h2 id="monotonicity">単調性は補間方式より前の条件</h2>
      <p>1枚のスレッショルドマップは、各画素に1つの切替位置しか保存できません。制作進行度<code>t</code>が増えるほどLight状態が広がる契約なら、すべての画素と隣接キーで次が成立する必要があります。</p>
      <pre><code>M_current(p) ≤ M_next(p)</code></pre>
      <p><code>Shadow → Light → Shadow</code>と往復する画素には、2つの切替位置が必要です。1チャンネルの値では表現できません。これはSDFの精度不足ではなく、出力形式の情報量による制約です。</p>
      <p>補間器に不正な列を黙って渡すのではなく、逆遷移と複数遷移を先に検出します。修復する場合も、補間とは別工程として元画像を保持し、どの画素を変更したか追跡できる方が安全です。</p>

      <h2 id="precision">8-bitと16-bitの違い</h2>
      <p>正規化値を<code>b</code>ビットへ最近傍丸め（round to nearest）で保存した場合、0°～90°の制作目盛りへ換算した理論上の最大量子化誤差は次のとおりです。</p>
      <pre><code>maximum error = 90° / (2 × (2^b - 1))</code></pre>
      <table>
        <caption>8-bitと16-bitの制作目盛り換算量子化誤差</caption>
        <thead><tr><th scope="col">精度</th><th scope="col">今回の平均誤差</th><th scope="col">最大誤差</th></tr></thead>
        <tbody>
          <tr><th scope="row">8-bit</th><td>{study.quantization.uint8.meanAngleErrorDegrees.toFixed(6)}°</td><td>{study.quantization.uint8.maxAngleErrorDegrees.toFixed(6)}°</td></tr>
          <tr><th scope="row">16-bit</th><td>{study.quantization.uint16.meanAngleErrorDegrees.toFixed(6)}°</td><td>{study.quantization.uint16.maxAngleErrorDegrees.toFixed(7)}°</td></tr>
        </tbody>
      </table>
      <p>これは量子化だけの誤差です。元マスクの解像度、補間方式、テクスチャフィルタリング、圧縮、色空間変換は含みません。16-bit化は、不正なキー列や形状対応の誤りを直すものではありません。</p>
      <p>値は色ではなくデータなので、<code>Non-Color</code>として読み込み、意図しないsRGB変換と非可逆圧縮を避けます。</p>

      <h2 id="exact-edt">exact EDTの「exact」とは</h2>
      <p>今回の参照実装は、二次元変換を各軸の一次元変換へ分離するexact EDTを使っています。<a href="https://theoryofcomputing.org/articles/v008a019/">FelzenszwalbとHuttenlocherの論文</a>は、サンプルされた関数の距離変換を格子点数に対して線形時間で計算する方法を示しています。<a href="https://doi.org/10.1109/TPAMI.2003.1177156">Maurerらの論文</a>も任意次元のexact EDTを扱います。</p>
      <p>ここでのexactは「二値画像の画素中心間のユークリッド距離として正確」という意味です。元の連続輪郭や、画素の間を通る境界を復元できるという意味ではありません。アンチエイリアス画像からサブピクセル境界を扱う距離変換は別の問題です。</p>
      <p>exact EDTであっても、別成分の対応、新しい成分の発生、区間をまたぐ速度、解像度未満の細線、UVアイランドをまたいだ距離、上下方向の光や遮蔽は解決しません。</p>

      <h2 id="conclusion">結論</h2>
      <ul>
        <li>最近傍キーは元画像を保つが、境界が段階的に切り替わる</li>
        <li>二値画像の画素線形補間は、0.5で二値化すると最近傍と同じになる</li>
        <li>正規化ボックスブラーは段差を弱められるが、解像度依存の調整と形状の膨張・漏れを伴う</li>
        <li>SDF距離比は少ないキーから境界移動を作りやすいが、形状の意味やトポロジーを理解しない</li>
        <li>最終出力はSDFではなく、制作進行度上の切替位置を格納したスレッショルドマップである</li>
        <li>1画素につき1値の形式では、制作進行度方向の単調性が必要である</li>
      </ul>
      <p>SDF補間は万能な影生成ではなく、角度別マスクを少ない手作業で連続化するための道具です。性質と失敗条件を理解し、破綻する場所だけを人が直すことで、実制作での反復量を減らせます。</p>
      <p>この補間をBlender上で確認し、必要な区間だけキーを増やす手順は、<a href={articlePath("blender-threshold-map-workflow")}>Quick SDF Paint 0.7.1で顔影スレッショルドマップを作る：Blenderでの実践手順</a>で説明します。</p>

      <SourceList>
        <h2>検証データと主な参考資料</h2>
        <ul>
          <li><a href={research + "results.json"}>本記事の未丸め検証結果（JSON）</a></li>
          <li><a href={studyScriptUrl}>検証画像とJSONを生成するスクリプト（GitHub）</a></li>
          <li><a href={studyResultsUrl}>コミットされた未丸め検証結果（GitHub）</a></li>
          <li><a href="https://media.gdcvault.com/gdc2024/Slides/GDC%2Bslide%2Bpresentations/Tanaka_Kosuke_3D_Toon_Rendering.pdf">3D Toon Rendering in Hi-Fi RUSH — GDC 2024 slides</a></li>
          <li><a href="https://theoryofcomputing.org/articles/v008a019/">Felzenszwalb &amp; Huttenlocher, Distance Transforms of Sampled Functions</a></li>
          <li><a href="https://doi.org/10.1109/TPAMI.2003.1177156">Maurer, Qi &amp; Raghavan, A Linear Time Algorithm for Computing Exact Euclidean Distance Transforms</a></li>
          <li><a href="https://www.sciencedirect.com/science/article/pii/S0167865510002953">Gustavson &amp; Strand, Anti-aliased Euclidean distance transform</a></li>
          <li><a href="https://nagakagachi.hatenablog.com/entry/2024/03/02/140704">SDF Based Transition Blending for Shadow Threshold Map</a></li>
          <li><a href="https://github.com/akasaki1211/sdf_shadow_threshold_map">akasaki1211/sdf_shadow_threshold_map</a></li>
        </ul>
      </SourceList>
    </ArticleLayout>
  );
}
