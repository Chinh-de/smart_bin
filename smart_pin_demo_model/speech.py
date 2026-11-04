"""Đọc tiếng Việt bằng edge-tts (giọng Microsoft neural), có cache.
Lần đầu cần Internet để tạo file mp3; các câu đã đọc được lưu trong audio_cache/ nên lần sau chạy offline.
Phát mp3 không cần thư viện thêm: Windows dùng MCI (winmm), macOS dùng afplay, Linux dùng ffplay/mpg123."""
import asyncio
import hashlib
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _play(path):
    if sys.platform == "win32":
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = "smartbin_tts"
        mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
        mci(f"play {alias} wait", None, 0, None)
        mci(f"close {alias}", None, 0, None)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", str(path)], check=False)
    elif shutil.which("ffplay"):
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)], check=False)
    elif shutil.which("mpg123"):
        subprocess.run(["mpg123", "-q", str(path)], check=False)
    else:
        log.warning("Không có trình phát mp3 (cài ffplay hoặc mpg123)")


class Speaker:
    def __init__(self, cache_dir, voice="vi-VN-HoaiMyNeural", rate="+0%", enabled=True):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.voice, self.rate, self.enabled = voice, rate, enabled
        self._q = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="speaker").start()

    def _path(self, text):
        key = hashlib.sha1(f"{self.voice}|{self.rate}|{text}".encode()).hexdigest()[:16]
        return self.cache / f"{key}.mp3"

    def _synth(self, text):
        path = self._path(text)
        with self._lock:                                  # tránh 2 luồng cùng tạo 1 file
            if not path.exists():
                import edge_tts
                tmp = path.with_suffix(".tmp")
                for attempt in range(4):                  # dịch vụ thỉnh thoảng trả NoAudioReceived -> thử lại
                    try:
                        asyncio.run(edge_tts.Communicate(text, self.voice, rate=self.rate).save(str(tmp)))
                        break
                    except edge_tts.exceptions.NoAudioReceived:
                        if attempt == 3:
                            raise
                        time.sleep(2 ** attempt)
                tmp.replace(path)
        return path

    def prepare(self, texts):
        """Tạo sẵn mp3 cho các câu sẽ dùng (chạy nền) để lúc phân loại đọc ngay, không chờ mạng."""
        def work():
            missing = [t for t in texts if not self._path(t).exists()]
            for t in missing:
                try:
                    self._synth(t)
                except Exception as e:
                    log.warning("Không tạo được giọng đọc '%s' (cần Internet lần đầu): %s", t, e)
            if missing:
                log.info("Giọng đọc sẵn sàng (%d câu mới)", len(missing))
        threading.Thread(target=work, daemon=True, name="tts-prepare").start()

    def say(self, text, wait=False):
        if not self.enabled:
            return
        # chỉ giữ câu mới nhất: phân loại liên tục thì không đọc dồn các câu cũ
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        done = threading.Event()
        self._q.put((text, done))
        if wait:
            done.wait(timeout=30)

    def _loop(self):
        while True:
            text, done = self._q.get()
            try:
                _play(self._synth(text))
            except Exception as e:
                log.warning("Lỗi đọc '%s': %s", text, e)
            finally:
                done.set()
