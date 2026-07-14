# Quick SDF Studio 0.3.3

Blender 5.1上で、トゥーン調の顔影テクスチャを直感的に作るWindows向けExtensionです。法線から作られた影ガイドを下描きにして、気になる部分だけを直せます。

角度ごとの画像管理や整合性チェックを意識する必要はありません。アーティストは顔を見ながら `Light` / `Shadow` を塗るだけです。

> [!NOTE]
> **UE5版からの系譜**
>
> Quick SDF Studioは、UE5向けの[QuickSDFTool](https://github.com/yeczrtu/QuickSDFTool)で培った角度別マスクとSDF距離補間の考え方を基に、Blenderとアーティスト向けの操作体験へ再設計した後継プロジェクトです。UE5版とのデータ互換を目的にした移植ではなく、UI、編集フロー、出力形式はBlender用に作り直しています。UE5版の開発は終了しており、既存のソースコードとリリースは参照用として引き続き公開されています。

## 5つの操作

1. 顔のMeshを選択する
2. `N` → `Quick SDF` → `Create & Edit`
3. 下のタイムラインで角度を選ぶ
4. `Light` または `Shadow` を選んで、3Dか2Dへ塗る
5. `Export Face Shadow Texture`

`Create & Edit` は、現在のポーズと評価済み法線から7角度（0～90°）の影ガイドを作り、45°を選択した状態で専用のQuick SDF Studioを開きます。左右対称は既定でONです。片側だけ直せば反対側の出力も自動生成されます。

一筆はBlender標準Texture Paintの操作感を保ったまま、選択中の角度へ即時反映されます。書き出し時に角度のつながりを非破壊で自動調整するため、Propagate、Validate、反対側生成の追加操作は不要です。

0.3.3では、角度タイムラインを1本のプレイヘッドへ統合しました。ドラッグ中だけ2D／3Dで補間結果を確認し、離すと最も近い既存キーへ吸着します。通常のペイントからキーを自動追加せず、ペイント開始時に別角度へ戻る表示切替もありません。

0.3.2では、3Dプレビューが黒くなるMaterial接続、ペンを離した後の待ち時間、Light／ShadowとBrush Assetの不一致、誤解を招く無変更エラーを修正しました。1024pxでの連続3Dペイント、タイムライン上のUndo、Studio終了時の復元をBlender 5.1実機テストへ追加しています。

詳しい使い方は[日本語ユーザーガイド](docs/USER_GUIDE_JA.md)を参照してください。

## インストール

対応環境はBlender 5.1、Windows x64です。

1. [GitHub Releases](https://github.com/yeczrtu/QuickSDFBlender/releases/latest)から `quick_sdf_blender-0.3.3-windows-x64.zip` を取得する
2. Blenderで `編集` → `プリファレンス` → `エクステンションを入手` を開く
3. 右上メニューから `ディスクからインストール` を選ぶ
4. 取得したZIPを展開せずに選択する
5. 3D Viewで `N` を押し、`Quick SDF` タブを開く

対象Meshにはローカルの0–1 UV Mapが必要です。初期解像度は1024です。

## Studioの画面

| 場所 | 用途 |
|---|---|
| 左上 | 2DのUVペイント |
| 右上 | 顔を見ながら3Dペイント／プレビュー |
| 下 | 角度キーを選ぶタイムライン |

通常作業で使うのは `Light`、`Shadow`、`Mirror On`、表示切替、`Export`だけです。解像度、UV、軸、Mirror方式、Boundaryなどは、3D Viewの `Quick SDF` → `Advanced` にまとめています。

タイムラインのプレイヘッドは1本だけです。レールをドラッグしている間は2D／3Dで角度間を連続確認でき、離すと最も近い既存キーへ吸着します。その時点でプレイヘッド、2D Canvas、3D表示、ペイント対象が同じ角度へ揃うため、塗り始めても表示は切り替わりません。キーを増やしたい場合だけ `Advanced` の `Add Angle…` を使います。

## 出力

`Export Face Shadow Texture`を押すと、検証・生成・保存を一度に行い、16-bit RGBA PNGを1枚出力します。角度のつながりに矛盾があっても、元のペイントを変更せず、書き出し用データだけを自動調整します。初回だけ保存先を選び、同じパスへの上書き時は確認が表示されます。エンジンではNon-Color／Dataテクスチャとして扱ってください。

最終生成はバックグラウンドで実行されます。高解像度ではStudio内に進捗と `Cancel` が表示されるため、Blenderを固めずに中止できます。保存先やディスクの問題で失敗した場合は、パスを保持したまま `Retry Export` できます。

## Development

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_extension.ps1
```

このリリースゲートはunit test、Blender背景／実ウィンドウsmoke、保存再読込、Extension validation、ZIPのbyte検証、隔離ユーザー領域への実インストールを順に実行します。

## License

Quick SDF Studioは[GNU General Public License v3.0 or later](LICENSE)で公開されています。同梱native coreも同じライセンスです。利用しているアルゴリズムとランタイムの帰属情報は[Third-Party Notices](THIRD_PARTY_NOTICES.md)に記載しています。
