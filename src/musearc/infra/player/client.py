"""MusePlayer TCP JSON Lines 控制客户端。"""

from __future__ import annotations

import json
import socket
from typing import Any


class PlayerClientError(Exception):
    """播放器通信错误。"""


class PlayerClient:
    """与 MusePlayer 的 TCP JSON Lines 控制接口通信。

    协议：每行一个 JSON 对象，以 ``\\n`` 分隔，UTF-8 编码。
    响应格式：``{"ok": true, "result": ...}`` 或 ``{"ok": false, "error": "..."}``。
    """

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 43121
    RECV_BUF = 65536

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        """初始化网络客户端实例，设置连接地址和端口，并初始化套接字对象。

        参数:
            host (str): 连接目标主机的地址，默认值为 `DEFAULT_HOST`。
            port (int): 连接目标主机的端口号，默认值为 `DEFAULT_PORT`。

        返回值:
            无 (None)
        """
        # 设置主机地址
        self.host = host
        # 设置端口号
        self.port = port
        # 初始化用于实际通信的套接字对象，初始值为空
        self._sock: socket.socket | None = None

    # ── 连接管理 ──────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self, *, timeout: float = 5.0) -> None:
        """建立 TCP 连接并验证对端可用（ping）。"""
        if self._sock is not None:
            self.disconnect()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((self.host, self.port))
        self._sock = sock
        try:
            self.ping()
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        """断开当前套接字连接并清理资源。

        功能：
            关闭底层网络套接字，并将套接字引用重置为 None，以释放资源并指示连接已断开。
        参数：
            self (类实例): 类实例本身。
        返回值：
            None: 该方法没有返回值。
        """
        # 检查套接字是否已存在（即是否已连接）
        if self._sock is not None:
            try:
                # 尝试关闭套接字连接
                self._sock.close()
            except Exception:
                # 捕获并忽略关闭套接字时可能发生的任何异常，确保后续清理代码能执行
                pass
            # 将套接字引用重置为 None，标记连接已断开
            self._sock = None

    # ── 底层通信 ──────────────────────────────────────────

    def _send_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """发送一条 JSON 命令并读取一行 JSON 响应。"""
        if self._sock is None:
            raise PlayerClientError("未连接到播放器")
        cmd_name = cmd.get("cmd", "?")
        payload = json.dumps(cmd, ensure_ascii=False) + "\n"
        try:
            self._sock.sendall(payload.encode("utf-8"))
        except OSError as exc:
            self._sock = None
            raise PlayerClientError(f"[{cmd_name}] 发送失败: {exc}") from exc
        buf = b""
        try:
            while not buf.endswith(b"\n"):
                chunk = self._sock.recv(self.RECV_BUF)
                if not chunk:
                    self._sock = None
                    raise PlayerClientError(f"[{cmd_name}] 连接已断开")
                buf += chunk
        except OSError as exc:
            self._sock = None
            raise PlayerClientError(f"[{cmd_name}] 接收失败: {exc}") from exc
        try:
            resp = json.loads(buf.decode("utf-8").strip())
        except json.JSONDecodeError as exc:
            raw = buf[:200].decode("utf-8", errors="replace")
            raise PlayerClientError(f"[{cmd_name}] 响应解析失败: {exc}, raw={raw}") from exc
        if not isinstance(resp, dict):
            raise PlayerClientError(f"[{cmd_name}] 响应格式异常: {resp}")
        if not resp.get("ok"):
            err_msg = resp.get("error", "未知错误")
            raise PlayerClientError(f"[{cmd_name}] 播放器返回错误: {err_msg}")
        return resp

    # ── 高级命令 ──────────────────────────────────────────

    def ping(self) -> str:
        resp = self._send_command({"cmd": "ping"})
        return str(resp.get("result", ""))

    def state(self) -> dict:
        """获取完整播放器状态。"""
        resp = self._send_command({"cmd": "state"})
        return resp.get("result") or {}

    def current_track(self) -> dict | None:
        """获取当前播放曲目。"""
        resp = self._send_command({"cmd": "current_track"})
        return resp.get("result")

    def current_playlist(self) -> dict | None:
        """获取当前播放歌单。"""
        resp = self._send_command({"cmd": "current_playlist"})
        return resp.get("result")

    def get_playlist(self, playlist_id: str) -> dict | None:
        """获取指定歌单（只读，不影响当前播放，曲目含 source_sha256）。"""
        resp = self._send_command({"cmd": "get_playlist", "playlist_id": playlist_id})
        return resp.get("result")

    def load_playlist(self, playlist_id: str) -> None:
        """加载歌单（不自动播放）。"""
        self._send_command({"cmd": "load_playlist", "playlist_id": playlist_id})

    def play_file(self, path: str) -> bool:
        """播放指定文件路径。"""
        resp = self._send_command({"cmd": "play_file", "path": path})
        return bool(resp.get("ok"))

    def remove_track_from_playlist(self, playlist_id: str, track_id: str) -> bool:
        """从歌单中移除曲目。

        注意：此命令需要 MusePlayer 端 control_server.py 中注册了
        ``remove_track_from_playlist`` 命令才可用。
        """
        resp = self._send_command({
            "cmd": "remove_track_from_playlist",
            "playlist_id": playlist_id,
            "track_id": track_id,
        })
        return bool(resp.get("ok"))
