import asyncio
import calendar
import hashlib
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_luguan"
DEER_EMOJI = "🦌"


@register(
    PLUGIN_NAME, "luguan", "Record 🦌 counts and publish monthly rankings.", "1.0.0"
)
class LuguanPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.plugin_dir = Path(__file__).resolve().parent
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.generated_dir = self.data_dir / "generated"
        self.db_file = self.data_dir / "luguan_data.json"
        self.legacy_db_file = self.data_dir / "luguan_stats.json"
        self.luguan_image = self.plugin_dir / "luguan.png"
        self._lock = asyncio.Lock()
        self._monthly_task: asyncio.Task | None = None

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.db = self._load_db()

    async def initialize(self):
        self._monthly_task = asyncio.create_task(self._month_end_loop())
        logger.info("Luguan plugin initialized.")

    async def terminate(self):
        if self._monthly_task:
            self._monthly_task.cancel()
            try:
                await self._monthly_task
            except asyncio.CancelledError:
                pass
        self._save_db()
        logger.info("Luguan plugin terminated.")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def record_luguan(self, event: AstrMessageEvent):
        count = event.message_str.count(DEER_EMOJI)
        if count <= 0:
            return

        try:
            today = datetime.now().astimezone().date()
            async with self._lock:
                self._add_count(event, today, count)
                self._save_db()

            image_path = self._render_calendar(event, today)
            yield event.make_result().file_image(str(image_path))
        except Exception as exc:
            logger.error(f"Luguan record failed: {exc}", exc_info=True)
            yield event.plain_result("🦌记录失败了，请稍后再试。")

    def _default_db(self) -> dict[str, Any]:
        return {"version": 1, "groups": {}, "last_ranked_months": {}}

    def _load_db(self) -> dict[str, Any]:
        if not self.db_file.exists():
            legacy_db = self._load_legacy_db()
            if legacy_db:
                self._write_db(legacy_db)
                return legacy_db

            db = self._default_db()
            self._write_db(db)
            return db

        try:
            loaded = json.loads(self.db_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"Luguan data file is invalid, reset to default: {exc}")
            loaded = self._default_db()

        loaded.setdefault("version", 1)
        loaded.setdefault("groups", {})
        loaded.setdefault("last_ranked_months", {})
        return loaded

    def _load_legacy_db(self) -> dict[str, Any] | None:
        if not self.legacy_db_file.exists():
            return None

        try:
            legacy = json.loads(self.legacy_db_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"Luguan legacy data file is invalid, skip migration: {exc}")
            return None

        migrated = self._default_db()
        for group_key, group in legacy.get("sessions", {}).items():
            migrated["groups"][group_key] = {
                "session": group.get("umo", ""),
                "group_id": group.get("group_id", ""),
                "is_group": bool(group.get("is_group", False)),
                "users": group.get("users", {}),
            }
        migrated["last_ranked_months"] = legacy.get("month_end_sent", {})
        return migrated

    def _save_db(self) -> None:
        self._write_db(self.db)

    def _write_db(self, db: dict[str, Any]) -> None:
        tmp_file = self.db_file.with_suffix(".json.tmp")
        tmp_file.write_text(
            json.dumps(db, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_file.replace(self.db_file)

    def _add_count(self, event: AstrMessageEvent, day: date, count: int) -> None:
        group_key = self._group_key(event)
        user_id = event.get_sender_id() or "unknown"
        group = self._ensure_group(group_key, event)
        user = group["users"].setdefault(user_id, {"name": "", "days": {}})
        user["name"] = event.get_sender_name() or user.get("name") or user_id
        user.setdefault("days", {})
        day_key = day.isoformat()
        user["days"][day_key] = int(user["days"].get(day_key, 0)) + count

    def _ensure_group(self, group_key: str, event: AstrMessageEvent) -> dict[str, Any]:
        groups = self.db.setdefault("groups", {})
        group = groups.setdefault(
            group_key,
            {
                "session": event.unified_msg_origin,
                "group_id": event.get_group_id(),
                "is_group": not event.is_private_chat(),
                "users": {},
            },
        )
        group["session"] = event.unified_msg_origin
        group["group_id"] = event.get_group_id()
        group["is_group"] = not event.is_private_chat()
        group.setdefault("users", {})
        return group

    def _group_key(self, event: AstrMessageEvent) -> str:
        group_id = event.get_group_id()
        if group_id:
            return f"{event.get_platform_id()}:{group_id}"
        return event.unified_msg_origin

    def _render_calendar(self, event: AstrMessageEvent, current_day: date) -> Path:
        group = self.db["groups"][self._group_key(event)]
        user_id = event.get_sender_id() or "unknown"
        user = group["users"][user_id]
        year = current_day.year
        month = current_day.month
        month_counts = self._month_counts(user, year, month)
        safe_user = self._safe_hash(user_id)
        safe_group = self._safe_hash(self._group_key(event))
        output_path = self.generated_dir / (
            f"calendar_{year}_{month:02d}_{safe_group}_{safe_user}.png"
        )

        width = 980
        height = 790
        margin = 34
        header_h = 112
        weekday_h = 42
        cell_w = (width - margin * 2) // 7
        cell_h = (height - margin * 2 - header_h - weekday_h) // 6

        bg = (250, 250, 247)
        line = (218, 216, 208)
        text = (45, 45, 42)
        muted = (132, 128, 120)
        accent = (148, 90, 54)

        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)
        title_font = self._font(42, bold=True)
        label_font = self._font(20)
        day_font = self._font(22, bold=True)
        count_font = self._font(24, bold=True)

        display_name = user.get("name") or user_id
        title = f"{display_name} {year}-{month:02d} 🦌 Calendar"
        draw.text((margin, 28), title, fill=text, font=title_font)
        total = sum(month_counts.values())
        draw.text((margin, 78), f"Monthly total: {total}", fill=accent, font=label_font)

        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        grid_top = margin + header_h
        for idx, weekday in enumerate(weekdays):
            x = margin + idx * cell_w
            draw.text((x + 12, grid_top), weekday, fill=muted, font=label_font)

        deer_img = self._load_deer_image()
        month_grid = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
        for row_index in range(6):
            week = month_grid[row_index] if row_index < len(month_grid) else [0] * 7
            for col_index, day_num in enumerate(week):
                x0 = margin + col_index * cell_w
                y0 = grid_top + weekday_h + row_index * cell_h
                x1 = x0 + cell_w
                y1 = y0 + cell_h
                draw.rounded_rectangle(
                    (x0 + 3, y0 + 3, x1 - 3, y1 - 3),
                    radius=8,
                    outline=line,
                    width=1,
                    fill=(255, 255, 252),
                )
                if not day_num:
                    continue

                draw.text((x0 + 13, y0 + 10), str(day_num), fill=text, font=day_font)
                count = month_counts.get(day_num, 0)
                if count <= 0:
                    continue

                paste_img = deer_img.copy()
                max_icon = min(cell_w - 44, cell_h - 52)
                paste_img.thumbnail((max_icon, max_icon), Image.Resampling.LANCZOS)
                px = x0 + (cell_w - paste_img.width) // 2
                py = y0 + 38
                mask = paste_img if paste_img.mode == "RGBA" else None
                image.paste(paste_img, (px, py), mask)
                count_label = f"x{count}"
                bbox = draw.textbbox((0, 0), count_label, font=count_font)
                draw.text(
                    (x0 + (cell_w - (bbox[2] - bbox[0])) // 2, y1 - 35),
                    count_label,
                    fill=accent,
                    font=count_font,
                )

        image.save(output_path, "PNG")
        return output_path

    def _load_deer_image(self) -> Image.Image:
        try:
            if self.luguan_image.exists():
                return Image.open(self.luguan_image).convert("RGBA")
        except Exception as exc:
            logger.error(f"Luguan image load failed: {exc}", exc_info=True)

        fallback = Image.new("RGBA", (160, 160), (255, 255, 255, 0))
        draw = ImageDraw.Draw(fallback)
        font = self._font(80, emoji=True)
        draw.text((32, 26), DEER_EMOJI, fill=(120, 75, 45), font=font)
        return fallback

    def _month_counts(
        self, user: dict[str, Any], year: int, month: int
    ) -> dict[int, int]:
        prefix = f"{year}-{month:02d}-"
        counts: dict[int, int] = {}
        for day_key, count in user.get("days", {}).items():
            if not day_key.startswith(prefix):
                continue
            try:
                day_num = int(day_key[-2:])
            except ValueError:
                continue
            counts[day_num] = int(count)
        return counts

    async def _month_end_loop(self) -> None:
        await self._send_previous_month_rankings_if_needed()
        while True:
            try:
                now = datetime.now().astimezone()
                next_run = datetime.combine(
                    now.date() + timedelta(days=1),
                    time(hour=0, minute=5),
                    tzinfo=now.tzinfo,
                )
                await asyncio.sleep(max(60, (next_run - now).total_seconds()))
                await self._send_previous_month_rankings_if_needed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Luguan month-end loop failed: {exc}", exc_info=True)
                await asyncio.sleep(300)

    async def _send_previous_month_rankings_if_needed(self) -> None:
        today = datetime.now().astimezone().date()
        target_day = today - timedelta(days=1)
        if not self._is_last_day_of_month(target_day):
            return

        month_key = f"{target_day.year}-{target_day.month:02d}"
        async with self._lock:
            if self.db.setdefault("last_ranked_months", {}).get(month_key):
                return

            messages = self._build_rank_messages(target_day.year, target_day.month)
            self.db["last_ranked_months"][month_key] = True
            self._save_db()

        for session, text in messages:
            try:
                await self.context.send_message(session, MessageChain().message(text))
            except Exception as exc:
                logger.error(
                    f"Luguan monthly ranking send failed for {session}: {exc}",
                    exc_info=True,
                )

    def _build_rank_messages(self, year: int, month: int) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        for group in self.db.get("groups", {}).values():
            if not group.get("is_group"):
                continue

            ranks = []
            for uid, user in group.get("users", {}).items():
                total = sum(self._month_counts(user, year, month).values())
                if total > 0:
                    ranks.append((total, user.get("name") or uid, uid))

            if not ranks:
                continue

            ranks.sort(key=lambda item: (-item[0], item[1], item[2]))
            lines = [f"{year}-{month:02d} 🦌排行榜"]
            for index, (total, name, uid) in enumerate(ranks[:20], start=1):
                lines.append(f"{index}. {name}({uid}) - {total}次")
            messages.append((group["session"], "\n".join(lines)))
        return messages

    def _is_last_day_of_month(self, day: date) -> bool:
        return (day + timedelta(days=1)).month != day.month

    def _safe_hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _font(
        self,
        size: int,
        *,
        bold: bool = False,
        emoji: bool = False,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_candidates = []
        if emoji:
            font_candidates.append(Path("C:/Windows/Fonts/seguiemj.ttf"))
        if bold:
            font_candidates.extend(
                [
                    Path("C:/Windows/Fonts/msyhbd.ttc"),
                    Path("C:/Windows/Fonts/simhei.ttf"),
                    Path("C:/Windows/Fonts/arialbd.ttf"),
                ],
            )
        font_candidates.extend(
            [
                Path("C:/Windows/Fonts/msyh.ttc"),
                Path("C:/Windows/Fonts/simsun.ttc"),
                Path("C:/Windows/Fonts/arial.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            ],
        )

        for font_path in font_candidates:
            try:
                if font_path.exists():
                    return ImageFont.truetype(str(font_path), size)
            except Exception:
                continue
        return ImageFont.load_default()
