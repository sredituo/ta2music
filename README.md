# ta2music

TubeArchivist to Music - TubeArchivistでダウンロードされた動画を検知し、yt-dlpでMP3形式でダウンロードしてNavidromeなどのミュージックサーバで使用可能にする常駐型アプリケーション

## 概要

`ta2music`は、TubeArchivistでダウンロードされた動画を監視し、同サービス内でサブスクライブしているプレイリストの中から名前が「MUSIC」で始まるプレイリストに含まれる動画を自動的にMP3形式でダウンロードしてNavidrome等の音楽ライブラリに追加するアプリケーションです。  

## 機能

- **自動検知**: TubeArchivistでダウンロードされた新しい動画を自動的に検知
- **プレイリストフィルタリング**: TubeArchivist APIを使用して、同サービス内でサブスクライブしているプレイリストの中から名前が「MUSIC」で始まるプレイリストに含まれる動画のみを処理（例：MUSIC2025）
- **高品質MP3ダウンロード**: yt-dlpを使用して最高音質のMP3をダウンロード
- **サムネイル埋め込み**: ダウンロードしたMP3ファイルに動画のサムネイルを自動的に埋め込むため、ミュージックアプリでもYoutubeの動画と同じサムネイルで視認することができます
- **重複防止**: SQLiteデータベースを使用して、既にMP3ダウンロード済みの動画をスキップ
- **ファイル名の自動設定**: 動画タイトルを取得して、わかりやすいファイル名で保存（例: `動画タイトル.mp3`）

## 必要な環境

- Python 3.11以上
- ffmpeg
- yt-dlp
- TubeArchivist APIへのアクセス（MUSICプレイリスト判定に必要）



## セットアップ

### docker

#### ビルド
```bash
docker build -t ta2music:latest .
```

#### 実行
```bash
docker run -d \
  --name ta2music \
  -v /path/to/tubearchivist/videos:/youtube:ro \
  -v /path/to/navidrome/music:/music \
  -v /path/to/data:/app/data \
  -v /path/to/logs:/app/logs \
  ta2music:latest
```

**環境変数**:
- `TA_API_URL`: TubeArchivist APIのベースURL（例: `http://tubearchivist.internal`）
- `TA_TOKEN`: TubeArchivist APIの認証トークン
- `TUBEARCHIVIST_DIR`: TubeArchivistの動画ディレクトリのパス（デフォルト: `/youtube`）
- `NAVIDROME_DIR`: Navidromeの音楽ディレクトリのパス（デフォルト: `/music`）
- `DB_FILE`: MP3ダウンロード済み動画を記録するSQLiteデータベースファイルのパス（デフォルト: `/app/data/mp3_downloaded.db`）

### Kubernetes

## 動作の仕組み

1. **ファイル監視**: `watchdog`を使用して、TubeArchivistの動画ディレクトリを監視します
2. **動画検知**: 新しいMP4ファイルが追加されると、イベントハンドラが検知します
3. **動画ID抽出**: ファイルパスから動画IDを抽出します（パス形式: `/チャンネルID/動画ID.mp4`）
4. **プレイリスト判定**: TubeArchivist APIを使用して、動画が「MUSIC」で始まるプレイリストに含まれるか確認します
5. **重複チェック**: SQLiteデータベースで、既にMP3ダウンロード済みか確認します
6. **MP3ダウンロード**: yt-dlpを使用して、最高音質のMP3をダウンロードし、サムネイルを埋め込みます
7. **ファイル名設定**: 動画タイトルを取得して、適切なファイル名でNavidromeディレクトリに保存します
8. **記録**: 処理完了後、データベースに記録して重複処理を防止します

## ログ

ログは以下の場所に出力されます：

- 標準出力（stdout）
- `/app/logs/ta2music.log`

## トラブルシューティング

### TubeArchivist APIに接続できない

**解決方法**: 
- `TA_API_URL`と`TA_TOKEN`が正しく設定されているか確認してください
- TubeArchivistのAPIが起動しているか確認してください
- ネットワーク接続（Service名やDNS解決）を確認してください

### MP3ダウンロードが失敗する

**解決方法**:
- yt-dlpが最新バージョンであることを確認してください
- インターネット接続を確認してください
- ログファイルで詳細なエラーメッセージを確認してください