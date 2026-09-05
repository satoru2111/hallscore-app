# ホールスコア (hallscore.com)

パチスロの出玉データを統計として整理し、「いつ・どの店に行くか」「そこで何を打つか」の
判断材料を出すサイトの**表示部分**です。

- 本番: https://hallscore.com
- データ収集・集計・検証は別リポジトリ (非公開)。ここは表示のみ
- 表示するDB (`serve.db`, 約54MB) は Cloudflare R2 から取得します

## 何をしているか

各ホールが公開している出玉データを集計し、統計的に確かめられたことと
確かめられなかったことを区別して掲載しています。当たることを保証するものではありません。

サイト内の「🔬 検証したこと」に、通った検証も通らなかった検証も載せています。

## 動かし方

```bash
pip install -r requirements.txt
streamlit run app.py
```

R2 の認証情報が無い場合は `data/serve.db` をローカルから読みます。

## Secrets

Streamlit Community Cloud の Secrets に以下を設定します。

```toml
R2_ACCOUNT_ID = "..."
R2_ACCESS_KEY_ID = "..."
R2_SECRET_ACCESS_KEY = "..."
R2_BUCKET = "hallscore"
R2_KEY = "serve.db"
```

## データ出典

- [みんレポ](https://min-repo.com/) — 台ごとの出玉データ
- [みんパチ](https://minpachi.com/) — 店舗情報・旧イベント日
- 設定別の機械割は各メーカーの公表値

## お問い合わせ

info@hallscore.com
