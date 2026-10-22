"""AstrBot 漂流瓶插件（Web 审核版）。

本插件由 NoneBot 插件 nonebot-plugin-web-bottle 迁移而来，功能保持一致：

- 群聊 / 私聊内：扔漂流瓶、捡漂流瓶、评论漂流瓶、点赞漂流瓶、查看漂流瓶；
- AstrBot WebUI 内：漂流瓶审核台（审核漂流瓶与评论）。

数据存放在 ``data/plugin_data/astrbot_plugin_web_bottle/``，与插件目录分离，
更新 / 重装插件不会丢失数据。
"""

from __future__ import annotations

import base64
import inspect
import sqlite3
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:  # AstrBot >= 插件可视化视图版本
    from astrbot.api.web import error_response, json_response

    WEB_API_AVAILABLE = True
except Exception:  # pragma: no cover - 旧版本 AstrBot 走这里
    WEB_API_AVAILABLE = False

    def json_response(payload: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return payload

    def error_response(message: str, status_code: int = 400) -> Any:  # type: ignore[misc]
        return {"status": "error", "message": message, "code": status_code}


try:  # 数据目录
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover
    get_astrbot_data_path = None  # type: ignore[assignment]


PLUGIN_NAME = "astrbot_plugin_web_bottle"
__version__ = "1.1.0"

# 审核状态：0 待审核，100 已拒绝，200 已通过
STATE_PENDING = 0
STATE_REJECTED = 100
STATE_APPROVED = 200

HELP_TEXT_TEMPLATE = """漂流瓶使用帮助
{p}扔漂流瓶 [文字]（可附带图片） 把瓶子扔进大海，等待审核
{p}捡漂流瓶                       随机捞一个已通过审核的瓶子
{p}评论漂流瓶 [编号] [内容]     给漂流瓶留言（需审核后展示）
{p}点赞漂流瓶 [编号]            给喜欢的瓶子点个赞
{p}查看漂流瓶 [编号]            按编号查看漂流瓶
{p}漂流瓶                       显示本帮助"""

# 指令 key -> 指令名与别名（用于「无前缀 / 自定义前缀」模式匹配）
COMMAND_NAMES: dict[str, tuple[str, ...]] = {
    "help": ("漂流瓶", "漂流瓶菜单", "漂流瓶帮助"),
    "throw": ("扔漂流瓶", "丢瓶子", "扔瓶子"),
    "get": ("捡漂流瓶", "捡瓶子", "捞瓶子"),
    "read": ("查看漂流瓶",),
    "comment": ("评论漂流瓶",),
    "up": ("点赞漂流瓶",),
}

_ACTIVE_PLUGIN: "WebBottlePlugin | None" = None
"""当前已加载的插件实例，供自定义 Filter 读取插件配置。"""


def _optional_custom_filter(filter_cls: type) -> Any:
    """旧版本 AstrBot 没有 custom_filter 时退化为「不注册该指令」。"""
    decorator = getattr(filter, "custom_filter", None)
    if decorator is None:
        return lambda func: func
    try:
        return decorator(filter_cls)
    except Exception:  # pragma: no cover
        return lambda func: func


_CustomFilterBase = getattr(filter, "CustomFilter", object)


class CommandPrefixFilter(_CustomFilterBase):  # type: ignore[misc, valid-type]
    """匹配「没有走 AstrBot 唤醒前缀」的漂流瓶指令。

    只在该过滤器命中时才让事件被唤醒，因此普通聊天不会受影响；
    命中后交给 ``prefix_command`` 统一分发。
    """

    def __init__(self, raise_error: bool = True) -> None:
        if _CustomFilterBase is not object:
            super().__init__(raise_error)

    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        plugin = _ACTIVE_PLUGIN
        if plugin is None:
            return False
        try:
            # 已经通过 @ / 全局唤醒前缀 / 私聊唤醒的事件交给标准指令处理，
            # 这里直接放行（返回 False），避免同一条消息被处理两次。
            if getattr(event, "is_at_or_wake_command", False):
                return False
            return (
                plugin.match_command_text(getattr(event, "message_str", "") or "")
                is not None
            )
        except Exception:  # noqa: BLE001
            return False


# --------------------------------------------------------------------------- #
# 路径工具
# --------------------------------------------------------------------------- #
def _resolve_data_dir() -> Path:
    """返回插件数据目录，优先使用 AstrBot 的 data 目录。"""
    if get_astrbot_data_path is not None:
        try:
            base = Path(get_astrbot_data_path())
            return base / "plugin_data" / PLUGIN_NAME
        except Exception as exc:  # pragma: no cover
            logger.warning(f"获取 AstrBot 数据目录失败，将使用当前目录：{exc}")
    return Path.cwd() / "data" / "plugin_data" / PLUGIN_NAME


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 数据存储
# --------------------------------------------------------------------------- #
class BottleStore:
    """漂流瓶数据层：SQLite 数据库 + 图片文件缓存。

    表结构与 NoneBot 原版保持一致，并新增 username / groupname 两列，
    方便展示发送者昵称，无需再调用协议端 API。
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.img_dir = self.data_dir / "img"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "bottle.db"
        self.counter_path = self.data_dir / "bottle_id.txt"
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    # ---------------------------- 基础 ---------------------------- #
    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS approved (
                    id INTEGER PRIMARY KEY,
                    content TEXT,
                    userid TEXT,
                    groupid TEXT,
                    timeinfo TEXT,
                    up INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    groupname TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS comments (
                    comment_id INTEGER PRIMARY KEY,
                    id INTEGER,
                    content TEXT,
                    state INTEGER DEFAULT 0,
                    uid TEXT,
                    username TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS pending (
                    id INTEGER PRIMARY KEY,
                    content TEXT,
                    userid TEXT,
                    groupid TEXT,
                    timeinfo TEXT,
                    state INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    groupname TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS user_up (
                    uid TEXT PRIMARY KEY UNIQUE,
                    ids TEXT
                );
                """
            )
        # 兼容 NoneBot 原版数据库结构
        for table, column, decl in (
            ("approved", "username", "TEXT DEFAULT ''"),
            ("approved", "groupname", "TEXT DEFAULT ''"),
            ("pending", "username", "TEXT DEFAULT ''"),
            ("pending", "groupname", "TEXT DEFAULT ''"),
            ("comments", "username", "TEXT DEFAULT ''"),
        ):
            self._ensure_column(table, column, decl)
        self._migrate_legacy_images()
        logger.info(f"漂流瓶数据目录：{self.data_dir}")

    def _migrate_legacy_images(self) -> None:
        """把 NoneBot 旧版本 images 表里的图片迁移成文件。"""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='images'"
        ).fetchone()
        if row is None:
            return
        logger.info("检测到旧版 images 表，开始迁移图片...")
        try:
            rows = self.conn.execute("SELECT id, data FROM images").fetchall()
            for image_id, data in rows:
                if not data:
                    continue
                self.save_image(_safe_int(image_id), bytes(data))
            with self.conn:
                self.conn.execute("DROP TABLE images")
            logger.info(f"旧版 images 表迁移完成，共 {len(rows)} 条")
        except sqlite3.Error as exc:  # pragma: no cover
            logger.warning(f"迁移旧版 images 表失败：{exc}")

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        try:
            columns = {
                row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in columns:
                with self.conn:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.Error as exc:  # pragma: no cover
            logger.warning(f"检查数据表 {table}.{column} 失败：{exc}")

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # pragma: no cover
            pass

    # ---------------------------- 瓶子 ID ---------------------------- #
    def next_bottle_id(self) -> int:
        try:
            counter = _safe_int(self.counter_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            counter = 0
        row = self.conn.execute(
            "SELECT MAX(id) AS max_id FROM ("
            "SELECT id FROM pending UNION ALL SELECT id FROM approved)"
        ).fetchone()
        max_id = _safe_int(row["max_id"] if row else 0)
        new_id = max(counter, max_id) + 1
        try:
            self.counter_path.write_text(str(new_id), encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            logger.warning(f"写入瓶子计数器失败：{exc}")
        return new_id

    # ---------------------------- 待审核瓶子 ---------------------------- #
    def add_pending(
        self,
        bottle_id: int,
        content: str,
        userid: str,
        groupid: str,
        username: str,
        groupname: str,
        timeinfo: str,
    ) -> bool:
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO pending "
                    "(id, content, userid, groupid, timeinfo, state, username, groupname) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        bottle_id,
                        content,
                        userid,
                        groupid,
                        timeinfo,
                        STATE_PENDING,
                        username,
                        groupname,
                    ),
                )
            return True
        except sqlite3.Error as exc:
            logger.error(f"保存漂流瓶 {bottle_id} 失败：{exc}")
            return False

    def pending_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM pending WHERE state = ?", (STATE_PENDING,)
        ).fetchone()
        return _safe_int(row["c"] if row else 0)

    def random_pending(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, content, userid, groupid, timeinfo, state, username, groupname "
            "FROM pending WHERE state = ? ORDER BY RANDOM() LIMIT 1",
            (STATE_PENDING,),
        ).fetchone()
        return dict(row) if row else None

    def pending_state(self, bottle_id: int) -> int | None:
        row = self.conn.execute(
            "SELECT state FROM pending WHERE id = ?", (bottle_id,)
        ).fetchone()
        return _safe_int(row["state"]) if row else None

    def approve_bottle(self, bottle_id: int) -> bool:
        row = self.conn.execute(
            "SELECT id, content, userid, groupid, timeinfo, username, groupname "
            "FROM pending WHERE id = ? AND state = ?",
            (bottle_id, STATE_PENDING),
        ).fetchone()
        if row is None:
            return False
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO approved "
                    "(id, content, userid, groupid, timeinfo, up, username, groupname) "
                    "VALUES (?, ?, ?, ?, ?, COALESCE("
                    "(SELECT up FROM approved WHERE id = ?), 0), ?, ?)",
                    (
                        row["id"],
                        row["content"],
                        row["userid"],
                        row["groupid"],
                        row["timeinfo"],
                        bottle_id,
                        row["username"],
                        row["groupname"],
                    ),
                )
                self.conn.execute(
                    "UPDATE pending SET state = ? WHERE id = ?",
                    (STATE_APPROVED, bottle_id),
                )
            return True
        except sqlite3.Error as exc:
            logger.error(f"审核通过漂流瓶 {bottle_id} 失败：{exc}")
            return False

    def refuse_bottle(self, bottle_id: int) -> bool:
        row = self.conn.execute(
            "SELECT id FROM pending WHERE id = ? AND state = ?",
            (bottle_id, STATE_PENDING),
        ).fetchone()
        if row is None:
            return False
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE pending SET state = ? WHERE id = ?",
                    (STATE_REJECTED, bottle_id),
                )
            self.delete_images(bottle_id)
            return True
        except sqlite3.Error as exc:
            logger.error(f"拒绝漂流瓶 {bottle_id} 失败：{exc}")
            return False

    # ---------------------------- 已通过瓶子 ---------------------------- #
    def approved_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM approved").fetchone()
        return _safe_int(row["c"] if row else 0)

    def get_approved(self, bottle_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, content, userid, groupid, timeinfo, up, username, groupname "
            "FROM approved WHERE id = ?",
            (bottle_id,),
        ).fetchone()
        return dict(row) if row else None

    def random_approved(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, content, userid, groupid, timeinfo, up, username, groupname "
            "FROM approved ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ---------------------------- 点赞 ---------------------------- #
    def add_up(self, bottle_id: int, uid: str) -> tuple[bool, int]:
        row = self.conn.execute(
            "SELECT up FROM approved WHERE id = ?", (bottle_id,)
        ).fetchone()
        if row is None:
            return False, 0

        up_row = self.conn.execute(
            "SELECT ids FROM user_up WHERE uid = ?", (uid,)
        ).fetchone()
        ids = (up_row["ids"] or "").split(",") if up_row and up_row["ids"] else []
        ids = [item for item in ids if item]
        if str(bottle_id) in ids:
            return False, _safe_int(row["up"])

        new_up = _safe_int(row["up"]) + 1
        ids.append(str(bottle_id))
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE approved SET up = ? WHERE id = ?", (new_up, bottle_id)
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO user_up (uid, ids) VALUES (?, ?)",
                    (uid, ",".join(ids)),
                )
            return True, new_up
        except sqlite3.Error as exc:  # pragma: no cover
            logger.error(f"点赞漂流瓶 {bottle_id} 失败：{exc}")
            return False, _safe_int(row["up"])

    # ---------------------------- 评论 ---------------------------- #
    def add_comment(self, bottle_id: int, content: str, uid: str, username: str) -> bool:
        exists = self.conn.execute(
            "SELECT 1 FROM approved WHERE id = ?", (bottle_id,)
        ).fetchone()
        if exists is None:
            return False
        row = self.conn.execute(
            "SELECT MAX(comment_id) AS m FROM comments"
        ).fetchone()
        new_id = _safe_int(row["m"] if row else 0) + 1
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO comments "
                    "(comment_id, id, content, state, uid, username) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id, bottle_id, content, STATE_PENDING, uid, username),
                )
            return True
        except sqlite3.Error as exc:  # pragma: no cover
            logger.error(f"保存评论失败：{exc}")
            return False

    def comments_for(self, bottle_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT comment_id, id, content, uid, username FROM comments "
            "WHERE id = ? AND state = ? ORDER BY comment_id ASC LIMIT ?",
            (bottle_id, STATE_APPROVED, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def comment_total(self, bottle_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM comments WHERE id = ? AND state = ?",
            (bottle_id, STATE_APPROVED),
        ).fetchone()
        return _safe_int(row["c"] if row else 0)

    def pending_comment_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM comments WHERE state = ?", (STATE_PENDING,)
        ).fetchone()
        return _safe_int(row["c"] if row else 0)

    def approved_comment_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM comments WHERE state = ?", (STATE_APPROVED,)
        ).fetchone()
        return _safe_int(row["c"] if row else 0)

    def random_pending_comment(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT comment_id, id, content, uid, username FROM comments "
            "WHERE state = ? ORDER BY RANDOM() LIMIT 1",
            (STATE_PENDING,),
        ).fetchone()
        return dict(row) if row else None

    def set_comment_state(self, comment_id: int, state: int) -> bool:
        rows = self.conn.execute(
            "SELECT 1 FROM comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()
        if rows is None:
            return False
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE comments SET state = ? WHERE comment_id = ?",
                    (state, comment_id),
                )
            return True
        except sqlite3.Error as exc:  # pragma: no cover
            logger.error(f"更新评论 {comment_id} 状态失败：{exc}")
            return False

    # ---------------------------- 统计 ---------------------------- #
    def stats(self) -> dict[str, int]:
        up_row = self.conn.execute(
            "SELECT COALESCE(SUM(up), 0) AS s FROM approved"
        ).fetchone()
        return {
            "pending_bottles": self.pending_count(),
            "pending_comments": self.pending_comment_count(),
            "approved_bottles": self.approved_count(),
            "approved_comments": self.approved_comment_count(),
            "total_up": _safe_int(up_row["s"] if up_row else 0),
        }

    # ---------------------------- 图片 ---------------------------- #
    def image_dir(self, bottle_id: int) -> Path:
        return self.img_dir / str(bottle_id)

    def image_paths(self, bottle_id: int) -> list[Path]:
        folder = self.image_dir(bottle_id)
        if not folder.is_dir():
            return []
        files = []
        for file in folder.glob("*.webp"):
            try:
                files.append((int(file.stem), file))
            except ValueError:
                continue
        return [file for _, file in sorted(files)]

    def delete_images(self, bottle_id: int) -> None:
        folder = self.image_dir(bottle_id)
        if not folder.is_dir():
            return
        try:
            for file in folder.iterdir():
                if file.is_file():
                    file.unlink()
            folder.rmdir()
        except OSError as exc:  # pragma: no cover
            logger.warning(f"删除漂流瓶 {bottle_id} 的图片失败：{exc}")

    def save_image(self, bottle_id: int, data: bytes) -> bool:
        """把图片转成 WebP 存盘，返回是否成功。"""
        try:
            from PIL import Image as PILImage
            from PIL import ImageSequence
        except ImportError:  # pragma: no cover
            logger.error("缺少 Pillow，无法保存漂流瓶图片")
            return False

        try:
            image = PILImage.open(BytesIO(data))
            folder = self.image_dir(bottle_id)
            folder.mkdir(parents=True, exist_ok=True)
            indexes = []
            for file in folder.glob("*.webp"):
                try:
                    indexes.append(int(file.stem))
                except ValueError:
                    continue
            output = folder / f"{(max(indexes) + 1) if indexes else 0}.webp"

            animated = bool(
                getattr(image, "is_animated", False)
                or getattr(image, "n_frames", 1) > 1
            )
            if animated:
                frames = [frame.copy() for frame in ImageSequence.Iterator(image)]
                frames[0].save(
                    output,
                    format="WEBP",
                    save_all=True,
                    append_images=frames[1:],
                    duration=image.info.get("duration", 100),
                    loop=0,
                    quality=80,
                )
            else:
                image.convert("RGB").save(output, format="WEBP", quality=80)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"保存漂流瓶 {bottle_id} 的图片失败：{exc}")
            return False


# --------------------------------------------------------------------------- #
# 插件主体
# --------------------------------------------------------------------------- #
class WebBottlePlugin(Star):
    """漂流瓶插件：聊天指令 + WebUI 审核台。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        global _ACTIVE_PLUGIN
        _ACTIVE_PLUGIN = self
        self.config = config or {}
        self.data_dir = _resolve_data_dir()
        self.store = BottleStore(self.data_dir)
        self._cooldowns: dict[str, dict[str, float]] = {}
        self._register_web_api(context)
        self._log_runtime_status()

    # ---------------------------- 运行状态日志 ---------------------------- #
    def _log_runtime_status(self) -> None:
        """启动时打印审核台与指令前缀状态，方便排查「看不到 WebUI」。"""
        view_entry = Path(__file__).resolve().parent / "views" / "review" / "index.html"
        if view_entry.is_file():
            logger.info(f"漂流瓶审核台视图已就绪：{view_entry}")
        else:
            logger.warning(
                f"未找到审核台视图文件：{view_entry}，"
                "插件详情页将不会显示「可视化视图」入口"
            )
        logger.info(
            "漂流瓶指令前缀配置：require_prefix=%s, command_prefix=%r",
            self._cfg_bool("require_prefix", True),
            self._cfg_str("command_prefix", "/"),
        )

    # ---------------------------- 配置读取 ---------------------------- #
    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError, AttributeError):
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            return default
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _cfg_str(self, key: str, default: str) -> str:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            return default
        return str(value) if value is not None else default

    # ---------------------------- 冷却 ---------------------------- #
    def _cooldown_remaining(self, event: AstrMessageEvent, key: str) -> int:
        cooling = self._cfg_int("cooling_time", 6)
        if cooling <= 0:
            return 0
        uid = str(event.get_sender_id() or "")
        bucket = self._cooldowns.setdefault(key, {})
        now = time.monotonic()
        last = bucket.get(uid, 0.0)
        remaining = cooling - (now - last)
        if remaining > 0:
            return int(remaining) + 1
        bucket[uid] = now
        return 0

    # ---------------------------- 指令参数 ---------------------------- #
    @staticmethod
    def _command_args(event: AstrMessageEvent, *names: str) -> str:
        """取出指令后面的参数。

        AstrBot 不同版本对 message_str 是否包含指令名处理不一致，
        因此这里做一次兼容：如果开头是指令名就去掉。
        """
        text = (event.message_str or "").strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        for name in sorted(names, key=len, reverse=True):
            if text.startswith(name):
                return text[len(name):].strip()
        return text

    # ---------------------------- 图片处理 ---------------------------- #
    @staticmethod
    def _is_image_component(component: Any) -> bool:
        if isinstance(component, Comp.Image):
            return True
        return getattr(component, "type", None) == "image"

    async def _read_image_source(self, source: str) -> bytes | None:
        source = source.strip()
        if not source:
            return None
        try:
            if source.startswith("base64://"):
                return base64.b64decode(source[len("base64://"):])
            if source.startswith("data:image/"):
                _, _, payload = source.partition(",")
                return base64.b64decode(payload)
            if source.startswith(("http://", "https://")):
                async with httpx.AsyncClient(
                    timeout=20, follow_redirects=True
                ) as client:
                    response = await client.get(
                        source, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    response.raise_for_status()
                    return response.content
            if source.startswith("file://"):
                path = Path(unquote(urlparse(source).path))
                return path.read_bytes() if path.is_file() else None
            path = Path(source)
            if path.is_file():
                return path.read_bytes()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"读取图片 {source[:64]} 失败：{exc}")
        return None

    async def _component_image_bytes(self, component: Any) -> bytes | None:
        candidates: list[str] = []
        for attr in ("url", "file", "path", "file_path"):
            value = getattr(component, attr, None)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
            elif isinstance(value, dict):
                for key in ("url", "file", "path"):
                    inner = value.get(key)
                    if isinstance(inner, str) and inner.strip():
                        candidates.append(inner.strip())

        for source in candidates:
            data = await self._read_image_source(source)
            if data:
                return data

        # 部分适配器提供 convert_to_file_path() 来落地图片
        converter = getattr(component, "convert_to_file_path", None)
        if callable(converter):
            try:
                result = converter()
                if inspect.isawaitable(result):
                    result = await result
                if result:
                    path = Path(str(result))
                    if path.is_file():
                        return path.read_bytes()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"convert_to_file_path 失败：{exc}")
        return None

    async def _message_images(self, event: AstrMessageEvent) -> list[bytes]:
        components = getattr(getattr(event, "message_obj", None), "message", None) or []
        images: list[bytes] = []
        for component in components:
            if not self._is_image_component(component):
                continue
            data = await self._component_image_bytes(component)
            if data is None:
                logger.warning("有一张漂流瓶图片未能下载，已跳过")
                continue
            images.append(data)
        return images

    # ---------------------------- 消息构建 ---------------------------- #
    def _display_name(self, row: dict[str, Any]) -> str:
        name = (row.get("username") or "").strip()
        if name:
            return name
        return self._cfg_str("default_nickname", "未知昵称")

    def _bottle_text(self, row: dict[str, Any], index: int | None = None) -> str:
        content = (row.get("content") or "").strip() or "（只投了图片，没有写文字）"
        lines = []
        title = f"漂流瓶 ID：{row['id']}"
        if index is not None:
            title += f"（第 {index} 条）"
        lines.append(title)
        lines.append(f"内容：{content}")
        if self._cfg_bool("show_sender_name", True):
            lines.append(f"发送者：{self._display_name(row)}")
        if self._cfg_bool("show_group_name", True):
            group = (row.get("groupname") or "").strip()
            group_id = (row.get("groupid") or "").strip()
            lines.append(f"发送群：{group or group_id or '私聊'}")
        lines.append(f"发送时间：{row.get('timeinfo') or '未知'}")
        lines.append(f"点赞数：{_safe_int(row.get('up'))}")
        return "\n".join(lines)

    def _comments_text(self, bottle_id: int) -> str:
        limit = max(self._cfg_int("max_bottle_comments", 3), 1)
        comments = self.store.comments_for(bottle_id, limit)
        if not comments:
            return ""
        total = self.store.comment_total(bottle_id)
        lines = [f"评论区（共 {total} 条，展示前 {len(comments)} 条）："]
        for comment in comments:
            name = (comment.get("username") or "").strip() or self._cfg_str(
                "default_nickname", "未知昵称"
            )
            content = (comment.get("content") or "").strip()
            lines.append(f"{name}：{content}")
        return "\n".join(lines)

    def _bottle_chains(self, event: AstrMessageEvent, row: dict[str, Any]) -> list[list]:
        chains: list[list] = []
        chain: list = [Comp.Plain(self._bottle_text(row))]
        for image_path in self.store.image_paths(_safe_int(row.get("id"))):
            chain.append(Comp.Image.fromFileSystem(str(image_path)))
        chains.append(chain)

        comments_text = self._comments_text(_safe_int(row.get("id")))
        if comments_text:
            if self._cfg_bool("bottle_msg_split", True):
                chains.append([Comp.Plain(comments_text)])
            else:
                chain.append(Comp.Plain("\n" + comments_text))
        return chains

    # ---------------------------- 帮助文本 ---------------------------- #
    def _help_text(self) -> str:
        prefix = self._command_prefix()
        if not prefix or not self._cfg_bool("require_prefix", True):
            prefix = ""
        return HELP_TEXT_TEMPLATE.format(p=prefix)

    # ---------------------------- 指令：帮助 ---------------------------- #
    @filter.command("漂流瓶", alias={"漂流瓶菜单", "漂流瓶帮助"})
    async def bottle_help(self, event: AstrMessageEvent):
        """查看漂流瓶插件的使用说明"""
        yield event.plain_result(self._help_text())

    async def _handle_help(self, event: AstrMessageEvent, args: str = ""):
        yield event.plain_result(self._help_text())

    # ---------------------------- 无前缀 / 自定义前缀 ---------------------------- #
    def _command_prefix(self) -> str:
        return self._cfg_str("command_prefix", "/").strip()

    def _hint_prefix(self) -> str:
        """用于提示文案的前缀：不要求前缀时返回空串。"""
        prefix = self._command_prefix()
        if not prefix or not self._cfg_bool("require_prefix", True):
            return ""
        return prefix

    def match_command_text(self, text: str) -> tuple[str, str, str] | None:
        """匹配指令文本，返回 (指令 key, 命中的指令名, 参数)；不匹配返回 None。"""
        raw = (text or "").strip()
        if not raw:
            return None
        prefix = self._command_prefix()
        require_prefix = self._cfg_bool("require_prefix", True)
        if prefix and raw.startswith(prefix):
            raw = raw[len(prefix):].lstrip()
        elif prefix and require_prefix:
            return None
        if not raw:
            return None

        # 长指令优先，避免「漂流瓶」抢先匹配「漂流瓶菜单」
        ordered = sorted(
            COMMAND_NAMES.items(),
            key=lambda item: max(len(name) for name in item[1]),
            reverse=True,
        )
        for key, names in ordered:
            for name in sorted(names, key=len, reverse=True):
                if raw == name:
                    return key, name, ""
                if raw.startswith(name) and raw[len(name)] in " \t\r\n\u3000":
                    return key, name, raw[len(name):].strip()
        return None

    def _command_handlers(self) -> dict[str, Any]:
        return {
            "help": self._handle_help,
            "throw": self._handle_throw,
            "get": self._handle_get,
            "read": self._handle_read,
            "comment": self._handle_comment,
            "up": self._handle_up,
        }

    @_optional_custom_filter(CommandPrefixFilter)
    async def prefix_command(self, event: AstrMessageEvent):
        """无前缀 / 自定义前缀模式下的指令入口"""
        matched = self.match_command_text(event.message_str or "")
        if matched is None:
            return
        key, _name, args = matched
        handler = self._command_handlers().get(key)
        if handler is None:
            return
        async for result in handler(event, args):
            yield result

    # ---------------------------- 指令：扔漂流瓶 ---------------------------- #
    @filter.command("扔漂流瓶", alias={"丢瓶子", "扔瓶子"})
    async def throw_bottle(self, event: AstrMessageEvent):
        """扔一个漂流瓶，支持文字与图片，审核通过后才会出现"""
        async for result in self._handle_throw(
            event, self._command_args(event, "扔漂流瓶", "丢瓶子", "扔瓶子")
        ):
            yield result

    async def _handle_throw(self, event: AstrMessageEvent, content: str):
        remaining = self._cooldown_remaining(event, "throw")
        if remaining > 0:
            yield event.plain_result(f"扔漂流瓶冷却中，请 {remaining} 秒后再试~")
            return

        images = await self._message_images(event)
        max_pic = self._cfg_int("max_bottle_pic", 2)
        max_word = self._cfg_int("max_bottle_word", 1200)
        max_lines = self._cfg_int("max_bottle_lines", 9)

        if not content and not images:
            message = "您还没有写好瓶子的内容哦~"
            if self._cfg_bool("embedded_help", True):
                message += f"\n{self._help_text()}"
            yield event.plain_result(message)
            return

        if len(content) > max_word:
            yield event.plain_result(f"扔漂流瓶失败啦，请不要超过 {max_word} 个字符哦~")
            return
        if len([line for line in content.splitlines() if line.strip()]) > max_lines:
            yield event.plain_result(f"扔漂流瓶失败啦，请不要超过 {max_lines} 行内容哦~")
            return
        if len(images) > max_pic:
            yield event.plain_result(f"扔漂流瓶失败啦，请不要超过 {max_pic} 张图片哦~")
            return

        bottle_id = self.store.next_bottle_id()
        group_id = str(getattr(event, "get_group_id", lambda: "")() or "")
        group_name = ""
        group_name_getter = getattr(event, "get_group_name", None)
        if callable(group_name_getter):
            try:
                value = group_name_getter()
                if inspect.isawaitable(value):
                    value = await value
                group_name = str(value or "")
            except Exception:  # noqa: BLE001
                group_name = ""

        username = str(event.get_sender_name() or "")
        userid = str(event.get_sender_id() or "")
        timeinfo = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for image_data in images:
            self.store.save_image(bottle_id, image_data)

        if self.store.add_pending(
            bottle_id, content, userid, group_id, username, group_name, timeinfo
        ):
            yield event.plain_result(
                f"扔漂流瓶成功！瓶子 ID 是：{bottle_id}，"
                "将在管理员审核通过后出现在大海中~"
            )
        else:
            self.store.delete_images(bottle_id)
            yield event.plain_result("扔漂流瓶失败啦，出现未知异常，请稍后再试")

    # ---------------------------- 指令：捡漂流瓶 ---------------------------- #
    @filter.command("捡漂流瓶", alias={"捡瓶子", "捞瓶子"})
    async def get_bottle(self, event: AstrMessageEvent):
        """随机捞一个已经通过审核的漂流瓶"""
        async for result in self._handle_get(event):
            yield result

    async def _handle_get(self, event: AstrMessageEvent, args: str = ""):
        remaining = self._cooldown_remaining(event, "get")
        if remaining > 0:
            yield event.plain_result(f"捡漂流瓶冷却中，请 {remaining} 秒后再试~")
            return

        row = self.store.random_approved()
        if row is None:
            yield event.plain_result("捡漂流瓶失败，大海里还没有漂流瓶~")
            return
        for chain in self._bottle_chains(event, row):
            yield event.chain_result(chain)

    # ---------------------------- 指令：查看漂流瓶 ---------------------------- #
    @filter.command("查看漂流瓶")
    async def read_bottle(self, event: AstrMessageEvent):
        """按编号查看一个漂流瓶"""
        async for result in self._handle_read(
            event, self._command_args(event, "查看漂流瓶")
        ):
            yield result

    async def _handle_read(self, event: AstrMessageEvent, args: str = ""):
        raw = args.split()
        if not raw:
            yield event.plain_result(
                f"请输入要查看的漂流瓶 ID，例如：{self._hint_prefix()}查看漂流瓶 12"
            )
            return
        bottle_id = _safe_int(raw[0], -1)
        if bottle_id < 0:
            yield event.plain_result("请输入正确的漂流瓶 ID")
            return

        row = self.store.get_approved(bottle_id)
        if row is None:
            state = self.store.pending_state(bottle_id)
            if state == STATE_REJECTED:
                yield event.plain_result("漂流瓶已被拒绝，无法查看！")
            elif state == STATE_PENDING:
                yield event.plain_result("漂流瓶还在审核中，请耐心等待~")
            else:
                yield event.plain_result("没有找到这个漂流瓶~")
            return
        for chain in self._bottle_chains(event, row):
            yield event.chain_result(chain)

    # ---------------------------- 指令：评论漂流瓶 ---------------------------- #
    @filter.command("评论漂流瓶")
    async def comment_bottle(self, event: AstrMessageEvent):
        """给漂流瓶留言，审核通过后展示"""
        async for result in self._handle_comment(
            event, self._command_args(event, "评论漂流瓶")
        ):
            yield result

    async def _handle_comment(self, event: AstrMessageEvent, args: str = ""):
        parts = args.split(maxsplit=1)
        if not parts:
            yield event.plain_result(
                f"用法：{self._hint_prefix()}评论漂流瓶 [编号] [评论内容]"
            )
            return
        bottle_id = _safe_int(parts[0], -1)
        if bottle_id < 0:
            yield event.plain_result("请输入正确的漂流瓶 ID 和评论内容")
            return
        if len(parts) < 2 or not parts[1].strip():
            yield event.plain_result("请输入评论内容")
            return

        content = parts[1].strip()
        max_word = self._cfg_int("max_bottle_word", 1200)
        if len(content) > max_word:
            yield event.plain_result(f"评论太长了，请不要超过 {max_word} 个字符哦~")
            return

        username = str(event.get_sender_name() or "")
        userid = str(event.get_sender_id() or "")
        if self.store.add_comment(bottle_id, content, userid, username):
            yield event.plain_result("评论成功！审核通过后即可展示~")
        else:
            yield event.plain_result("评论失败，漂流瓶不存在或还未通过审核")

    # ---------------------------- 指令：点赞漂流瓶 ---------------------------- #
    @filter.command("点赞漂流瓶")
    async def up_bottle(self, event: AstrMessageEvent):
        """给喜欢的漂流瓶点个赞"""
        async for result in self._handle_up(
            event, self._command_args(event, "点赞漂流瓶")
        ):
            yield result

    async def _handle_up(self, event: AstrMessageEvent, args: str = ""):
        parts = args.split()
        if not parts:
            yield event.plain_result(f"用法：{self._hint_prefix()}点赞漂流瓶 [编号]")
            return
        bottle_id = _safe_int(parts[0], -1)
        if bottle_id < 0:
            yield event.plain_result("请输入正确的漂流瓶 ID")
            return

        userid = str(event.get_sender_id() or "")
        success, total = self.store.add_up(bottle_id, userid)
        if success:
            yield event.plain_result(f"点赞成功，现在有 {total} 个赞！")
        else:
            if total:
                yield event.plain_result(f"你已经点赞过啦，现在有 {total} 个赞！")
            else:
                yield event.plain_result("点赞失败，漂流瓶不存在或还未通过审核")

    # ---------------------------- WebUI 审核 API ---------------------------- #
    def _register_web_api(self, context: Context) -> None:
        register = getattr(context, "register_web_api", None)
        if not WEB_API_AVAILABLE or not callable(register):
            logger.warning(
                "当前 AstrBot 版本不支持插件可视化视图（register_web_api），"
                "漂流瓶审核台不可用；聊天指令不受影响。"
            )
            return

        routes = (
            ("/stats", self.api_stats, ["GET"], "漂流瓶审核台统计"),
            ("/bottles/random", self.api_random_bottle, ["GET"], "随机取一个待审漂流瓶"),
            ("/bottles/approve/<bottle_id>", self.api_approve_bottle, ["POST"], "通过漂流瓶"),
            ("/bottles/refuse/<bottle_id>", self.api_refuse_bottle, ["POST"], "拒绝漂流瓶"),
            ("/comments/random", self.api_random_comment, ["GET"], "随机取一条待审评论"),
            ("/comments/approve/<comment_id>", self.api_approve_comment, ["POST"], "通过评论"),
            ("/comments/refuse/<comment_id>", self.api_refuse_comment, ["POST"], "拒绝评论"),
        )
        for route, handler, methods, description in routes:
            try:
                register(f"/{PLUGIN_NAME}{route}", handler, methods, description)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"注册 WebUI 接口 {route} 失败：{exc}")
                return
        logger.info("漂流瓶审核台已注册：插件详情页 → 可视化视图")

    async def api_stats(self):
        """审核台统计信息"""
        return json_response({"stats": self.store.stats()})

    async def api_random_bottle(self):
        """随机返回一个待审核漂流瓶（图片以 base64 返回）"""
        row = self.store.random_pending()
        if row is None:
            return json_response({"bottle": None, "images": [], "pending": 0})
        images: list[str] = []
        for path in self.store.image_paths(_safe_int(row.get("id"))):
            try:
                images.append(base64.b64encode(path.read_bytes()).decode("utf-8"))
            except OSError as exc:  # pragma: no cover
                logger.warning(f"读取图片 {path} 失败：{exc}")
        payload = {
            "id": row.get("id"),
            "content": row.get("content") or "",
            "username": self._display_name(row),
            "userid": row.get("userid") or "",
            "groupname": row.get("groupname") or "",
            "groupid": row.get("groupid") or "",
            "timeinfo": row.get("timeinfo") or "",
        }
        return json_response(
            {"bottle": payload, "images": images, "pending": self.store.pending_count()}
        )

    async def api_approve_bottle(self, bottle_id: str):
        """通过一个漂流瓶"""
        target = _safe_int(bottle_id, -1)
        if target < 0:
            return error_response("无效的漂流瓶 ID", status_code=400)
        if not self.store.approve_bottle(target):
            return error_response("漂流瓶不存在", status_code=404)
        logger.info(f"审核通过漂流瓶 {target}")
        return json_response(
            {"ok": True, "pending": self.store.pending_count(), "id": target}
        )

    async def api_refuse_bottle(self, bottle_id: str):
        """拒绝一个漂流瓶"""
        target = _safe_int(bottle_id, -1)
        if target < 0:
            return error_response("无效的漂流瓶 ID", status_code=400)
        if not self.store.refuse_bottle(target):
            return error_response("漂流瓶不存在", status_code=404)
        logger.info(f"审核拒绝漂流瓶 {target}")
        return json_response(
            {"ok": True, "pending": self.store.pending_count(), "id": target}
        )

    async def api_random_comment(self):
        """随机返回一条待审核评论"""
        row = self.store.random_pending_comment()
        if row is None:
            return json_response({"comment": None, "pending": 0})
        comment = {
            "comment_id": row.get("comment_id"),
            "bottle_id": row.get("id"),
            "content": row.get("content") or "",
            "username": (row.get("username") or "").strip()
            or self._cfg_str("default_nickname", "未知昵称"),
            "uid": row.get("uid") or "",
        }
        return json_response(
            {"comment": comment, "pending": self.store.pending_comment_count()}
        )

    async def api_approve_comment(self, comment_id: str):
        """通过一条评论"""
        target = _safe_int(comment_id, -1)
        if target < 0:
            return error_response("无效的评论 ID", status_code=400)
        if not self.store.set_comment_state(target, STATE_APPROVED):
            return error_response("评论不存在", status_code=404)
        logger.info(f"审核通过评论 {target}")
        return json_response(
            {"ok": True, "pending": self.store.pending_comment_count(), "id": target}
        )

    async def api_refuse_comment(self, comment_id: str):
        """拒绝一条评论"""
        target = _safe_int(comment_id, -1)
        if target < 0:
            return error_response("无效的评论 ID", status_code=400)
        if not self.store.set_comment_state(target, STATE_REJECTED):
            return error_response("评论不存在", status_code=404)
        logger.info(f"审核拒绝评论 {target}")
        return json_response(
            {"ok": True, "pending": self.store.pending_comment_count(), "id": target}
        )

    # ---------------------------- 卸载 ---------------------------- #
    async def terminate(self):
        """插件被停用 / 卸载时关闭数据库连接"""
        global _ACTIVE_PLUGIN
        if _ACTIVE_PLUGIN is self:
            _ACTIVE_PLUGIN = None
        self.store.close()
        logger.info("漂流瓶插件已卸载，数据库连接已关闭")
