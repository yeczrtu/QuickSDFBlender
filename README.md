# Quick SDF Studio 0.2.0

Blender 5.1上で、トゥーン調の顔影テクスチャを直感的に作るWindows向けExtensionです。

角度ごとの画像管理や整合性チェックを意識する必要はありません。アーティストは顔を見ながら `Light` / `Shadow` を塗るだけです。

## 5つの操作

1. 顔のMeshを選択する
2. `N` → `Quick SDF` → `Create & Edit`
3. 下のタイムラインで角度を選ぶ
4. `Light` または `Shadow` を選んで、3Dか2Dへ塗る
5. `Export Face Shadow Texture`

`Create & Edit` は、現在のポーズから7角度（0～90°）を自動ベイクし、専用のQuick SDF Studioを開きます。左右対称は既定でONです。片側だけ直せば反対側の出力も自動生成されます。

塗った一筆はSmart Paintによって必要な角度へ自動反映されます。角度伝播、整合性確認、反対側生成の追加操作は不要です。

詳しい使い方は[日本語ユーザーガイド](docs/USER_GUIDE_JA.md)を参照してください。

## インストール

対応環境はBlender 5.1、Windows x64です。

1. Blenderで `編集` → `プリファレンス` → `エクステンションを入手` を開く
2. 右上メニューから `ディスクからインストール` を選ぶ
3. `quick_sdf_blender.zip` を選択する（展開不要）
4. 3D Viewで `N` を押し、`Quick SDF` タブを開く

対象Meshにはローカルの0–1 UV Mapが必要です。初期解像度は1024です。

## Studioの画面

| 場所 | 用途 |
|---|---|
| 左上 | 2DのUVペイント |
| 右上 | 顔を見ながら3Dペイント／プレビュー |
| 下 | 編集キーとプレビュー角度のタイムライン |

通常作業で使うのは `Light`、`Shadow`、`Mirror On`、表示切替、`Export`だけです。解像度、UV、軸、Mirror方式、Boundaryなどは、3D Viewの `Quick SDF` → `Advanced` にまとめています。

## 出力

`Export Face Shadow Texture`を押すと、検証・生成・保存を一度に行い、16-bit RGBA PNGを1枚出力します。初回だけ保存先を選び、同じパスへの上書き時は確認が表示されます。エンジンではNon-Color／Dataテクスチャとして扱ってください。

最終生成はバックグラウンドで実行されます。高解像度ではStudio内に進捗と `Cancel` が表示されるため、Blenderを固めずに中止できます。

## Development

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe' `
  --background --factory-startup --python tests/blender_smoke.py

& 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe' `
  --command extension build --source-dir quick_sdf_blender
```
