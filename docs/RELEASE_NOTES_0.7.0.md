# Quick SDF Studio 0.7.0

0.7.0は、アーティスト向けの操作とRGBA16出力を変えず、2048pxの実制作を軽くする性能更新です。

## 主な変更

- Timelineは96×64px Thumbnailだけを使用し、フル解像度TextureをGPUへ追加しません。
- Seek、Onion、Threshold Preview、Export調整表示を最大512pxの派生データへ統一しました。
- 2048px以上ではActiveキーと前後キーを優先してCPUへ常駐させ、その他のDisplayはpacked dataから必要時に復元します。
- Base／Coverage、Preview、History、Display常駐に明示的なメモリ上限を追加しました。
- ペイント履歴はgray8とbitplaneの差分だけを保存し、Smart Paintの伝播先を1キーずつ処理します。
- 明示的な`Rebake Base`をNative workerで実行し、キーを1枚ずつ公開するようにしました。Cancel、失敗、revision競合時は処理前の状態へ戻ります。
- Boundary再生成時は、解放済みDisplayをcold reloadしてgray8経路で更新します。
- Exportは最大16キーを画素ごとのbit fieldへまとめ、修復、Exact EDT、RGBA packing、PNG圧縮を低メモリで実行します。
- PNGは保存先と同じフォルダーの一時ファイルへworkerが書き込み、revision確認後にatomicに公開します。
- Native core ABIを7へ更新しました。

## 互換性

- Project schemaは6のままです。0.6.0／0.6.1のProjectを変更せず開けます。
- Operator ID、Packing Recipe、lilToon向けチャンネル構成は変更していません。
- PNGコンテナの圧縮方法は変わりますが、デコード後のRGBA16全画素は同じ入力に対して0.6.1と完全一致します。
- Python fallbackは正確性を維持します。性能目標は同梱Windows x64 Native coreを使用した場合が対象です。

## 検証結果

Blender 5.1、2048px、片側16キー、Mirrorのリリース計測では、Quick SDFの追加定常メモリ301.7 MiB、Export 0.95秒でした。19段階のリリースゲートでTexture Paint、Save／Reload、Undo／Redo、Cancel、Extension validation、隔離インストールを検証しています。

## 対象環境

- Blender 5.1
- Windows x64
- 16GB RAM
- 1024px／2048pxを主対象とし、4096pxはcold reload、進捗、Cancelを伴う形でサポート
