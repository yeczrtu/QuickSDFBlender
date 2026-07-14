# Quick SDF Studio 0.6.0

0.6.0は、既定8キーの分かりやすさを維持しながら、必要な角度だけ後から描き足せる更新です。作成時に枚数を決める必要はありません。

## 自動角度キー

- キー間の角度へスクラブし、そのまま実際に塗ると角度キーを自動追加します。
- スクラブ、同色の一筆、Projection Paintの空振りではキーを追加しません。
- 角度は1°単位、既存キーから2°以内は吸着し、上限は片側16キーです。
- Mirror Onでは編集側だけへ追加し、反対側は従来どおりライブ生成します。
- 最初の一筆とキー作成は1回のUndo／Redoとして扱います。

## 省メモリなProject形式

- DisplayはBlender Texture Paint用のRGBA8 Imageとして維持します。
- BaseとPaint Override CoverageはCRC32付きの1-bit bitplaneへ変更しました。
- Base／Coverage用のRGBA Image datablockは作成しません。
- bitplaneはrawまたはzlib level 1の小さい方を使用し、save/reload時に寸法、role、payload長、CRCを検証します。

## 互換性

- Project schemaは6です。旧schemaの移行処理はありません。
- Extension versionは0.6.0、Native core ABIは6です。
- Output Packing、RGBA16 PNG形式、lilToonのR/G値は0.5.0から変更していません。
