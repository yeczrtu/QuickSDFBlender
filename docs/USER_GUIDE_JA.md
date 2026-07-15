# Quick SDF Paint 0.7.1 クイックガイド

Quick SDF Paintは、トゥーンレンダリング用の顔影スレッショルドマップを作成するBlender Extensionです。法線から作られた顔影の下描きを、アーティストが顔を見ながら `Light` と `Shadow` で直せます。左右反転、書き出し時の角度整合性調整、最終生成は自動で行われます。

角度別の白黒マスクからSDF距離補間で影の切替値を求め、1枚の16-bit RGBAスレッショルドマップへまとめます。SDFはマスク境界を補間するための内部処理であり、書き出す画像自体はSDFではありません。

> [!NOTE]
> 統一された名称はありません。公開されているシェーダーや資料では、次の表記が実際に使われています。
>
> - lilToon：[`SDF Face Shadow`、`Shadow SDF mode`](https://github.com/lilxyzw/lilToon/blob/master/Assets/lilToon/CHANGELOG.md)
> - PotaToon：[`Face SDF`、`FaceSDF textures`、`Face SDF Tex`](https://potatoon.dev/en/features/material-settings)
> - ChiliMilk URP Toon：[`SDF_FaceShadow`、`sdf shadow mask`](https://github.com/ChiliMilk/URP_Toon)
> - Anime Shading Plus：[`SDF-based face shadow map`](https://erichu33.github.io/ASPDocs/en/articles/face-shadow-map-creation-and-baking-workflow.html)
> - 類似ツール：[`Shadow Threshold Map`、`Face Threshold Map`、`Face Shadow Map`](https://github.com/nagakagachi/NagaSdfTextureToolForUE)
>
> これらは各実装の機能名・モード名・テクスチャ名です。本書ではQuick SDF Paintの出力内容を「顔影スレッショルドマップ」と呼びます。

## 最初に覚える5操作

1. Object Modeで顔のMeshを選択する
2. 3D Viewで `N` → `Quick SDF` → `Create & Edit`
3. 画面下のタイムラインで角度キーをクリックする
4. `Light` または `Shadow` を選び、3D Viewか2D Canvasへ塗る
5. `Export Threshold Map`を押す

これだけで基本作業は完了します。

## インストール

正式対応はBlender 5.1、Windows x64です。

1. [GitHub Releases](https://github.com/yeczrtu/QuickSDFBlender/releases/latest)から `quick_sdf_blender-0.7.1-windows-x64.zip` を取得します。
2. Blenderの `編集` → `プリファレンス` → `エクステンションを入手` を開きます。
3. 右上のメニューから `ディスクからインストール` を選びます。
4. 取得したZIPを展開せずに選択します。
5. 3D Viewで `N` を押し、右サイドバーの `Quick SDF` タブを開きます。

## 1. 顔を選んで作成する

対象には次のものが必要です。

- ローカルのMesh Object
- 0–1範囲内のUV Map
- 顔に使うMaterial Slot

顔Meshを選択し、使用するMaterial Slotをアクティブにして `Create & Edit` を押します。通常は設定変更不要です。

この1操作で、Quick SDFは次を自動実行します。

- 現在のポーズ、Armature、Shape Key、Modifierを反映
- 評価済み法線から0～90°を均等に分けた8枚の影ガイドを作成
- 既定1024pxの作業画像を作成
- UV構成からMirror方式を推定し、左右対称をON
- Quick SDF Paintワークスペースを開き、Texture PaintとCanvasを同期
- 元Materialの上に見やすいペイントプレビューを表示
- 最初に直しやすい中央付近のキーを選択

Mirrorの推定に確信がない場合だけ、`Whole Texture`、`Paired Islands`、`Shared UV`の選択肢が表示されます。顔のUV配置に合うものを1つ選んでください。

別の顔Meshへ移る場合は、そのMeshを選択して `Open Quick SDF Paint` または `Create & Edit` を1回押します。現在の編集画面を手動で終了する必要はありません。Canvas、Texture Paint対象、プレビューMaterialは新しい顔へまとめて切り替わります。

## 2. 編集画面の見方

| 場所 | 役割 |
|---|---|
| 左上 | 2D Canvas。UV上で細部を塗る |
| 右上 | 3D View。顔を見ながら直接塗る／結果を見る |
| 下 | Quick SDF Timeline。角度を選択・スクラブする |

上部の主な操作は次の5つだけです。

- `Light`：明るくしたい部分を塗る
- `Shadow`：影にしたい部分を塗る
- `Mirror On`：片側から反対側の出力を自動生成する
- 表示切替：`Paint Overlay`、`Mask`、`Toon Result`
- `Export Threshold Map`：完成したスレッショルドマップを書き出す

初回は「斜め後ろから全体へ広がる影ガイドを作成しました。気になる部分だけ修正してください。」というヒントが表示され、最初の一筆後に消えます。

## 3. タイムラインで角度を選ぶ

下段のサムネイルを1回クリックすると、その角度がすぐに編集対象になります。別のActivate操作はありません。

- 青いプレイヘッドと青枠：現在表示・編集している角度キー
- diamond：手描き修正あり
- dot：Auto Bakeのみ
- 赤バッジ：読み込みデータなどに修正が必要
- 色付き背景：次の一筆を編集する角度キー

タイムライン上段のレールをドラッグすると、0～90°の補間結果を2D／3Dで連続確認できます。既存キーから2°以内でマウスを離すと、そのキーへ吸着します。キーの間で離した場合は、その角度の一時Canvasが用意されます。そのまま実際に塗った時だけ角度キーが自動追加され、スクラブだけではProjectのデータは増えません。角度は1°単位、上限は片側16キーです。

通常は、必要な角度へプレイヘッドを動かしてそのまま塗るだけで十分です。数値で厳密に追加したい場合だけ、`Advanced` → `Angle Keys` → `Add Angle…` を使います。追加キーは現在の角度補間から作られます。

通常は、影ガイドが作られた8キーをクリックして、おかしい部分だけを修正すれば十分です。キーは0.0、12.9、25.7、38.6、51.4、64.3、77.1、90.0°です。45°の真横は中央2キーの間にあるTimeline上の基準点で、0°は光り始め、90°は全体が明部になる制作段階を表します。

## 4. Light／Shadowを塗る

`Light` または `Shadow` を選び、Blender標準と同じ感覚で3D Viewか2D Canvasへ塗ります。Brush Asset、Size、Strength、Pressure、FalloffとユーザーKeymapをそのまま利用します。Quick SDFはBrush Assetを置き換えず、一筆の間だけLight／Shadowの色を使います。

同じ色の場所へ塗った場合は、エラーを出さずそのまま作業を続けます。見た目が変わらないときは反対の `Light`／`Shadow`を試してください。2D Canvasで変更した面が3Dの現在視点から裏側にある場合は、モデルを回転すると確認できます。

一筆は選択中の角度へ即時反映されます。ソフトブラシ、低いStrength、筆圧による8-bitの濃淡はそのまま保たれ、0.5の境界を実際に越えた画素だけが必要な角度側へ自動反映されます。Lightは90°側、Shadowは0°側へつながるため、通常の一筆から角度矛盾を作りにくくなっています。

角度間に矛盾が残っていても、Export時に書き出し用コピーだけを自動調整します。元のCanvasは変更されないため、PropagateやValidateを押す必要はありません。選択中キー、ほかの角度への自動反映、必要なら自動キー作成までをまとめて1回のUndo／Redoとして扱います。

Mirrorは既定でONです。通常は右側の0～90°だけを編集し、反対側の出力は自動生成します。左右を別々に描きたい場合だけ、`Advanced` → `Break Mirror`を使います。

便利な操作：

| 操作 | 内容 |
|---|---|
| `LMB` | 選択中のLight／Shadowを塗る |
| `Ctrl + LMB` | その一筆だけLight／Shadowを反転 |
| `X` | Light／Shadowを切り替える |
| `F` | Blender既定Keymapでブラシサイズを変える |
| `←` / `→` | 前／次の角度キー |
| `Home` | 0°へ戻る |
| `Ctrl + Z` / `Ctrl + Shift + Z` | 一筆をUndo／Redo |

## 5. プレビューする

Quick SDF Paintワークスペースを開くとプレビューは自動で有効になります。開始／停止ボタンはありません。

- `Paint Overlay`：元Materialに、Lightを暖色、Shadowを寒色で重ねる
- `Mask`：不透明な白黒マスクを表示する
- `Toon Result`：トゥーン表示で影の見え方を確認する

Quick SDFを終了すると、元のWorkspace、選択、モード、Canvas、Materialへ戻ります。保存時も一時Materialは安全に復元されます。

PoseやMeshを変更した後に `Base needs update` が表示された場合は、`Rebake Base`を押してください。Auto Bake部分だけがバックグラウンドで更新され、手描き修正は残ります。処理中も画面は応答し、必要なら`Cancel`で元の状態へ戻せます。

3D Viewへ極端に近づいたときは、Quick SDF PaintがClip StartとNormal Falloffを一時調整して近距離ペイントを助けます。それでも投影できない場合は、少し引く、`Numpad5`でOrthographicへ切り替える、または2D Canvasで塗ってください。OccludeとBackfaceのユーザー設定は変更しません。

## 6. 書き出す

`Export Threshold Map`を押します。

1. 初回だけ保存先を選びます。
2. Quick SDFが書き出し用データを作り、必要なら角度のつながりを自動調整します。
3. 顔影スレッショルドマップが16-bit RGBA PNGとして1枚保存されます。

自動調整はExport worker内のコピーだけへ適用されます。ペイント画像、影ガイド、補正範囲、Undo履歴は変更されません。調整があった場合は「角度のつながりを自動調整して書き出しました」と表示されます。通常作業を止めるエラーではありません。

最終生成中もBlenderは操作でき、Quick SDF Paintワークスペースに進捗バーと `Cancel` が表示されます。特に2048px／4096pxで中止したい場合は、ほかの操作を待たず `Cancel` を押してください。

次回からは前回の保存先を使います。同じファイルを上書きする場合だけ確認が表示されます。エンジンへ読み込む際は、sRGBではなくNon-Color／Dataテクスチャとして扱ってください。

Imageの欠損、無効なUV、書き込み権限、ディスク障害などで失敗した場合は、保存先を保持した `Retry Export` が表示されます。原因を直して再試行してください。

通常はチャンネル構成を変更する必要はありません。出力先シェーダーの対応例として、既定の`Packing: lilToon`はlilToon／liltoonUE向けに次のチャンネル構成を使用します。

| Channel | 黒（0） | 白（65535） |
|---|---|---|
| R | 右光スレッショルドの最小値 | 右光スレッショルドの最大値 |
| G | 左光スレッショルドの最小値 | 左光スレッショルドの最大値 |
| B | 角度別の顔影制御を使用 | 通常法線シェーディングへ戻す |
| A | 顔影を無効化 | 顔影を通常強度で使用 |

出力形式は16-bit RGBA PNGのみです。エンジンではNon-Color／Dataテクスチャとして扱ってください。

## Advancedを使う場面

`Advanced`は、3D Viewの `N` → `Quick SDF` パネル下部に折り畳まれています。通常作業では開く必要はありません。

ここには次の設定があります。

- 作成前：解像度、初期化方法、既存Maskの指定
- 作成後：Object、Material Slot、UV Map、Forward／Up軸
- Mirror方式と編集側、`Break Mirror`
- `Adjust Shadow Guide`：`Shadow Amount`を0～100で調整し、手描き部分を保持して更新
- `Use This View as Front`：顔の正面方向が合わない場合に現在のビューを使う
- `Rebake Base`
- 非破壊Boundary Tool
- `Review Export Adjustments`：書き出し時だけ調整された画素を読み取り専用で確認
- `Output Packing`：R/G/B/Aへ格納する内容をProject単位で変更
- `Additional Masks`：全角度に共通する領域／強度マスクを編集・読み込み
- Review Maskの書き出し、`Restore Materials`
- Projectの削除

4096pxはメモリ使用量が大きいため、まず1024pxで形を作り、必要な場合だけ上げてください。

### Output Packingを変更する

通常はlilToon設定のまま使用します。別のシェーダーが異なるチャンネル構成を要求する場合だけ、`Advanced` → `Output Packing`を開きます。

1. `Customize`を押します。
2. R/G/B/Aの各行でSourceを選びます。
3. 必要な行だけ`Direct`／`Invert`を切り替えます。
4. `Preview`でRGB合成または各チャンネルのグレースケールを確認します。
5. 通常どおり`Export Threshold Map`を押します。

選べるSourceは次の6種類です。

| Source | 内容 |
|---|---|
| `Right Threshold` | 右側からの光に対する顔影スレッショルド |
| `Left Threshold` | 左側からの光に対する顔影スレッショルド |
| `SDF Area` | 角度別の顔影制御を使用する領域を示す角度非依存マスク |
| `Shadow Strength` | 顔影の強度を指定する角度非依存マスク |
| `Custom Mask` | Projectへ追加した任意の角度非依存マスク |
| `Constant` | 0～1の固定値 |

`Direct`はSourceをそのまま格納し、`Invert`は白黒を反転します。Custom Maskを使う場合は、`Additional Masks`で対象を選び、パッキング行の`Use Selected Mask`を押します。`Reset to lilToon`で既定構成へ戻せます。設定は`Custom — This Project`として`.blend`内のProjectにだけ保存されます。

Levels、Multiply、Min／Maxなどの演算、ノードグラフ、複数Texture出力には対応しません。

### Additional Masksを編集する

Project作成時に、次の2枚が自動で用意されます。

- `SDF Area`：対象Material SlotのUV領域が白、領域外が黒。白い場所で角度別の顔影制御を使います。
- `Shadow Strength`：初期状態は全面白。黒く塗った場所では顔影を無効化します。

`Advanced` → `Additional Masks`でマスクを選択し、`Edit Mask`を押すと、同じ3D／2D Texture Paint環境で編集できます。この間は角度Timelineを操作できず、「このマスクは全角度共通」と表示されます。上部の`White`／`Black`で塗り、`Back to Face Shadow`で角度別の顔影編集へ戻ります。

- `Fill White`／`Fill Black`：選択中マスクを一色で埋める
- `Reset SDF Area from UV`：SDF Areaを対象Material SlotのUV占有領域から作り直す
- `Add Custom Mask`：Project専用の任意マスクを追加する
- `Delete`：Custom Maskを削除する。標準の2枚は削除できない
- `Import from Image`：既存のBlender ImageからR/G/B/A/Luminanceを読み込む

Importは元Imageを変更しません。選んだ成分をProject解像度へリサンプルし、Quick SDF専用の内部グレースケールImageへコピーします。その後は`.blend`内のコピーが編集元になります。

### 既存Projectとの互換性

0.7.1はschema 6を維持し、0.6.x／0.7.0のProjectをそのまま開けます。schema 5以前のProjectを変換する処理はありません。Texture Paint用DisplayはRGBA8 Image、Baseと手描き範囲はCRC付き1-bit bitplaneとして`.blend`内へ保存されます。Native core ABIは7です。2048px以上では、作業中のキーと前後キーを優先して読み込み、その他は`.blend`内のpacked dataから必要時に復元します。

## 困ったとき

### Create & Editが表示されない

Object Modeで顔のMeshをアクティブ選択してください。リンクされたLibrary Objectはローカル化し、0–1 UV Mapを用意します。

### 塗れない

Quick SDF Paintワークスペース内で `Quick SDF Paint` ツールが選ばれていること、3D ViewまたはImage EditorがPaint状態であることを確認してください。

3Dで近距離だけ塗れない場合は、少し引くか `Numpad5` でOrthographic表示を試してください。2D Canvasでは同じ箇所を距離に関係なく編集できます。

2D Canvasには筆跡が出るのに3Dで変化が見えない場合は、まず反対の `Light`／`Shadow`を試してください。現在の面が既に選択色なら変化しません。また、2Dで塗ったUV面が3Dの現在視点から隠れている場合はモデルを回転してください。角度キーの選択または次の一筆開始時に、Quick SDF Paintは3D ViewをMaterial Previewへ自動で戻します。

### 角度エラーで書き出せない

角度のつながりはExport時に非破壊で自動調整するため、角度矛盾だけで書き出しを停止しません。`Advanced` → `Review Export Adjustments`で調整箇所を確認できます。

### 左右の結果が合わない

表示されたMirror候補をUV配置に合わせて選び直します。全体がU反転なら `Whole Texture`、左右の島が分かれているなら `Paired Islands`、左右で同じUVを共有しているなら `Shared UV`です。

### Materialが元に戻らない

`Advanced` → `Review / Recovery` → `Restore Materials`を押してください。

### 保存し直したらStop表示から始まった

編集セッションはファイルへ保存しません。再読み込み後は必ず `Open Quick SDF Paint`から始まります。
