# Quick SDF Paint 0.7.1

0.7.1は、製品名と用語を実際の用途に合わせて整理するリリースです。0.7.0までの編集機能、Projectデータ、出力形式は変更していません。

## 製品名と目的

- 製品名を`Quick SDF Studio`から`Quick SDF Paint`へ変更しました。
- 本製品の目的を「トゥーンレンダリング用の顔影スレッショルドマップ作成」と明記しました。
- SDFは角度別の白黒マスク境界を補間する内部処理であり、最終出力はSDFそのものではなく16-bit RGBAスレッショルドマップであることを明記しました。
- 公開実装で使われる`SDF Face Shadow`、`Shadow SDF mode`、`Face SDF`、`FaceSDF textures`、`SDF_FaceShadow`、`sdf shadow mask`、`SDF-based face shadow map`と、生成物に使われる`Shadow Threshold Map`、`Face Threshold Map`、`Face Shadow Map`を出典どおりの表記で整理しました。Quick SDF Paintの出力は「顔影スレッショルドマップ」と呼びます。

## 表示の変更

- Extension、ワークスペース、パネル、ツールチップ、ガイドの公開製品名を`Quick SDF Paint`へ統一しました。
- 通常の書き出し操作を`Export Threshold Map`へ変更しました。
- Nパネルの`Quick SDF`タブ名は変更していません。
- 既存の`Quick SDF Studio`ワークスペースは、重複を作らず`Quick SDF Paint`へ改名して再利用します。
- lilToon／liltoonUEは製品全体の定義ではなく、出力先シェーダーの対応例として案内します。

## 互換性

- Project schemaは6、Native core ABIは7のままです。
- 0.6.x／0.7.0のProjectを変更せず開けます。
- `quicksdf.*` Operator ID、`QSDF_*` RNA ID、保存プロパティ、Pythonパッケージ名、Extension IDは変更していません。
- 既定のlilToon向けRGBA16パッキングと、同じ入力に対する出力画素値は0.7.0と同一です。
- 過去のリリースノートでは、当時の製品名をそのまま記載しています。

## 対象環境

- Blender 5.1
- Windows x64
