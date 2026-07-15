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
      alt: "同じ角度別マスクを最近傍、画素線形、簡易ブラー、SDF距離比で補間した比較",
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
  { id: "exact-edt", label: "Exact EDTの意味" },
  { id: "conclusion", label: "結論" },
] as const;

type MethodKey = keyof typeof study.aggregate;
const methodLabels: Record<MethodKey, string> = {
  nearestKey: "最近傍キー",
  pixelLinear: "画素線形＋二値化",
  blurredCumulative: "初回キー＋Box Blur",
  sdfDistanceRatio: "Exact SDF距離比",
};

function percent(value: number) {
  return value.toFixed(2) + "%";
}

function degrees(value: number | undefined) {
  return typeof value === "number" ? value.toFixed(3) + "°" : "—";
}

export default function SdfThresholdInterpolationArticle() {
  return (
    <ArticleLayout
      article={article}
      toc={toc}
      lead="同じ7枚の二値マスクを、最近傍、画素線形、簡易ブラー、Signed Distanceの距離比で中間角度へ復元しました。SDFが有効な条件だけでなく、凹形状、成分の出現、細線で起きる誤差も実測します。"
    >
      <EvidenceNote title="比較実験の条件">
        <p>512×512、7キー、6種類の数式形状を使う決定的な検証を新規作成しました。1～89度の89評価角度を正解画像と比較し、全画像の画素不一致率、IoU、時間方向の変化量、切替位置の角度誤差を計測しています。掲載画像とJSONは同じスクリプトから生成されます。</p>
      </EvidenceNote>

      <h2 id="definition">入力と出力を定義する</h2>
      <p>最初に三つの量を分けます。<code>φ</code>は頭部に対する実ライト方向、<code>t</code>はLight StartsからFull Lightまでの制作上の進行度、<code>u(p)</code>は画素<code>p</code>がLightへ切り替わる正規化位置です。本実験は<code>t</code>を0から1へ正規化し、7枚のキーを<code>t = 0, 1/6, 2/6 … 1</code>へ配置します。0～90度という表記は<code>t</code>を読みやすくした実験上の目盛りで、物理的なライト角<code>φ</code>ではありません。</p>
      <pre><code>M_i(p) = 1 : Light{"\n"}M_i(p) = 0 : Shadow</code></pre>
      <p>完成するスレッショルドマップの値を<code>u(p)</code>とすると、ランタイムで必要な基本判定は一つです。</p>
      <pre><code>Light(t, p) = [ t &gt;= u(p) ]</code></pre>
      <p>実際のシェーダーでは、実ライト方向<code>φ</code>から<code>t</code>を求めて比較します。シェーダーによって値や比較方向を反転して保存する場合はありますが、情報の意味は同じです。<code>u</code>は距離ではなく、制作進行方向の切替位置です。一般的なSDFが持つ境界からの長さや勾配は、完成テクスチャには残りません。</p>
      <p>Quick SDF Paint 0.7.1のlilToon向け既定出力では、<code>u</code>を反転して16-bitへ量子化します。</p>
      <pre><code>q_lilToon(p) = round((1 - u(p)) × 65535)</code></pre>
      <p><a href="https://media.gdcvault.com/gdc2024/Slides/GDC%2Bslide%2Bpresentations/Tanaka_Kosuke_3D_Toon_Rendering.pdf">『Hi-Fi RUSH』GDC資料</a>にも、固定角度の二値マスクをDCC上で生成・修正し、一枚のthreshold mapへまとめる制作工程があります。ただし公開資料だけから内部生成式の詳細までは判断できません。本記事の数式と結果は、公開された別実装と本記事の検証コードに基づきます。</p>

      <h2 id="methods">比較する4つの方法</h2>
      <h3>1. 最も近いキーを使う</h3>
      <pre><code>k = round(6t){"\n"}Light(t, p) = M_k(p)</code></pre>
      <p>作者が描いたキーの形を完全に保ちますが、隣のキーとの中間を越えるまで境界は止まり、差分領域が一度に切り替わります。キーを増やせば段差は細かくなる一方、画像数と修正箇所も増えます。</p>

      <h3>2. 二値画素を線形補間して0.5で切る</h3>
      <pre><code>V(p) = (1 - f) M_current(p) + f M_next(p){"\n"}Light = V(p) &gt;= 0.5</code></pre>
      <p><code>M_current=0</code>、<code>M_next=1</code>の画素では、結果がLightになる条件は常に<code>f &gt;= 0.5</code>です。二値結果として評価する限り、変化する全画素が区間中央で同時に反転するため、今回の最近傍キーと同じ結果になりました。グレーのまま影色を混ぜればフェードになりますが、輪郭が移動する二値影とは別の表現です。</p>

      <h3>3. 最初にLightになるキーを空間方向へぼかす</h3>
      <p>各画素が最初にLightになったキー番号を段階値として保存し、水平・垂直方向へ半径8pxのBox Blurを適用しました。実装は単純で段差も弱められますが、ぼかし幅は解像度へ依存し、細い領域や近接する形状へ値が漏れます。これは一般的なブラー方式すべての代表ではなく、比較用に固定した一つの簡易ベースラインです。</p>

      <h3>4. Signed Distanceの距離比を使う</h3>
      <p>各二値マスクからLight側を正、Shadow側を負とするSigned Distanceを作ります。この正負は本記事と検証コードで採用した符号規約で、逆に定義しても式を一貫して反転すれば結果は同じです。隣接キーでShadowからLightへ変わる画素について、二つの境界までの距離から区間内の切替位置を求めます。</p>

      <h2 id="distance-ratio">SDF距離比は何を計算しているのか</h2>
      <p>現在のキーではShadow、次のキーではLightである画素を考えます。現在の境界までの距離を<code>d_current</code>、次の境界までの距離を<code>d_next</code>とすると、区間内の遷移率は次の比になります。</p>
      <pre><code>r(p) = d_current / (d_current + d_next){"\n"}u(p) = t_current + (t_next - t_current) r(p)</code></pre>
      <p>同じ値は、二つのSigned Distanceを線形補間したときのゼロ交差からも導けます。</p>
      <pre><code>(1-f) S_current(p) + f S_next(p) = 0{"\n"}f = S_current / (S_current - S_next)</code></pre>
      <p>毎フレーム二枚のSDFを読む代わりに、ゼロを横切る時刻だけを一枚へ保存している、と捉えられます。SDFはこの生成が終われば不要です。</p>
      <ArticleFigure
        src={research + "sdf-stages.png"}
        alt="30度マスク、Signed Distance、45度マスク、画素ごとの切替値を並べた生成段階"
        caption="左から、30度の二値マスク、そのSigned Distance、45度の二値マスク、7キーから生成した切替値。距離場は中間計算で、右端が最終的に必要な角度データ。"
        width={2072}
        height={512}
      />

      <h2 id="experiment">比較実験の条件</h2>
      <p>手描き画像を選ぶと、特定の方式に都合のよい形を採用する余地があります。そこで連続角度における正解形状を数式で定義し、そこから0、15、30、45、60、75、90度の7枚だけを取り出しました。各方式には7枚だけを渡し、元の連続式は見せません。</p>
      <p>この7キー構成は比較条件を等間隔に揃えるための実験用です。Quick SDF Paint 0.7.1の新規Projectが作る既定8キーとは別で、製品のキー数を推奨するための実験ではありません。</p>
      <ul>
        <li>解像度：512×512</li>
        <li>中間評価：1～89度を1度刻み</li>
        <li>形状：直線、非線形な曲線、凹形状、成分の出現と結合、画像端、細線と分岐</li>
        <li>距離変換：画素中心上で最寄りの反対クラスの画素中心まで測るExact Euclidean Distance Transform</li>
        <li>評価：全画素不一致率、IoU、1度ごとの変化量、切替位置<code>u(p)</code>の平均角度誤差</li>
        <li>補正：結果を見た後のパラメーター調整なし</li>
      </ul>
      <p>円や単一点だけに限定せず、SDFが苦手とする形状の分離・結合と凹部も含めています。全白・全黒のキーから新しい成分が現れる区間では、距離比を定義できないため区間中央をフォールバックとしました。この選択も結果へ含まれます。</p>

      <h2 id="results">実測結果</h2>
      <p>次の画像は凹形状の3つの中間角度です。列は左から正解、最近傍、画素線形、簡易ブラー、SDF距離比です。最近傍と画素線形が一致し、ブラーは形全体を太らせ、SDFはキー間で境界を進めています。</p>
      <ArticleFigure
        src={research + "method-comparison.png"}
        alt="凹形状を正解、最近傍、画素線形、簡易ブラー、SDF距離比の5方式で比較した3角度の画像"
        caption="行は22.5度、37.5度、52.5度。白＝数式上の正解、橙＝最近傍と画素線形、青＝簡易ブラー、緑＝SDF距離比。"
        width={2592}
        height={1552}
      />

      <p>集計値は次のとおりです。時間変化の標準偏差と最大1度変化量は、小さいほど角度ごとの変化量が均等で、急な切替が少ないことを示します。切替角MAEは、正解の<code>u(p)</code>と各方式が推定した切替位置の平均絶対誤差です。</p>
      <table>
        <caption>6形状・89評価角度を集計した4方式の比較</caption>
        <thead>
          <tr>
            <th scope="col">方式</th>
            <th scope="col">平均画素誤差</th>
            <th scope="col">平均IoU</th>
            <th scope="col">時間変化の標準偏差</th>
            <th scope="col">最大1度変化量</th>
            <th scope="col">切替角MAE</th>
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
                <td>{degrees(metrics.meanTransitionAngleErrorDegrees)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p>下表は6形状について、1～89度の全画像で測った平均画素不一致率です。小さいほど、数式から直接作った正解画像に近いことを示します。</p>
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
      <p>Signed Distanceは「最も近い境界までの距離」は知っていますが、「一枚目のどの部分が二枚目のどこへ移動したか」は知りません。凹部、分岐、新しい成分の発生や結合では、最短距離がアーティストの意図した対応にならないことがあります。</p>
      <ArticleFigure
        src={research + "topology-comparison.png"}
        alt="円形の成分が途中で出現する形状を正解、簡易ブラー、SDF距離比で比較した画像"
        caption="左から正解、簡易ブラー、SDF距離比。見た目が近い区間でも、キーの一方に成分が存在しなければ距離比は定義できず、この検証では区間中央を使っている。"
        width={1552}
        height={1552}
      />
      <p>また、隣接する二枚ずつの補間は各キー位置では同じマスクに一致しますが、区間境界で境界の移動速度まで一致する保証はありません。この制約は<a href="https://nagakagachi.hatenablog.com/entry/2024/03/02/140704">公開されているSDF距離比の解説</a>でも指摘されています。</p>
      <p>実用上の利点は「必ず正解になること」ではなく、少数キーから広い範囲の境界移動を自動生成しやすいことです。破綻する区間だけキーを追加し、アーティストが修正できるワークフローと組み合わせる必要があります。</p>

      <h2 id="monotonicity">単調性は補間方式より前の条件</h2>
      <p>一枚のスレッショルドマップは、各画素に一つの切替値しか保存できません。角度が進むほどLightが広がる契約なら、すべての画素と隣接キーで次が成立する必要があります。</p>
      <pre><code>M_current(p) &lt;= M_next(p)</code></pre>
      <p><code>Shadow → Light → Shadow</code>と往復する画素には二つの切替角が必要です。一チャンネルの値では表現できません。これはSDFの精度不足ではなく、出力形式の情報量による制約です。</p>
      <p>補間器へ不正な列を黙って渡すのではなく、逆遷移と複数遷移を先に検出します。修復する場合も、補間とは別工程として元画像を保持し、どの画素を変更したか追跡できる方が安全です。</p>

      <h2 id="precision">8-bitと16-bitの違い</h2>
      <p>正規化値を<code>b</code> bitへround-to-nearestで保存した場合、0～90度に換算した理論上の最大量子化誤差は次です。</p>
      <pre><code>maximum error = 90° / (2 × (2^b - 1))</code></pre>
      <table>
        <caption>8-bitと16-bitの角度量子化誤差</caption>
        <thead><tr><th scope="col">精度</th><th scope="col">今回の平均角度誤差</th><th scope="col">最大角度誤差</th></tr></thead>
        <tbody>
          <tr><th scope="row">8-bit</th><td>{study.quantization.uint8.meanAngleErrorDegrees.toFixed(6)}°</td><td>{study.quantization.uint8.maxAngleErrorDegrees.toFixed(6)}°</td></tr>
          <tr><th scope="row">16-bit</th><td>{study.quantization.uint16.meanAngleErrorDegrees.toFixed(6)}°</td><td>{study.quantization.uint16.maxAngleErrorDegrees.toFixed(7)}°</td></tr>
        </tbody>
      </table>
      <p>これは量子化だけの誤差です。元マスクの解像度、補間方式、テクスチャフィルタリング、圧縮、色空間変換は含みません。16-bit化は、不正なキー列や形状対応の誤りを直すものではありません。値は色ではなくデータなので、Non-Colorとして読み込み、意図しないsRGB変換と非可逆圧縮を避けます。</p>

      <h2 id="exact-edt">Exact Euclidean Distance Transformの「Exact」</h2>
      <p>今回の参照実装は、二次元変換を各軸の一次元変換へ分離するExact Euclidean Distance Transformを使っています。<a href="https://theoryofcomputing.org/articles/v008a019/">FelzenszwalbとHuttenlocherの論文</a>は、サンプルされた関数の距離変換を格子点数に対して線形時間で計算する方法を示しています。<a href="https://doi.org/10.1109/TPAMI.2003.1177156">Maurerらの論文</a>も任意次元のexact EDTを扱います。</p>
      <p>ここでのExactは「二値画像の画素中心間のEuclidean Distanceとして正確」という意味です。元のベクター曲線や、画素の間を通る連続境界を復元できるという意味ではありません。アンチエイリアス画像からサブピクセル境界を扱う距離変換は別の問題です。</p>
      <p>Exact EDTであっても、別成分の対応、新しい成分の発生、区間をまたぐ速度、解像度未満の細線、UV島をまたいだ距離、上下方向の光や遮蔽は解決しません。</p>

      <h2 id="conclusion">結論</h2>
      <ul>
        <li>最近傍キーは元画像を保つが、境界が段階的に切り替わる</li>
        <li>二値画像の画素線形補間は、0.5で二値化すると最近傍と同じになる</li>
        <li>簡易ブラーは段差を弱められるが、解像度依存の調整と形状の膨張・漏れを伴う</li>
        <li>SDF距離比は少数キーから境界移動を作りやすいが、形状の意味やトポロジーを理解しない</li>
        <li>最終出力はSDFではなく、角度方向の切替値を格納したスレッショルドマップである</li>
        <li>一画素一値の形式では、角度方向の単調性が必要である</li>
      </ul>
      <p>SDF補間は万能な影生成ではなく、角度別マスクを少ない手作業で連続化するための道具です。性質と失敗条件を理解し、破綻する場所だけを人が直すことで、実制作での反復量を減らせます。</p>
      <p>この補間をBlender上で確認し、必要な区間だけキーを増やす手順は、<a href={articlePath("blender-threshold-map-workflow")}>Quick SDF Paint 0.7.1の実践ワークフロー</a>で説明します。</p>

      <SourceList>
        <h3>検証データと主な参考資料</h3>
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
