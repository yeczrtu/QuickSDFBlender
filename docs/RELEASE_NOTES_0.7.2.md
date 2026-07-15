# Quick SDF Paint 0.7.2

0.7.2は、Blender 5.2 LTSを正式対応へ追加する互換性リリースです。Blender 5.1の対応も継続し、同じWindows x64 Extension ZIPを両バージョンで使用できます。

## Blender対応

- Blender 5.1以上、5.3未満を正式対応範囲としました。
- Blender 5.2 LTSのTexture Paint、WorkSpaceTool、Timeline、GPU Preview、Image常駐管理、評価済みMesh Bakeを検証しました。
- Studio中の通常保存とTexture Paint Autosaveについて、元Material、Canvas、Onion、Display、Base、Coverageが正しく復元・維持されることを確認しました。
- 5.1／5.2の両方で、3D／2Dペイント、自動角度キー、Undo／Redo、Save／Reload、Export、Extension validation、隔離インストールを実行しました。

## 互換性

- Project schemaは6のままです。0.6.x／0.7.xのProjectを変更せず開けます。
- Native core ABIは7のままです。
- Operator ID、Packing Recipe、lilToon向けチャンネル構成、Projectデータ形式は変更していません。
- 同じ入力から生成されるRGBA16の全チャンネル値は0.7.1と同一です。
- Blender 5.3以降は未検証のため、このパッケージのManifestでは対象外です。

## 対象環境

- Blender 5.1／5.2 LTS
- Windows x64
- 単一Object、単一Material Slot、単一0–1 UV
