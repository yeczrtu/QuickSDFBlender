# Quick SDF Studio 0.2.0 クイックガイド

Quick SDF Studioは、アーティストが顔を見ながら `Light` と `Shadow` を塗るための専用環境です。角度間の伝播、左右反転、整合性チェック、最終生成は自動で行われます。

## 最初に覚える5操作

1. Object Modeで顔のMeshを選択する
2. 3D Viewで `N` → `Quick SDF` → `Create & Edit`
3. 画面下のタイムラインで角度キーをクリックする
4. `Light` または `Shadow` を選び、3D Viewか2D Canvasへ塗る
5. `Export Face Shadow Texture`を押す

これだけで基本作業は完了します。

## インストール

正式対応はBlender 5.1、Windows x64です。

1. Blenderの `編集` → `プリファレンス` → `エクステンションを入手` を開きます。
2. 右上のメニューから `ディスクからインストール` を選びます。
3. `quick_sdf_blender.zip` を選択します。ZIPは展開しません。
4. 3D Viewで `N` を押し、右サイドバーの `Quick SDF` タブを開きます。

## 1. 顔を選んで作成する

対象には次のものが必要です。

- ローカルのMesh Object
- 0–1範囲内のUV Map
- 顔に使うMaterial Slot

顔Meshを選択し、使用するMaterial Slotをアクティブにして `Create & Edit` を押します。通常は設定変更不要です。

この1操作で、Quick SDFは次を自動実行します。

- 現在のポーズ、Armature、Shape Key、Modifierを反映
- 評価済み法線から0、15、30、45、60、75、90°をAuto Bake
- 既定1024pxの作業画像を作成
- UV構成からMirror方式を推定し、左右対称をON
- Quick SDF Studioを開き、Texture PaintとCanvasを同期
- 元Materialの上に見やすいペイントプレビューを表示

Mirrorの推定に確信がない場合だけ、`Whole Texture`、`Paired Islands`、`Shared UV`の選択肢が表示されます。顔のUV配置に合うものを1つ選んでください。

## 2. Studioの見方

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
- `Export Face Shadow Texture`：完成画像を書き出す

初回は「角度を選ぶ → Light/Shadowを選ぶ → 塗る」というヒントが表示され、最初の一筆後に消えます。

## 3. タイムラインで角度を選ぶ

下段のサムネイルを1回クリックすると、その角度がすぐに編集対象になります。別のActivate操作はありません。

- 青枠：現在編集中のキー
- diamond：手描き修正あり
- dot：Auto Bakeのみ
- 赤バッジ：読み込みデータなどに修正が必要
- 色付き背景：次の一筆が自動反映される角度範囲

タイムライン上段のレールをドラッグすると、0～90°のプレビュー角度を動かせます。レールのスクラブは編集中のキーやPaint Canvasを変えません。

通常は、Auto Bakeで作られた7キーをクリックして修正するだけで十分です。

## 4. Light／Shadowを塗る

`Light` または `Shadow` を選び、Blender標準と同じ感覚で3D Viewか2D Canvasへ塗ります。ペンタブレット、筆圧、Stroke補間、`F`によるブラシサイズ変更をそのまま利用できます。

Smart Paintは一筆ごとに角度関係を自動で保ちます。

- `Light`を塗る：現在角度から90°側へ自動反映
- `Shadow`を塗る：現在角度から0°側へ自動反映

そのため、角度ごとに同じ場所を描き直したり、塗った後にPropagateやValidateを押したりする必要はありません。一筆の全角度変更は1回のUndo／Redoとして扱われます。

Mirrorは既定でONです。通常は右側の0～90°だけを編集し、反対側の出力は自動生成します。左右を別々に描きたい場合だけ、`Advanced` → `Break Mirror`を使います。

便利な操作：

| 操作 | 内容 |
|---|---|
| `LMB` | 選択中のLight／Shadowを塗る |
| `Ctrl + LMB` | その一筆だけLight／Shadowを反転 |
| `X` | Light／Shadowを切り替える |
| `F` | ブラシサイズを変える |
| `←` / `→` | 前／次の角度キー |
| `Home` | 0°へ戻る |
| `Ctrl + Z` / `Ctrl + Shift + Z` | 一筆をUndo／Redo |

## 5. プレビューする

Studioを開くとプレビューは自動で有効になります。開始／停止ボタンはありません。

- `Paint Overlay`：元Materialに、Lightを暖色、Shadowを寒色で重ねる
- `Mask`：不透明な白黒マスクを表示する
- `Toon Result`：トゥーン表示で影の見え方を確認する

Quick SDFを終了すると、元のWorkspace、選択、モード、Canvas、Materialへ戻ります。保存時も一時Materialは安全に復元されます。

PoseやMeshを変更した後に `Base needs update` が表示された場合は、`Rebake Base`を押してください。Auto Bake部分だけが更新され、手描き修正は残ります。

## 6. 書き出す

`Export Face Shadow Texture`を押します。

1. 初回だけ保存先を選びます。
2. Quick SDFが内部で整合性確認と最終生成を行います。
3. 16-bit RGBA PNGが1枚保存されます。

最終生成中もBlenderは操作でき、Studioに進捗バーと `Cancel` が表示されます。特に2048px／4096pxで中止したい場合は、ほかの操作を待たず `Cancel` を押してください。

次回からは前回の保存先を使います。同じファイルを上書きする場合だけ確認が表示されます。エンジンへ読み込む際は、sRGBではなくNon-Color／Dataテクスチャとして扱ってください。

通常はチャンネル構成を意識する必要はありません。技術的にはRが右光、Gが左光、Bが0、Aが65535です。

## Advancedを使う場面

`Advanced`は、3D Viewの `N` → `Quick SDF` パネル下部に折り畳まれています。通常作業では開く必要はありません。

ここには次の設定があります。

- 作成前：解像度、初期化方法、既存Maskの指定
- 作成後：Object、Material Slot、UV Map、Forward／Up軸
- Mirror方式と編集側、`Break Mirror`
- `Rebake Base`
- 非破壊Boundary Tool
- Review Maskの書き出し、`Restore Materials`
- Projectの削除

4096pxはメモリ使用量が大きいため、まず1024pxで形を作り、必要な場合だけ上げてください。

## 困ったとき

### Create & Editが表示されない

Object Modeで顔のMeshをアクティブ選択してください。リンクされたLibrary Objectはローカル化し、0–1 UV Mapを用意します。

### 塗れない

Quick SDF Studio内で `Quick SDF Paint` ツールが選ばれていること、3D ViewまたはImage EditorがPaint状態であることを確認してください。

### 左右の結果が合わない

表示されたMirror候補をUV配置に合わせて選び直します。全体がU反転なら `Whole Texture`、左右の島が分かれているなら `Paired Islands`、左右で同じUVを共有しているなら `Shared UV`です。

### Materialが元に戻らない

`Advanced` → `Review / Recovery` → `Restore Materials`を押してください。

### 保存し直したらStop表示から始まった

0.2.0ではStudio Sessionをファイルへ保存しません。再読み込み後は必ず `Open Quick SDF Studio`から始まります。古いバージョンのUIが表示される場合はExtensionを更新し、Blenderを再起動してください。
