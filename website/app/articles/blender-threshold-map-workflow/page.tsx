import type { Metadata } from "next";
import {
  ArticleFigure,
  ArticleLayout,
  EvidenceNote,
  ModelCredit,
} from "../article-layout";
import {
  absoluteArticleUrl,
  articlePath,
  basePath,
  getArticle,
  siteOrigin,
} from "../article-data";

const article = getArticle("blender-threshold-map-workflow");
const media = `${basePath}/media/`;

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
      width: 1920,
      height: 1001,
      alt: "Quick SDF PaintでKipfelの顔影ガイドをBlender上から修正している画面",
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
  { id: "quick-start", label: "完成までの最短5手順" },
  { id: "plan", label: "塗る前に決めること" },
  { id: "prepare", label: "MeshとUVを確認する" },
  { id: "guide", label: "法線ガイドを診断する" },
  { id: "order", label: "全体から細部へ修正する" },
  { id: "features", label: "顔の部位ごとの判断基準" },
  { id: "motion", label: "動きとして確認する" },
  { id: "mirror", label: "ミラーとUVを合わせる" },
  { id: "troubleshooting", label: "症状から原因を切り分ける" },
  { id: "export", label: "書き出しと確認" },
  { id: "done", label: "完成の判断" },
] as const;

export default function BlenderWorkflowArticle() {
  return (
    <ArticleLayout
      article={article}
      toc={toc}
      lead="Quick SDF Paint 0.7.1を使い、法線ガイドから顔影スレッショルドマップを書き出す実践手順です。角度ごとに別の影絵を完成させるのではなく、一つの影境界が制作上の進行度に応じて顔の上をどう移動するかを設計します。"
    >
      <EvidenceNote title="動作確認条件">
        <p>Blender 5.1、Quick SDF Paint 0.7.1、1024px、0～90度の既定8キー、左右ミラーONで確認しました。操作の列挙ではなく、設定異常を先に見分ける方法、大きな形から直す順番、顔の部位ごとの判断、静止画と動きの評価を中心にしています。</p>
      </EvidenceNote>

      <h2 id="quick-start">完成までの最短5手順</h2>
      <p>完成物は、左右からの光に対する画素ごとの切替位置を格納した16-bit RGBA PNGです。通常の陰影画像ではなく、対応シェーダーが光方向と比較するデータテクスチャとして使います。</p>
      <ol>
        <li>顔Meshを選び、Material SlotとUV Mapを確認する</li>
        <li><code>Create &amp; Edit</code>で法線ガイドを作る</li>
        <li>中央付近のキーからLight／Shadowで気になる形だけ直す</li>
        <li>Timelineを端から端まで動かし、境界の動きを確認する</li>
        <li><code>Export Threshold Map</code>で16-bit PNGを書き出す</li>
      </ol>
      <p>仕組みから確認したい場合は先に<a href={articlePath("face-shadow-threshold-map")}>顔影スレッショルドマップの基礎</a>を、補間の性質を検証したい場合は<a href={articlePath("sdf-threshold-interpolation")}>SDF距離補間の比較</a>を参照してください。</p>

      <h2 id="plan">最初に決めるのは「どこを塗るか」ではない</h2>
      <p>一筆目より先に、次の三点を決めます。</p>
      <ol>
        <li><strong>制御する範囲：</strong>通常は肌の顔影が対象です。髪が顔へ落とす影、眼球、口内、アクセサリーまで同じ仕組みで扱うとは限りません。</li>
        <li><strong>明暗のデザイン：</strong>鼻を境に面を大きく分けるのか、頬へ丸く回り込ませるのか、顎まで一体にするのかを決めます。物理的な正しさより、キャラクターデザインとして読める形を優先できます。</li>
        <li><strong>左右の共有：</strong>多くの顔ではミラーから始めた方が速く、角度間も確認しやすくなります。前髪、傷、非対称な造形を影へ反映すると決めた場合だけ後から分けます。</li>
      </ol>
      <p>方針が曖昧なまま各キーを個別に仕上げると、静止画ではきれいでも、角度を動かしたときに影の設計が途中で変わって見えます。</p>

      <h2 id="prepare">1. 顔MeshとUVを確認する</h2>
      <p>Object Modeで顔を含むMeshを選び、Quick SDFパネルで対象のMaterial SlotとUV Mapを確認してから<code>Create &amp; Edit</code>を押します。</p>
      <ArticleFigure
        src={`${media}quick-sdf-create-and-edit.png`}
        alt="Kipfelの顔Mesh、Material Slot、UV Mapを選択したQuick SDFパネル"
        caption="最初に対象Mesh、顔へ割り当てたMaterial Slot、0–1 UV Mapを確認する。"
        width={1920}
        height={1001}
      />
      <ModelCredit />
      <h3>作成前のチェック</h3>
      <ul>
        <li>UVが0～1の範囲に収まっている</li>
        <li>顔に使うMaterial Slotを選んでいる</li>
        <li>対象外の服、髪、アクセサリーの面が同じSlotへ混ざっていない</li>
        <li>意図しないUV重複がない</li>
        <li>制作に使う表情、Shape Key、Armature Poseになっている</li>
      </ul>
      <p>2D Canvasに顔以外の島が大量に見えるときは、ブラシより先にMaterial Slotを確認します。左右で同じUVを共有する重複はミラー方式として利用できますが、無関係な面が重なっている場合は独立して調整できません。</p>

      <h2 id="guide">2. 法線ガイドは完成形ではなく、動きの下描き</h2>
      <p><code>Create &amp; Edit</code>を実行すると、現在のポーズと評価済み法線から角度別の影ガイドが作られます。左の2D Canvas、右の3D View、下のTimelineは同じ角度を示します。</p>
      <ArticleFigure
        src={`${media}quick-sdf-studio-overview.png`}
        alt="左に2D Canvas、右にKipfelの3D View、下に角度Timelineを配置したQuick SDF Paint"
        caption="法線ガイドは、光の変化に対して影がどちらへ移動するかを示す下描き。鼻や口周りの細かな分裂を、そのまま完成形とは考えない。"
        width={1920}
        height={1001}
      />
      <p>Timelineの0度、45度、90度は頭部に対する物理的なライト角ではありません。0度はLight Starts、90度はFull Light、45度はその中間という制作上の進行度です。実ライト方向からこの進行度への変換はシェーダー側で行います。</p>
      <p>まだ塗らずにTimelineを端から端まで動かし、ガイド全体を診断します。次の状態なら局所修正へ進まず、設定を直します。</p>
      <ul>
        <li>ほぼすべてのキーが同じ形に見える</li>
        <li>全面が白または黒のまま変化しない</li>
        <li>明るくなる方向と暗くなる方向が逆</li>
        <li>左右の光で同じ側ばかり変化する</li>
        <li>顔ではないUV islandが主に変化する</li>
      </ul>
      <p>正面方向が違う場合は、顔を正面から見て<code>Use This View as Front</code>を使います。全体の影量だけが多い、または少ない場合は<code>Shadow Amount</code>を調整し、その後に<code>Update Shadow Guide</code>を押してガイドへ反映します。PoseやMeshを変更した場合は<code>Rebake Base</code>を使います。これらは手描き範囲を保持して下描きだけを更新します。</p>
      <ArticleFigure
        src={`${media}quick-sdf-advanced.png`}
        alt="正面方向、Shadow Amount、Mirror、Rebake Baseを含むQuick SDF Paintの詳細設定"
        caption="大量に塗り直す前に、正面方向、全体の影量、対象UV、ミラー方式を確認する。"
        width={1920}
        height={1001}
      />

      <h2 id="order">3. 修正は「全体から細部」の順に行う</h2>
      <p>8キーを左から順に完成させる必要はありません。次の順番にすると、各キーを独立した絵として作ってしまうのを避けられます。</p>
      <ol>
        <li>Light Startsの0度とFull Lightの90度で変化の両端を確認する</li>
        <li>進行度の中央付近で、顔影の主役となるシルエットを作る</li>
        <li>その前後で境界の出現と消失を整える</li>
        <li>Timelineを連続して動かし、急な変形を探す</li>
        <li>隣接キーだけでは経路を作れない場所へ中間キーを追加する</li>
      </ol>
      <p>中央付近は鼻を境に明暗が大きく分かれ、顔影の印象を判断しやすい段階です。ここで大きな形を決めると、ほかのキーでは「その形がどう現れ、どう消えるか」に集中できます。</p>
      <ArticleFigure
        src={`${media}quick-sdf-normal-guide-and-paint.png`}
        alt="2D CanvasとKipfelの3D Viewで同じ顔影境界をLightとShadowで修正している画面"
        caption="3D Viewでは顔としての見え方を、2D Canvasでは境界の細部と隠れた面を確認する。"
        width={1920}
        height={1001}
      />
      <p><code>Light</code>は白、<code>Shadow</code>は黒を塗ります。一筆で変わらない場合は同じ値の場所へ塗っている可能性があります。3D投影が難しい場所は少し引く、Orthographicへ切り替える、または2D Canvasで修正します。</p>

      <h2 id="features">4. 顔の部位ごとの判断基準</h2>
      <p>法線ガイドを直すときは、細かな陰影を写し取るのではなく、通常の表示距離でも読める境界へ整理します。</p>
      <table>
        <caption>顔の部位ごとの確認ポイント</caption>
        <thead><tr><th scope="col">部位</th><th scope="col">見るべき点</th><th scope="col">よくある失敗</th></tr></thead>
        <tbody>
          <tr><th scope="row">額・こめかみ</th><td>大きな面として一続きに移るか</td><td>髪際や細かな起伏を拾い、境界が波打つ</td></tr>
          <tr><th scope="row">鼻筋</th><td>光側と影側を分ける軸として安定するか</td><td>鼻先の小さな島が突然現れたり消えたりする</td></tr>
          <tr><th scope="row">小鼻</th><td>シルエットを補助する大きさに留まるか</td><td>凹凸をすべて拾い、ノイズ状に分裂する</td></tr>
          <tr><th scope="row">頬</th><td>顔の丸みを感じる大きなカーブか</td><td>トポロジーへ沿いすぎて角張る</td></tr>
          <tr><th scope="row">口元</th><td>表情や口テクスチャと競合しないか</td><td>口角や法令線を細かく拾い、表情でちらつく</td></tr>
          <tr><th scope="row">顎</th><td>頬とつなぐか、独立させるか明確か</td><td>キーごとに接続と分離を繰り返す</td></tr>
          <tr><th scope="row">目周辺</th><td>眼球やまつ毛ではなく肌を制御しているか</td><td>別Materialで扱う部位まで含める</td></tr>
          <tr><th scope="row">前髪付近</th><td>顔の陰影と髪の落ち影を分けているか</td><td>動く髪の影を固定UVへ描き込む</td></tr>
        </tbody>
      </table>
      <h3>鼻は細部より、境界の軸を見る</h3>
      <p>鼻周辺は法線変化が大きく、小さな白黒の島が生まれやすい場所です。小島を一つずつ整えるより、鼻筋から鼻先へ一本の境界がどう移動するかを先に決めます。</p>
      <h3>頬は通常の表示距離で判断する</h3>
      <p>拡大すると凹凸まで直したくなりますが、顔影はバストアップや全身でも読める必要があります。細かく正しい輪郭より、頬を大きく横切る安定したカーブが意図を伝えやすい場合があります。</p>
      <h3>口元は表情差分を想定する</h3>
      <p>現在の表情だけに合わせて口角を細かく描くと、Shape Keyで口を動かしたときに影の意味が変わります。表情アニメーションがあるモデルでは、口周辺を単純な面として扱うか、適用範囲から外すことも検討します。</p>

      <h2 id="motion">5. 一枚ずつではなく、連続した変化として確認する</h2>
      <p>キーをクリックするとその位置が編集対象になります。キーの間へTimelineを動かすと補間状態を確認でき、そこで実際に画素を変えた場合だけ自動キーが作られます。スクラブして確認するだけでは画像もキーも増えません。意図せず自動キーを作った場合は、その一筆をUndoするか、対象キーを選んでDeleteします。</p>
      <ArticleFigure
        src={`${media}quick-sdf-angle-seek.gif`}
        reducedMotionSrc={`${media}quick-sdf-angle-seek-poster.png`}
        alt="Timelineを0度から90度まで動かすと2DマスクとKipfelの顔影が連続して変化する画面"
        caption="ゆっくり動かして境界の方向を確認し、速く往復して小さな島の点滅や急な面積変化を探す。"
        width={800}
        height={450}
      />
      <p>良い変化は、各キーの絵が似ていることではありません。LightまたはShadowが一定方向へ広がり、途中で同じ画素が何度も明暗を往復しないことです。</p>
      <ArticleFigure
        src={`${media}quick-sdf-single-playhead.png`}
        alt="角度キーとキー間のプレビューを一本のプレイヘッドで操作するQuick SDF Paint Timeline"
        caption="隣接する形は正しいが、その間の経路だけを変えたい場合に中間キーを追加する。キー数そのものを品質目標にしない。"
        width={2048}
        height={540}
      />

      <h2 id="mirror">6. ミラーが合わないときは描き直さない</h2>
      <p>反対側への反映がずれる、二重になる、変化しない場合は、Mirror方式とUV構成が合っていない可能性があります。</p>
      <table>
        <caption>UV構成とMirror方式の対応</caption>
        <thead><tr><th scope="col">UV構成</th><th scope="col">対応する方式</th></tr></thead>
        <tbody>
          <tr><th scope="row">テクスチャ全体をU反転すると左右が一致</th><td>Whole Texture／Texture Mirror</td></tr>
          <tr><th scope="row">左右のUV islandが別配置で、形が対応</th><td>Paired Islands</td></tr>
          <tr><th scope="row">左右が同じUVを共有</th><td>Shared UV</td></tr>
        </tbody>
      </table>
      <p><code>Break Mirror</code>はトラブル回避のボタンではありません。傷や前髪など、左右を意図的に別データとして描くと決めた場合に使います。まずUVに合う方式を選び直してください。</p>

      <h2 id="troubleshooting">7. 症状から原因を切り分ける</h2>
      <h3>全キーがほぼ白または黒</h3>
      <p>正面方向と<code>Shadow Amount</code>を確認します。方向が誤ったまま大量に塗り直さないでください。</p>
      <h3>2Dには描けるが3Dで変わらない</h3>
      <p>同じ色の場所へ塗っている、2Dで塗った面が現在の視点から隠れている、別のMaterial Slotを見ている、という順に確認します。反対色を試し、モデルを回転させます。</p>
      <h3>顔以外の細かな島が大量にある</h3>
      <p>対象外の面がMaterial Slotへ含まれている可能性があります。<code>SDF Area</code>は、角度別顔影を適用する範囲を指定するQuick SDF Paint共通の補助マスクです。顔と対象外の面が別のUV islandとして分離されている場合は、SDF Areaで顔側だけを指定できます。ただし無関係な面が同じUV座標へ重なっている場合はマスクだけで分離できないため、Material SlotまたはUVを分ける必要があります。</p>
      <h3>左右で明暗の進行が逆</h3>
      <p>正面軸またはMirror方式を確認します。ペイントで左右を無理に合わせても、出力時の光方向との対応は直りません。</p>
      <h3>ポーズを変えたらガイドと合わない</h3>
      <p><code>Rebake Base</code>で評価済みMeshから更新します。手描き修正は保持されますが、変形が大きければ全角度を再確認します。</p>

      <h2 id="export">8. 書き出し前と出力後の確認</h2>
      <ul>
        <li>0度から90度まで明暗が一方向へ変化する</li>
        <li>鼻先や口元の小島が点滅しない</li>
        <li>顔影と髪の落ち影を混同していない</li>
        <li>左右の光で意図した側が変化する</li>
        <li>Mirrorの反映先が正しい</li>
        <li>通常の表示距離でToon Resultを確認した</li>
        <li>出力先シェーダーのチャンネル構成を確認した</li>
      </ul>
      <p><code>Export Threshold Map</code>は角度列を検査し、必要なら書き出し用コピーだけを調整します。元のCanvasとUndo履歴は変わりません。「角度のつながりを自動調整して書き出しました」という表示も成功です。</p>
      <ArticleFigure
        src={`${media}quick-sdf-export.png`}
        alt="Quick SDF PaintのExport Threshold Mapと16-bit PNGの書き出し完了表示"
        caption="初回は保存先を選び、以後は同じ場所へ書き出す。構造的な問題だけが失敗として残る。"
        width={1920}
        height={1001}
      />
      <p>完成画像は16-bit RGBAのデータです。画像ビューアーで自然な色に見えるかではなく、Blenderと出力先シェーダーで光を動かして確認します。</p>
      <ArticleFigure
        src={`${media}quick-sdf-threshold-example.png`}
        alt="Quick SDF Paintが出力した左右の閾値と補助マスクを含む16-bit RGBAテクスチャ"
        caption="複数チャンネルへ数値を格納するため、通常の陰影テクスチャの見た目にはならない。"
        width={1024}
        height={1024}
        contain
      />
      <ol>
        <li>BlenderのToon Resultで端から端まで動かす</li>
        <li>16-bit PNGのまま保存されていることを確認する</li>
        <li>エンジンではsRGBを無効にし、Non-Color／Dataとして読み込む</li>
        <li>対応シェーダーで光を左右へ動かす</li>
        <li>左右が逆ならペイントではなくチャンネル割り当てと反転を確認する</li>
      </ol>
      <p>画像編集ソフトで開いて保存し直すと、8-bit化やカラーマネジメントで値が変わる場合があります。加工する場合はbit depthとデータ用途の色管理を維持してください。</p>

      <h2 id="done">完成を判断する三つの条件</h2>
      <ol>
        <li>代表角度で、キャラクターデザインとして読みやすい影形状になっている</li>
        <li>角度を動かしても、輪郭が意図した方向へ連続して移動する</li>
        <li>実際のシェーダーで、左右の光と出力チャンネルが対応している</li>
      </ol>
      <p>法線ガイドはゼロから描く量を減らしますが、最終判断まで自動化するものではありません。大きなシルエット、顔のランドマーク、角度間の動きという順番で見ると、細部に迷い込まず短い反復で仕上げられます。</p>
    </ArticleLayout>
  );
}
