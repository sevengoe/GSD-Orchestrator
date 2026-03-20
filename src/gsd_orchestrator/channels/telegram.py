import json
import logging
import time
from pathlib import Path
from typing import Callable, Awaitable

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from .base import ChannelAdapter

logger = logging.getLogger(__name__)

BLOCKED_FILE = Path("/tmp/gsd-orchestrator.blocked")
TOKEN_TRACK_FILE = Path("/tmp/gsd-orchestrator.token-usage")


class TelegramAdapter(ChannelAdapter):
    """Telegram 채널 어댑터. python-telegram-bot 기반."""

    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._app: Application | None = None
        self._on_message_callback: Callable[..., Awaitable[None]] | None = None

    @property
    def channel_type(self) -> str:
        return "telegram"

    @property
    def max_message_length(self) -> int:
        return 4096

    def get_channel_id(self) -> str:
        return self._chat_id

    async def start(self, on_message: Callable[..., Awaitable[None]]) -> None:
        self._on_message_callback = on_message
        self._app = (
            Application.builder()
            .token(self._bot_token)
            .get_updates_read_timeout(2)
            .get_updates_connect_timeout(5)
            .build()
        )

        self._app.add_handler(CommandHandler("help", self._on_help))
        self._app.add_handler(CommandHandler("start", self._on_help))
        self._app.add_handler(CommandHandler("reset", self._on_reset))
        self._app.add_handler(CommandHandler("status", self._on_status))
        self._app.add_handler(CommandHandler("resume", self._on_resume))
        self._app.add_handler(CommandHandler("gsd", self._on_gsd))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            poll_interval=1.0,
            drop_pending_updates=True,
        )
        logger.info(f"TelegramAdapter 시작 (chat_id: {self._chat_id})")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_message(self, channel_id: str, text: str,
                           parse_mode: str | None = None) -> bool:
        """메시지 발송. 4096자 초과 시 내부 분할."""
        if not self._app:
            return False
        try:
            max_len = self.max_message_length
            if len(text) <= max_len:
                await self._app.bot.send_message(
                    chat_id=channel_id, text=text, parse_mode=parse_mode,
                )
            else:
                for i in range(0, len(text), max_len):
                    chunk = text[i:i + max_len]
                    await self._app.bot.send_message(
                        chat_id=channel_id, text=chunk, parse_mode=parse_mode,
                    )
            return True
        except Exception as e:
            logger.error(f"Telegram 발송 실패 [{channel_id}]: {e}")
            return False

    def _build_source(self, update: Update) -> dict:
        user = update.effective_user
        chat = update.effective_chat
        return {
            "channel_type": "telegram",
            "channel_id": str(chat.id),
            "user_id": str(user.id) if user else "",
            "user_name": (user.full_name or user.username or str(user.id)) if user else "",
            "message_id": update.message.message_id if update.message else 0,
            "thread_ts": None,
        }

    def _is_allowed(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self._chat_id

    # ── 메시지 핸들러 ──────────────────────────────────────

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        if not self._is_allowed(update):
            return
        user = update.effective_user
        if user and user.is_bot:
            return

        source = self._build_source(update)
        text = update.message.text

        if self._on_message_callback:
            await self._on_message_callback(source=source, text=text)

    # ── 슬래시 커맨드 ──────────────────────────────────────

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        help_text = (
            "GSD Orchestrator 명령어\n"
            "\n"
            "/help — 이 도움말\n"
            "/gsd <작업> — GSD 워크플로우로 작업 실행\n"
            "  (계획 수립 → 확인 → 분류 단위별 분석/설계/구현/테스트)\n"
            "/status — 상태 확인 (대기 건수, 쿨다운, 토큰 사용량)\n"
            "/reset — 다음 요청부터 새 세션으로 시작\n"
            "/resume — 쿨다운 즉시 해제\n"
            "\n"
            "일반 메시지 — 자동 분류 후 Simple Track 또는 GSD Track으로 라우팅"
        )
        await update.message.reply_text(help_text)

    async def _on_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        Path("/tmp/gsd-orchestrator.reset").touch()
        await update.message.reply_text("다음 요청부터 새 세션으로 시작합니다.")

    async def _on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return

        cooldown_file = Path("/tmp/gsd-orchestrator.cooldown")
        lines = []

        if cooldown_file.exists():
            try:
                resume_at = int(cooldown_file.read_text().strip())
                remaining = max(0, resume_at - int(time.time()))
                lines.append(f"쿨다운 중: {remaining // 60}분 후 재개")
            except (ValueError, OSError):
                lines.append("쿨다운 상태: 확인 불가")
        else:
            lines.append("상태: 정상 운영 중")

        if BLOCKED_FILE.exists():
            lines.append("GSD 블로킹: 사용자 판단 대기 중")

        if TOKEN_TRACK_FILE.exists():
            try:
                token_data = json.loads(TOKEN_TRACK_FILE.read_text())
                total_in = token_data.get("input_tokens", 0)
                total_out = token_data.get("output_tokens", 0)
                total_cost = token_data.get("total_cost_usd", 0)
                call_count = token_data.get("call_count", 0)
                last = token_data.get("last_call", {})
                lines.append(f"토큰: in {total_in:,} / out {total_out:,} ({call_count}회)")
                lines.append(f"비용: ${total_cost:.4f}")
                if last:
                    lines.append(
                        f"최근: in {last.get('input_tokens', 0):,} / "
                        f"out {last.get('output_tokens', 0):,} "
                        f"(${last.get('cost_usd', 0):.4f})"
                    )
            except (json.JSONDecodeError, ValueError, OSError):
                pass

        await update.message.reply_text("\n".join(lines))

    async def _on_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return

        cooldown_file = Path("/tmp/gsd-orchestrator.cooldown")
        failcount_file = Path("/tmp/gsd-orchestrator.failcount")
        cooldown_alert_file = Path("/tmp/gsd-orchestrator.cooldown-alerted")

        removed = []
        for f in [cooldown_file, failcount_file, cooldown_alert_file]:
            if f.exists():
                try:
                    f.unlink()
                    removed.append(f.name)
                except OSError:
                    pass

        if removed:
            reply = "쿨다운 해제 완료. 다음 요청부터 즉시 처리됩니다."
        else:
            reply = "쿨다운 상태가 아닙니다. 정상 운영 중입니다."
        await update.message.reply_text(reply)

    async def _on_gsd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return

        text = update.message.text.replace("/gsd", "", 1).strip()
        if not text:
            await update.message.reply_text("사용법: /gsd <작업 내용>")
            return

        source = self._build_source(update)
        if self._on_message_callback:
            await self._on_message_callback(source=source, text=text, mode="gsd")
