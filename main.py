#!/usr/bin/env python3
"""
ta2music - TubeArchivist to Music
"""

import os
import sys
import logging
import hashlib
import subprocess
import sqlite3
import re
import requests
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/app/logs/ta2music.log')
    ]
)
logger = logging.getLogger(__name__)


class TubeArchivistAPI:
    """TubeArchivist APIクライアント"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Authorization': f'Token {token}'
        })

    def get_ta_video_info(self, ta_video_id: str) -> dict | None:
        """
        指定した動画の情報をTubeArchivistから取得
        /api/video/{ta_video_id}/
        """
        try:
            url = f"{self.base_url}/api/video/{ta_video_id}/"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"動画情報の取得に失敗 (video_id: {ta_video_id}): {e}")
            return None

    def get_ta_playlists(self) -> list[dict]:
        """
        TubeArchivistからプレイリスト一覧を取得
        /api/playlist/
        """
        try:
            url = f"{self.base_url}/api/playlist/"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get('data', [])
        except requests.exceptions.RequestException as e:
            logger.error(f"プレイリスト一覧の取得に失敗: {e}")
            return []

    def get_ta_playlist_videos(self, ta_playlist_id: str) -> list[str]:
        """
        TubeArchivistから指定したプレイリストに含まれる動画IDのリストを取得
        /api/playlist/{ta_playlist_id}/
        """
        try:
            url = f"{self.base_url}/api/playlist/{ta_playlist_id}/"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            video_ids: list[str] = []
            entries = data.get("playlist_entries", [])
            for entry in entries:
                if isinstance(entry, dict) and "youtube_id" in entry:
                    video_ids.append(entry["youtube_id"])
            return video_ids
        except requests.exceptions.RequestException as e:
            logger.error(
                f"プレイリスト動画の取得に失敗 (playlist_id: {ta_playlist_id}): {e}")
            return []

    def is_in_music_playlist(self, video_id: str) -> bool:
        """
        動画が「MUSIC」で始まるプレイリストに含まれているかチェック（例: MUSIC2025, MUSIC_ROCK）
        MP3データのダウンロード前にチェックで使用
        """
        try:
            ta_playlists = self.get_ta_playlists()
            for playlist in ta_playlists:
                playlist_name = playlist.get('playlist_name', '')
                # プレイリスト名が「MUSIC」で始まるもののみ対象（例: MUSIC2025, MUSIC_ROCK）
                if playlist_name.upper().startswith('MUSIC'):
                    playlist_id = playlist.get('playlist_id')
                    if playlist_id:
                        ta_music_playlist_video_ids = self.get_ta_playlist_videos(
                            playlist_id)
                        if video_id in ta_music_playlist_video_ids:
                            logger.info(
                                f"動画 {video_id} は「{playlist_name}」プレイリストに含まれています")
                            return True
            return False
        except Exception as e:
            logger.error(f"MUSICプレイリストの確認に失敗 (video_id: {video_id}): {e}")
            return False


class MusicDownloader:
    """TubeArchivistでダウンロードされた動画を検知し、同じ動画をyt-dlpでMP3形式でダウンロードしてNavidromeで使用可能にするクラス"""

    def __init__(self, ta_dir: str, navidrome_dir: str, db_file: str, ta_api: TubeArchivistAPI | None = None):
        self.ta_dir = Path(ta_dir)
        self.navidrome_dir = Path(navidrome_dir)
        self.db_file = Path(db_file)
        self.ta_api = ta_api

        if not self.navidrome_dir.exists():
            logger.error(f"Navidromeの音楽ディレクトリが存在しません: {self.navidrome_dir}")
            logger.error("KubernetesのDeploymentでPVCが正しくマウントされているかを確認してください。")
            raise FileNotFoundError(
                f"Navidrome music directory not found: {self.navidrome_dir}")

        # /app/data/を作成
        self.db_file.parent.mkdir(parents=True, exist_ok=True)

        self._init_database()

        mp3_downloaded_count = self._get_mp3_downloaded_count()

        logger.info(f"入力ディレクトリ: {self.ta_dir}")
        logger.info(f"出力ディレクトリ: {self.navidrome_dir}")
        logger.info(f"MP3ダウンロード済みファイル数: {mp3_downloaded_count}")
        if self.ta_api:
            logger.info("TubeArchivist APIが有効です")
        else:
            logger.warning("TubeArchivist APIが無効です。MUSICプレイリスト判定はスキップされます")

    def _init_database(self):
        """データベースを初期化する"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            # MP3ダウンロード済み動画のハッシュを保存するテーブルを作成
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mp3_downloaded_videos (
                    file_hash TEXT PRIMARY KEY,
                    mp3_downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # インデックスを作成して検索を高速化
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_file_hash 
                ON mp3_downloaded_videos(file_hash)
            ''')
            conn.commit()
            conn.close()
            logger.info(f"データベースを初期化しました: {self.db_file}")
        except Exception as e:
            logger.error(f"データベースの初期化に失敗: {e}")
            raise

    def _get_mp3_downloaded_count(self) -> int:
        """MP3ダウンロード済みファイル数を取得"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM mp3_downloaded_videos')
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            logger.error(f"MP3ダウンロード済みファイル数の取得に失敗: {e}")
            return 0

    def is_mp3_downloaded(self, file_hash: str) -> bool:
        """ファイルがMP3ダウンロード済みかどうかを確認"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                'SELECT 1 FROM mp3_downloaded_videos WHERE file_hash = ?', (file_hash,))
            result = cursor.fetchone() is not None
            conn.close()
            return result
        except Exception as e:
            logger.error(f"MP3ダウンロード済み確認に失敗: {e}")
            return False

    def mark_as_mp3_downloaded(self, file_hash: str):
        """ファイルをMP3ダウンロード済みとしてマーク"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                # 既に同じ file_hash が存在する場合、エラーにせず無視する。(重複挿入を防ぐため)
                'INSERT OR IGNORE INTO mp3_downloaded_videos (file_hash) VALUES (?)',
                (file_hash,)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"MP3ダウンロード済みマークの保存に失敗: {e}")

    def _get_file_hash(self, file_path: Path) -> str:
        """MP4ファイルのハッシュ値を計算"""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.error(f"ハッシュ計算に失敗 ({file_path}): {e}")
            return ""

    def _is_video_file(self, file_path: Path) -> bool:
        """動画ファイルかどうかを判定"""
        video_extensions = {'.mp4', '.mkv',
                            '.webm', '.avi', '.mov', '.flv', '.m4v'}
        return file_path.suffix.lower() in video_extensions

    def _extract_video_id(self, video_path: Path) -> str | None:
        """ファイルパスから動画IDを抽出
        パス形式: /exports/k8s/tubearchivist-media-pvc/チャンネルID/動画ID.mp4
        動画IDはMP4ファイル名（拡張子を除く）から取得
        """
        try:
            video_id = video_path.stem
            if video_id:
                logger.debug(f"ファイル名から動画IDを取得: {video_id}")
                return video_id

            return None
        except Exception as e:
            logger.error(f"動画IDの抽出に失敗 ({video_path}): {e}")
            return None

    def _sanitize_filename(self, filename: str) -> str:
        """ファイル名をサニタイズ（ファイルシステムで使用できない文字を削除）"""
        # Windows/Linux/Macで使用できない文字を削除
        invalid_chars = r'[<>:"/\\|?*\x00-\x1f]'
        sanitized = re.sub(invalid_chars, '_', filename)
        # 先頭・末尾の空白やドットを削除
        sanitized = sanitized.strip(' .')
        # 長すぎるファイル名を切り詰め（255文字制限）
        if len(sanitized) > 200:
            sanitized = sanitized[:200]
        return sanitized if sanitized else 'untitled'

    def _download_mp3_with_thumbnail(self, youtube_id: str, video_title: str | None = None) -> Path | None:
        """yt-dlpでYouTubeからMP3+サムネイルをダウンロード"""
        youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"

        # 出力ファイル名を生成
        if video_title:
            # 動画タイトルを使用
            sanitized_title = self._sanitize_filename(video_title)
            output_template = str(self.navidrome_dir /
                                  f"{sanitized_title}.%(ext)s")
        else:
            # フォールバック: 動画IDを使用
            output_template = str(self.navidrome_dir / f"{youtube_id}.%(ext)s")

        # 既にMP3ファイルが存在する場合はスキップ
        expected_mp3_path = Path(output_template.replace('%(ext)s', 'mp3'))
        if expected_mp3_path.exists():
            logger.info(f"MP3ファイルが既に存在します: {expected_mp3_path}")
            return expected_mp3_path

        logger.info(f"ダウンロード開始: {youtube_url} -> {expected_mp3_path}")

        # yt-dlpでMP3+サムネイルをダウンロード
        # -x: 音声のみ抽出
        # --audio-format mp3: MP3形式で出力
        # --audio-quality 0: 最高音質（0=最高品質）
        # --embed-thumbnail: サムネイルを埋め込み
        # --output: 出力ファイル名テンプレート
        cmd = [
            'yt-dlp',
            '-x',                          # 音声のみ抽出
            '--audio-format', 'mp3',       # MP3形式
            '--audio-quality', '0',        # 最高音質（0=最高品質）
            '--embed-thumbnail',           # サムネイルを埋め込み
            '--output', output_template,   # 出力ファイル名
            youtube_url                    # YouTube URL
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=3600
            )
            logger.info(f"ダウンロード完了: {expected_mp3_path}")
            logger.debug(f"yt-dlp出力: {result.stdout}")

            # 実際に生成されたファイルパスを確認
            if expected_mp3_path.exists():
                return expected_mp3_path
            else:
                logger.warning(f"ダウンロードされたファイルが見つかりません: {expected_mp3_path}")
                return None
        except subprocess.TimeoutExpired:
            logger.error(f"ダウンロードタイムアウト: {youtube_url}")
            return None
        except subprocess.CalledProcessError as e:
            logger.error(f"ダウンロード失敗: {youtube_url}")
            logger.error(f"エラー出力: {e.stderr}")
            return None
        except Exception as e:
            logger.error(f"ダウンロード中にエラーが発生: {youtube_url}, {e}")
            return None

    def process_video(self, video_path: Path) -> bool:
        """動画を処理（MP3ダウンロード）する"""
        if not video_path.exists():
            logger.warning(f"ファイルが存在しません: {video_path}")
            return False

        if not self._is_video_file(video_path):
            logger.debug(f"動画ファイルではありません: {video_path}")
            return False

        # ファイルのハッシュを計算して、既にMP3ダウンロード済みかチェック
        file_hash = self._get_file_hash(video_path)
        if not file_hash:
            logger.warning(f"ハッシュ計算に失敗: {video_path}")
            return False

        if self.is_mp3_downloaded(file_hash):
            logger.debug(f"既にMP3ダウンロード済み: {video_path}")
            return False

        # TubeArchivist APIを使用する場合
        video_id = None
        video_title = None
        if self.ta_api:
            # ファイルパスから動画IDを抽出
            video_id = self._extract_video_id(video_path)
            if not video_id:
                logger.warning(f"動画IDを抽出できませんでした: {video_path}")
                return False

            # MUSICプレイリストに含まれるかチェック
            if not self.ta_api.is_in_music_playlist(video_id):
                logger.info(f"動画 {video_id} はMUSICプレイリストに含まれていないため、スキップします")
                return False

            # 動画情報を取得してタイトルを取得
            video_info = self.ta_api.get_ta_video_info(video_id)
            if video_info:
                video_title = video_info.get(
                    'title') or video_info.get('video_title')
                if not video_title:
                    logger.warning(f"動画タイトルを取得できませんでした (video_id: {video_id})")
            else:
                logger.warning(f"動画情報を取得できませんでした (video_id: {video_id})")

            # yt-dlpでMP3+サムネイルをダウンロード
            mp3_path = self._download_mp3_with_thumbnail(video_id, video_title)
            if mp3_path and mp3_path.exists():
                # MP3ダウンロード済みとしてマーク
                self.mark_as_mp3_downloaded(file_hash)
                logger.info(f"MP3ダウンロード完了: {video_path} -> {mp3_path}")
                return True
            else:
                logger.error(f"MP3ダウンロード失敗: {video_path}")
                return False
        else:
            logger.warning("TubeArchivist APIが無効のため、処理をスキップします")
            return False


class VideoFileHandler(FileSystemEventHandler):
    """ファイルシステムイベントを処理するハンドラ"""

    def __init__(self, downloader: MusicDownloader):
        self.downloader = downloader
        self.processing = set()  # 処理中のファイルを追跡

    def on_created(self, event: FileSystemEvent):
        """ファイルが作成されたときの処理"""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        self._process_file(file_path)

    def _process_file(self, file_path: Path):
        """ファイルを処理する（重複処理を防ぐ）"""
        # 処理中のファイルはスキップ
        if file_path in self.processing:
            return

        # ファイルが完全に書き込まれるまで少し待つ
        import time
        time.sleep(2)

        # ファイルが存在し、サイズが0でないことを確認
        if not file_path.exists() or file_path.stat().st_size == 0:
            logger.debug(f"ファイルがまだ準備中または空です: {file_path}")
            return

        # 処理中フラグを設定
        self.processing.add(file_path)
        try:
            self.downloader.process_video(file_path)
        finally:
            self.processing.discard(file_path)


def main():
    """メイン関数"""
    # taの動画ディレクトリ
    ta_dir = os.getenv('TUBEARCHIVIST_DIR', '/youtube')
    # navidromeの音楽ディレクトリ
    navidrome_dir = os.getenv('NAVIDROME_DIR', '/music')
    # MP3ダウンロード済みの動画ファイルを記録するデータベースファイルのパス
    db_file = os.getenv('DB_FILE', '/app/data/mp3_downloaded.db')

    # TubeArchivist API設定
    ta_api_url = os.getenv('TA_API_URL')
    ta_token = os.getenv('TA_TOKEN')

    logger.info("ta2music を起動します")
    logger.info(f"taの動画ディレクトリ: {ta_dir}")
    logger.info(f"navidromeの音楽ディレクトリ: {navidrome_dir}")

    # TubeArchivist APIクライアントを初期化
    api = None
    if ta_api_url and ta_token:
        try:
            api = TubeArchivistAPI(ta_api_url, ta_token)
            logger.info(f"TubeArchivist APIに接続: {ta_api_url}")
        except Exception as e:
            logger.error(f"TubeArchivist APIの初期化に失敗: {e}")
            logger.warning("APIなしで動作を続行します（MUSICプレイリスト判定はスキップ）")
    else:
        logger.warning("TubeArchivist APIの設定が不完全です（TA_API_URL, TA_TOKENが必要）")
        logger.warning("MUSICプレイリスト判定はスキップされます")

    # ダウンローダーを初期化
    downloader = MusicDownloader(ta_dir, navidrome_dir, db_file, api)

    # ファイルシステム監視を開始
    event_handler = VideoFileHandler(downloader)
    observer = Observer()
    # TubeArchivistの動画ディレクトリを監視ルートに設定
    observer.schedule(event_handler, str(downloader.ta_dir), recursive=True)
    observer.start()

    logger.info("ファイル監視を開始しました")

    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("シャットダウンを開始します...")
        observer.stop()

    observer.join()
    logger.info("アプリケーションを終了します")


if __name__ == '__main__':
    main()
