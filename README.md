# Quick SDF Paint 0.7.1

Quick SDF Paintは、Blender 5.1上でトゥーンレンダリング用の顔影スレッショルドマップを作成するWindows向けExtensionです。法線から作られた影ガイドを下描きにして、気になる部分だけを直せます。

角度別の白黒マスクをペイントし、SDF距離補間を使って、光の向きに応じた影の切替値を1枚の16-bit RGBAテクスチャにまとめます。SDFはマスク境界を補間するための内部処理であり、最終出力はSDFそのものではなくスレッショルドマップです。

角度ごとの画像管理や整合性チェックを意識する必要はありません。アーティストは顔を見ながら `Light` / `Shadow` を塗り、スレッショルドマップを書き出すだけです。

## 出力とSDF

Quick SDF Paintが出力するのは「顔影スレッショルドマップ」です。SDFは角度別マスクの境界を補間し、画素ごとの切替値を求める生成手法としてだけ使用します。

統一された名称はありません。公開されているシェーダーや資料では、次の表記が実際に使われています。

- lilToon：[`SDF Face Shadow`、`Shadow SDF mode`](https://github.com/lilxyzw/lilToon/blob/master/Assets/lilToon/CHANGELOG.md)
- PotaToon：[`Face SDF`、`FaceSDF textures`、`Face SDF Tex`](https://potatoon.dev/en/features/material-settings)
- ChiliMilk URP Toon：[`SDF_FaceShadow`、`sdf shadow mask`](https://github.com/ChiliMilk/URP_Toon)
- Anime Shading Plus：[`SDF-based face shadow map`、`Face Shadow Map`](https://erichu33.github.io/ASPDocs/en/articles/face-shadow-map-creation-and-baking-workflow.html)
- entropy622の公開シェーダー実装：[`face SDF shadow`](https://github.com/entropy622/Unity-URP-Shader-For-Starrail-Characters/blob/master/README_EN.md)
- Natane Toon Shader：[`SDF Shadow Map`、`SDF Shadow Texture`](https://github.com/natane010/natane_toon_shader/blob/v1.1.5/Website/en/params/lighting/sdf-shadow.html)
- akasakiの公開ツール：[`Shadow Threshold Map`](https://github.com/akasaki1211/sdf_shadow_threshold_map)
- Hi-Fi RUSHの事例記事：[`Face Threshold Map`](https://cgworld.jp/article/202306-hifirush01.html)

これらは各実装の機能名・モード名・テクスチャ名です。本書ではQuick SDF Paintの出力内容を「顔影スレッショルドマップ」と呼びます。

> [!NOTE]
> **UE5版からの系譜**
>
> Quick SDF Paintは、UE5向けの[QuickSDFTool](https://github.com/yeczrtu/QuickSDFTool)で培った角度別マスクとSDF距離補間の考え方を基に、Blenderとアーティスト向けの操作体験へ再設計した後継プロジェクトです。UE5版とのデータ互換を目的にした移植ではなく、UI、編集フロー、出力形式はBlender用に作り直しています。UE5版の開発は終了しており、既存のソースコードとリリースは参照用として引き続き公開されています。

## 5つの操作

1. 顔のMeshを選択する
2. `N` → `Quick SDF` → `Create & Edit`
3. 下のタイムラインで角度を選ぶ
4. `Light` または `Shadow` を選んで、3Dか2Dへ塗る
5. `Export Threshold Map`

`Create & Edit` は、現在のポーズと評価済み法線から8段階（0～90°）の影ガイドを作り、中央付近のキーを選択した状態で専用のQuick SDF Paintワークスペースを開きます。0°では斜め後ろから光が入り始め、45°で真横、90°で顔全体が明部になります。左右対称は既定でONです。片側だけ直せば反対側の出力も自動生成されます。

キーの間へさらに形を描き込みたい場合は、タイムラインを目的の角度へ動かしてそのまま塗ります。実際に筆跡が付いた時だけ角度キーが自動追加され、スクラブだけではデータは増えません。既存キーから2°以内は吸着し、上限は片側16キーです。

一筆はBlender標準Texture Paintの操作感を保ったまま、選択中の角度へ即時反映されます。書き出し時に角度のつながりを非破壊で自動調整するため、Propagate、Validate、反対側生成の追加操作は不要です。

書き出すRGBAの割り当てはプロジェクトごとに変更できます。通常は設定を触らず、そのまま1ボタンで書き出せます。

schema 6では、Texture Paintに必要なDisplayだけをRGBA8 Imageとして保持し、Baseと手描き範囲は検証付き1-bit bitplaneとして保存します。キーを追加しても補助レイヤーがRGBA Imageとして増えないため、従来より少ないメモリで細かな角度制御ができます。

> [!WARNING]
> 0.7.1もProject schema 6を維持しています。0.6.x以降で作成したProjectをそのまま開けます。schema 5以前を移行する処理はありません。

詳しい使い方は[Web操作ガイド](https://yeczrtu.github.io/QuickSDFBlender/)または[日本語ユーザーガイド](docs/USER_GUIDE_JA.md)を参照してください。

## インストール

対応環境はBlender 5.1、Windows x64です。

1. [GitHub Releases](https://github.com/yeczrtu/QuickSDFBlender/releases/latest)から `quick_sdf_blender-0.7.1-windows-x64.zip` を取得する
2. Blenderで `編集` → `プリファレンス` → `エクステンションを入手` を開く
3. 右上メニューから `ディスクからインストール` を選ぶ
4. 取得したZIPを展開せずに選択する
5. 3D Viewで `N` を押し、`Quick SDF` タブを開く

対象Meshにはローカルの0–1 UV Mapが必要です。初期解像度は1024です。

## Quick SDF Paintの画面

| 場所 | 用途 |
|---|---|
| 左上 | 2DのUVペイント |
| 右上 | 顔を見ながら3Dペイント／プレビュー |
| 下 | 角度キーを選ぶタイムライン |

通常作業で使うのは `Light`、`Shadow`、`Mirror On`、表示切替、`Export`だけです。解像度、UV、軸、Mirror方式、Boundaryなどは、3D Viewの `Quick SDF` → `Advanced` にまとめています。

タイムラインのプレイヘッドは1本だけです。レールをドラッグしている間は2D／3Dで角度間を連続確認できます。既存キーから2°以内で離すとそのキーへ吸着し、それ以外では一時Canvasを準備します。そこで実際に塗ると、その角度が自動キーとして確定します。スクラブや同色の一筆だけではProjectのキーや永続Imageは増えません。

## スレッショルドマップの出力

`Export Threshold Map`を押すと、検証・SDF距離補間・パッキング・保存を一度に行い、顔影スレッショルドマップを16-bit RGBA PNGとして1枚出力します。SDF距離補間は角度別の白黒マスクから滑らかな切替値を求めるために使われます。

出力先シェーダーの対応例として、既定の`Packing: lilToon`はlilToon／liltoonUE向けに次のチャンネル構成を使用します。

| Channel | 既定の内容 |
|---|---|
| R | キャラクター右側からの光に対するスレッショルド |
| G | キャラクター左側からの光に対するスレッショルド |
| B | 角度別の顔影制御を使う領域で0、通常法線シェーディングへ戻す領域で65535 |
| A | 通常の影で65535、影を無効化する領域で0 |

角度のつながりに矛盾があっても、元のペイントを変更せず、書き出し用データだけを自動調整します。初回だけ保存先を選び、同じパスへの上書き時は確認が表示されます。エンジンではNon-Color／Dataテクスチャとして扱ってください。

別のシェーダー向けに並べ替える場合は、`Quick SDF` → `Advanced` → `Output Packing`で`Customize`を押します。R/G/B/Aの各行へRight Threshold、Left Threshold、SDF Area、Shadow Strength、Custom Mask、Constantを割り当て、必要な行だけDirect／Invertを切り替えられます。このレシピはProject内に保存され、ほかのProjectには影響しません。ノードグラフや数式は使いません。

`Additional Masks`では、全角度に共通する`SDF Area`と`Shadow Strength`を3D／2Dでペイントできます。任意のCustom Maskも追加でき、既存のBlender ImageからR/G/B/A/Luminanceのいずれかを読み込めます。マスク編集を終えるときは`Back to Face Shadow`で角度別ペイントへ戻ります。

最終生成と明示的な`Rebake Base`はバックグラウンドで実行されます。高解像度ではQuick SDF Paintワークスペース内に進捗と `Cancel` が表示されるため、Blenderを固めずに中止できます。保存先やディスクの問題で失敗した場合は、パスを保持したまま `Retry Export` できます。

## Development

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_extension.ps1
```

このリリースゲートはunit test、Blender背景／実ウィンドウsmoke、2048pxの性能・メモリ予算、保存再読込、Extension validation、ZIPのbyte検証、隔離ユーザー領域への実インストールを順に実行します。

## License

Quick SDF Paintは[GNU General Public License v3.0 or later](LICENSE)で公開されています。同梱native coreも同じライセンスです。利用しているアルゴリズムとランタイムの帰属情報は[Third-Party Notices](THIRD_PARTY_NOTICES.md)に記載しています。
