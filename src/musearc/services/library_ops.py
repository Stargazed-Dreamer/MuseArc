from __future__ import annotations

from musearc.core.ids import new_id
from musearc.infra.db.repositories import FAVORITES_PLAYLIST_ID, LibraryRepository


class LibraryOpsService:
    def __init__(self, repo: LibraryRepository):
        self.repo = repo

    def search(self, query: str, limit: int = 100) -> list[dict]:
        return self.repo.search_tracks(query, limit=limit)

    def list_tracks(self, limit: int = 5000) -> list[dict]:
        return self.repo.list_tracks(limit=limit)

    def pending_reviews(self, limit: int = 100) -> list[dict]:
        return self.repo.list_pending_reviews(limit=limit)

    def resolve_reviews(self, review_ids: list[str], status: str = "resolved") -> int:
        return self.repo.resolve_reviews(review_ids, status=status)

    def delete_tracks(self, track_ids: list[str], *, mode: str = "move_linked_lyrics") -> int:
        """删除指定的音轨，并根据模式处理关联的歌词。

        功能：软删除指定的音轨，并清理相关关系。根据 mode 参数决定如何处理关联的歌词。
        参数：
            track_ids (list[str]): 要删除的音轨ID列表。
            mode (str, 可选): 处理模式。默认为 "move_linked_lyrics"。
                - "move_linked_lyrics": 将关联的歌词移动到垃圾箱。
                - "unlink_only": 仅取消关联歌词，不移动。
        返回值：
            int: 实际删除的音轨数量。
        """
        count = self.repo.soft_delete_tracks(track_ids)  # 调用仓库层软删除音轨，获取删除数量
        if count > 0:  # 如果有音轨被成功删除
            self.repo.cleanup_relations_after_soft_delete(track_ids)  # 清理软删除后可能残留的关系数据
            if mode == "move_linked_lyrics":  # 如果模式为移动关联歌词到垃圾箱
                linked = self.repo.linked_lyrics_ids_for_tracks(track_ids)  # 查询与这些音轨关联的歌词ID列表
                if linked:  # 如果存在关联歌词
                    self.repo.move_lyrics_to_trash(linked)  # 将这些关联歌词移至垃圾箱
            elif mode == "unlink_only":  # 如果模式为仅取消关联，不删除歌词
                self.repo.unlink_lyrics_for_tracks(track_ids)  # 取消音轨与歌词的关联关系
        return count  # 返回删除的音轨数量

    def restore_tracks(self, track_ids: list[str]) -> int:
        count = self.repo.restore_tracks(track_ids)
        if count > 0:
            self.repo.restore_lyrics_for_tracks(track_ids)
        return count

    def cascade_delete_lyrics_for_tracks(self, track_ids: list[str]) -> list[str]:
        return self.repo.cascade_delete_lyrics_for_tracks(track_ids)

    def unlink_lyrics_for_tracks(self, track_ids: list[str]) -> int:
        return self.repo.unlink_lyrics_for_tracks(track_ids)

    def update_tracks_fields(self, track_ids: list[str], fields: dict[str, object]) -> int:
        return self.repo.update_tracks_fields(track_ids, fields)

    def list_deleted_tracks(self, limit: int = 5000) -> list[dict]:
        return self.repo.list_deleted_tracks(limit=limit)

    def list_import_batches(self, limit: int = 200) -> list[dict]:
        return self.repo.list_import_batches(limit=limit)

    def get_import_batch(self, import_batch_id: str) -> dict | None:
        return self.repo.get_import_batch(import_batch_id)

    def list_playlists(self) -> list[dict]:
        return self.repo.list_playlists()

    def create_playlist(self, name: str, description: str = "") -> str:
        playlist_id = new_id("pl")
        self.repo.create_playlist(playlist_id, name, description)
        return playlist_id

    def delete_playlist(self, playlist_id: str) -> int:
        return self.repo.delete_playlist(playlist_id)

    def list_playlist_items(self, playlist_id: str) -> list[dict]:
        return self.repo.list_playlist_items(playlist_id)

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        return self.repo.add_tracks_to_playlist(playlist_id, track_ids)

    def remove_tracks_from_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        return self.repo.remove_tracks_from_playlist(playlist_id, track_ids)

    def clear_playlist(self, playlist_id: str) -> int:
        return self.repo.clear_playlist(playlist_id)

    def reorder_playlist(self, playlist_id: str, ordered_track_ids: list[str]) -> None:
        self.repo.reorder_playlist(playlist_id, ordered_track_ids)

    def update_playlist_entries(self, playlist_id: str, entries: dict[str, int]) -> int:
        return self.repo.update_playlist_entries(playlist_id, entries)

    def add_to_favorites(self, track_ids: list[str]) -> int:
        self.repo.ensure_favorites_playlist()
        return self.repo.add_tracks_to_playlist(FAVORITES_PLAYLIST_ID, track_ids)

    def remove_from_favorites(self, track_ids: list[str]) -> int:
        return self.repo.remove_tracks_from_playlist(FAVORITES_PLAYLIST_ID, track_ids)

    def list_tag_fields(self) -> list[dict]:
        return self.repo.list_tag_fields()

    def create_tag_field(self, tag_name: str) -> bool:
        return self.repo.create_tag_field(tag_name)

    def delete_tag_field(self, tag_name: str) -> int:
        return self.repo.delete_tag_field(tag_name)

    def update_track_tag_values(self, track_ids: list[str], tag_name: str, value: str) -> int:
        return self.repo.update_track_tag_values(track_ids, tag_name, value)

    def list_lyrics(self, limit: int = 5000) -> list[dict]:
        return self.repo.list_lyrics(limit)

    def set_primary_lyrics_for_track(self, track_id: str, lyrics_id: str | None) -> None:
        self.repo.set_primary_lyrics_for_track(track_id, lyrics_id)

    def set_primary_track_for_lyrics(self, lyrics_id: str, track_id: str | None) -> None:
        self.repo.set_primary_track_for_lyrics(lyrics_id, track_id)

    def update_lyrics_author(self, lyrics_ids: list[str], author: str) -> int:
        return self.repo.update_lyrics_author(lyrics_ids, author)

    def update_lyrics_fields(self, lyrics_ids: list[str], fields: dict[str, object]) -> int:
        return self.repo.update_lyrics_fields(lyrics_ids, fields)

    def delete_lyrics(self, lyrics_ids: list[str]) -> list[str]:
        return self.repo.delete_lyrics(lyrics_ids)

    def restore_lyrics(self, lyrics_ids: list[str]) -> int:
        return self.repo.restore_lyrics(lyrics_ids)

    def create_fullscan_work(self, name: str, track_ids: list[str]) -> str:
        """
        创建一个新的全扫描工作。

        该方法生成一个唯一的工作ID，并调用仓库方法存储工作信息。

        参数：
            name (str): 工作的名称。
            track_ids (list[str]): 需要扫描的轨道ID列表。

        返回值：
            str: 生成的工作ID。
        """
        work_id = new_id("work")  # 生成一个新的唯一工作ID
        self.repo.create_fullscan_work(work_id, name, track_ids)  # 调用仓库方法，将工作信息持久化存储
        return work_id  # 返回生成的工作ID

    def list_fullscan_works(self) -> list[dict]:
        return self.repo.list_fullscan_works()

    def get_fullscan_work_items(self, work_id: str, limit: int = 200000) -> list[dict]:
        return self.repo.get_fullscan_work_items(work_id, limit)

    def remove_fullscan_items(self, work_id: str, track_ids: list[str]) -> int:
        return self.repo.remove_fullscan_items(work_id, track_ids)

    def update_fullscan_items_status(self, work_id: str, track_ids: list[str], status: str) -> int:
        return self.repo.update_fullscan_items_status(work_id, track_ids, status)

    def delete_fullscan_work(self, work_id: str) -> int:
        return self.repo.delete_fullscan_work(work_id)
