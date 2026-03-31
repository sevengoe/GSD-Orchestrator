import json
import logging
import time
from pathlib import Path
from typing import Callable, Awaitable

from telegram import Update
from telegram.error import (
    BadRequest, Forbidden, TimedOut, NetworkError, RetryAfter, TelegramError,
)
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from .base import ChannelAdapter
from ..attachment_handler import (
    validate_file, download_file_telegram, extract_text,
    build_metadata, cleanup_temp_file,
)
from ..text_cleaner import clean_text

logger = logging.getLogger(__name__)


class TelegramAdapter(ChannelAdapter):
    """Telegram 채널 어댑터. python-telegram-bot 기반."""

    def __init__(self, bot_token: str, chat_id: str,
                 runtime_paths: dict[str, Path] | None = None,
                 attachments_config: dict | None = None,
                 error_dir: Path | None = None,
                 outbox_dir: Path | None = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._app: Application | None = None
        self._on_message_callback: Callable[..., Awaitable[None]] | None = None
        # 첨부파일 설정
        att = attachments_config or {}
        self._att_allowed_ext: list[str] = att.get("allowed_extensions", ["txt", "md", "pdf"])
        self._att_max_size: int = att.get("max_file_size", 1_048_576)
        self._att_temp_dir: Path = att.get("temp_dir", Path("messages/attachments"))
        self._att_reject_msg: str = att.get("reject_message", "txt, md, pdf 파일만 지원합니다.")
        # error/outbox 디렉토리 (retry 커맨드용)
        self._error_dir = error_dir or Path("messages/error")
        self._outbox_dir = outbox_dir or Path("messages/outbox")
        # 인스턴스별 런타임 파일 경로
        paths = runtime_paths or {}
        self._blocked_file = paths.get("blocked", Path("/tmp/gsd-orchestrator.blocked"))
        self._token_track_file = paths.get("token-usage", Path("/tmp/gsd-orchestrator.token-usage"))
        self._reset_file = paths.get("reset", Path("/tmp/gsd-orchestrator.reset"))
        self._cooldown_file = paths.get("cooldown", Path("/tmp/gsd-orchestrator.cooldown"))
        self._fail_count_file = paths.get("failcount", Path("/tmp/gsd-orchestrator.failcount"))
        self._cooldown_alert_file = paths.get("cooldown-alerted", Path("/tmp/gsd-orchestrator.cooldown-alerted"))
        self._gsd_active_file = paths.get("gsd-active", Path("/tmp/gsd-orchestrator.gsd-active"))

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
        self._app.add_handler(CommandHandler("retry", self._on_retry))
        self._app.add_handler(CommandHandler("gsd", self._on_gsd))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        self._app.add_handler(
            MessageHandler(filters.Document.ALL, self._on_document)
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
        except Forbidden as e:
            logger.error(f"Telegram 발송 차단 (permanent) [{channel_id}]: {e}")
            return False
        except BadRequest as e:
            logger.error(f"Telegram 잘못된 요청 [{channel_id}]: {e}")
            return False
        except RetryAfter as e:
            logger.warning(
                f"Telegram 속도 제한 [{channel_id}]: {e.retry_after}초 후 재시도 필요"
            )
            return False
        except TimedOut as e:
            logger.warning(f"Telegram 타임아웃 (recoverable) [{channel_id}]: {e}")
            return False
        except NetworkError as e:
            logger.warning(f"Telegram 네트워크 에러 (recoverable) [{channel_id}]: {e}")
            return False
        except TelegramError as e:
            logger.error(f"Telegram API 에러 [{channel_id}]: {e}")
            return False
        except Exception as e:
            logger.error(f"Telegram 발송 실패 (unknown) [{channel_id}]: {type(e).__name__}: {e}")
            return False

    def _build_source(self, update: Update) -> dict:
        user = update.effective_user
        chat = update.effective_chat
        # message.date: 원본 메시지 전송 시각 (UTC datetime)
        message_ts = 0
        if update.message and update.message.date:
            message_ts = int(update.message.date.timestamp())
        return {
            "channel_type": "telegram",
            "channel_id": str(chat.id),
            "user_id": str(user.id) if user else "",
            "user_name": (user.full_name or user.username or str(user.id)) if user else "",
            "message_id": update.message.message_id if update.message else 0,
            "message_ts": message_ts,
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

    async def _on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """첨부파일(Document) 수신 핸들러."""
        if not update.message or not update.message.document:
            return
        if not self._is_allowed(update):
            return
        user = update.effective_user
        if user and user.is_bot:
            return

        doc = update.message.document
        file_name = doc.file_name or "unknown"
        file_size = doc.file_size or 0

        # 화이트리스트 / 크기 검증
        reject = validate_file(
            file_name, file_size,
            self._att_allowed_ext, self._att_max_size, self._att_reject_msg,
        )
        if reject:
            await update.message.reply_text(reject)
            return

        # 다운로드 → 텍스트 추출 → 정제
        local_path = None
        try:
            local_path = await download_file_telegram(
                self._app.bot, doc.file_id, self._att_temp_dir,
            )
            raw_text = extract_text(local_path, file_name)

            if raw_text.startswith("[오류]"):
                await update.message.reply_text(raw_text)
                return

            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
            cleaned_text, summary = clean_text(raw_text, ext)

            # 확인 메시지 발송
            caption = update.message.caption or ""
            preview = cleaned_text[:1000]
            if len(cleaned_text) > 1000:
                preview += "\n… (이하 생략)"
            confirm_msg = (
                f"📎 {file_name} — 텍스트 추출 완료\n"
                f"(원본 {summary['original_length']:,}자 → 정제 후 {summary['cleaned_length']:,}자)\n\n"
                f"{preview}\n\n"
                f"아래 내용이 맞는지 확인해주세요. 이어서 메시지를 보내시면 해당 내용과 함께 처리됩니다."
            )
            await update.message.reply_text(confirm_msg)

            # source에 메타데이터 포함하여 콜백 호출
            source = self._build_source(update)
            metadata = build_metadata(file_name, file_size)
            source["attachments"] = [metadata]

            text = caption if caption else f"[첨부파일: {file_name}]"
            if self._on_message_callback:
                await self._on_message_callback(
                    source=source, text=text, extracted_text=cleaned_text,
                )
        except Exception as e:
            logger.error(f"첨부파일 처리 실패 [{file_name}]: {e}")
            await update.message.reply_text(f"첨부파일 처리 중 오류가 발생했습니다: {e}")
        finally:
            if local_path:
                cleanup_temp_file(local_path)

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
            "/retry — 발송 실패 메시지 재발송\n"
            "  /retry (목록) | /retry all (전체) | /retry 번호\n"
            "\n"
            "일반 메시지 — 자동 분류 후 Simple Track 또는 GSD Track으로 라우팅"
        )
        await update.message.reply_text(help_text)

    async def _on_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        self._reset_file.touch()
        self._gsd_active_file.unlink(missing_ok=True)
        await update.message.reply_text("다음 요청부터 새 세션으로 시작합니다.")

    async def _on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return

        lines = []

        if self._cooldown_file.exists():
            try:
                resume_at = int(self._cooldown_file.read_text().strip())
                remaining = max(0, resume_at - int(time.time()))
                lines.append(f"쿨다운 중: {remaining // 60}분 후 재개")
            except (ValueError, OSError):
                lines.append("쿨다운 상태: 확인 불가")
        else:
            lines.append("상태: 정상 운영 중")

        if self._blocked_file.exists():
            lines.append("GSD 블로킹: 사용자 판단 대기 중")

        if self._token_track_file.exists():
            try:
                token_data = json.loads(self._token_track_file.read_text())
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

        removed = []
        for f in [self._cooldown_file, self._fail_count_file, self._cooldown_alert_file]:
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

    async def _on_retry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """error/ 디렉토리의 실패 메시지를 outbox/로 복원하여 재발송한다."""
        if not self._is_allowed(update):
            return

        arg = update.message.text.replace("/retry", "", 1).strip()
        error_files = sorted(self._error_dir.glob("*.json"))

        if not error_files:
            await update.message.reply_text("error/에 재발송 대상이 없습니다.")
            return

        # /retry (인자 없음): 목록 표시
        if not arg:
            lines = [f"발송 실패 메시지 ({len(error_files)}건):"]
            for idx, f in enumerate(error_files, 1):
                try:
                    data = json.loads(f.read_text())
                    req = data.get("request", {})
                    req_text = req.get("text", "") if isinstance(req, dict) else ""
                    preview = req_text[:40] or data.get("response", {}).get("text", "")[:40]
                except (json.JSONDecodeError, OSError):
                    preview = "(읽기 실패)"
                lines.append(f"  {idx}. {f.name}\n      {preview}")
            lines.append("\n/retry all — 전체 재발송\n/retry <번호> — 개별 재발송")
            await update.message.reply_text("\n".join(lines))
            return

        # /retry all: 전체 복원
        if arg.lower() == "all":
            restored = 0
            for f in error_files:
                try:
                    data = json.loads(f.read_text())
                    data["retry_count"] = 0
                    # targets가 비어있으면 원본 source에서 복원
                    if not data.get("targets"):
                        source = data.get("source", {})
                        ch_type = source.get("channel_type", "telegram")
                        ch_id = source.get("channel_id", "")
                        data["targets"] = [{"channel_type": ch_type, "channel_id": ch_id, "is_origin": True}]
                    tmp = self._outbox_dir / f".{f.name}.tmp"
                    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                    tmp.rename(self._outbox_dir / f.name)
                    f.unlink()
                    restored += 1
                except (json.JSONDecodeError, OSError) as e:
                    logger.error(f"retry 복원 실패: {f.name} — {e}")
            await update.message.reply_text(f"{restored}/{len(error_files)}건 outbox로 복원 완료.")
            return

        # /retry <번호>: 개별 복원
        try:
            idx = int(arg) - 1
            if idx < 0 or idx >= len(error_files):
                raise ValueError
        except ValueError:
            await update.message.reply_text(f"유효하지 않은 번호입니다. 1~{len(error_files)} 범위로 입력하세요.")
            return

        f = error_files[idx]
        try:
            data = json.loads(f.read_text())
            data["retry_count"] = 0
            if not data.get("targets"):
                source = data.get("source", {})
                ch_type = source.get("channel_type", "telegram")
                ch_id = source.get("channel_id", "")
                data["targets"] = [{"channel_type": ch_type, "channel_id": ch_id, "is_origin": True}]
            tmp = self._outbox_dir / f".{f.name}.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            tmp.rename(self._outbox_dir / f.name)
            f.unlink()
            await update.message.reply_text(f"복원 완료: {f.name}")
        except (json.JSONDecodeError, OSError) as e:
            await update.message.reply_text(f"복원 실패: {f.name} — {e}")

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
