import asyncio
import logging
import time
from typing import Callable, Awaitable

from .config import Config
from .channels.base import ChannelAdapter
from .channels.manager import ChannelManager
from .channels.telegram import TelegramAdapter
from .inbox_writer import InboxWriter, extract_keyword
from .outbox_sender import OutboxSender
from .inbox_processor import InboxProcessor
from .archiver import Archiver
from .api import ChannelSender

logger = logging.getLogger(__name__)


# on_result 콜백 타입: (source, request_text, response_text, status)
ResultCallback = Callable[[dict, str, str, str], Awaitable[None]]


class Orchestrator:
    """메인 오케스트레이터. 채널 어댑터와 백그라운드 태스크를 관리한다.

    단독 실행: run() 또는 await start() / await stop()
    연동 실행: on_result(callback) + channel_sender로 인터페이스 제공
    """

    def __init__(self, config: Config):
        self._config = config
        self._inbox_writer = InboxWriter(config.inbox_dir)
        self._result_callback: ResultCallback | None = None
        self._tasks: list[asyncio.Task] = []
        self._blocked_file = config.runtime_path("blocked")
        self._start_time = int(time.time())

        # 채널 어댑터 생성
        adapters: list[ChannelAdapter] = []

        runtime_paths = {
            name: config.runtime_path(name)
            for name in ("blocked", "token-usage", "reset", "cooldown",
                         "failcount", "cooldown-alerted", "gsd-active")
        }

        if config.telegram_enabled and config.telegram_bot_token:
            adapters.append(TelegramAdapter(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                runtime_paths=runtime_paths,
            ))

        if config.slack_enabled and config.slack_bot_token:
            try:
                from .channels.slack import SlackAdapter
                adapters.append(SlackAdapter(
                    bot_token=config.slack_bot_token,
                    app_token=config.slack_app_token,
                    channel_id=config.slack_channel_id,
                ))
            except ImportError as e:
                logger.error(f"Slack 어댑터 로드 실패: {e}")
                raise

        if not adapters:
            raise RuntimeError("최소 하나의 채널이 활성화되어야 합니다.")

        self._channel_manager = ChannelManager(adapters)

        self._inbox_processor = InboxProcessor(
            config, self._channel_manager, result_callback=self._fire_result)
        self._outbox_sender = OutboxSender(
            channel_manager=self._channel_manager,
            outbox_dir=config.outbox_dir,
            sent_dir=config.sent_dir,
            error_dir=config.error_dir,
            interval=config.outbox_interval,
            snippet_length=config.broadcast_snippet_length,
        )

        self._channel_sender = ChannelSender(self._channel_manager, config.outbox_dir)

    # ── 외부 연동 인터페이스 ──────────────────────────────

    @property
    def channel_sender(self) -> ChannelSender:
        """호스트 앱용 채널 발송 API."""
        return self._channel_sender

    def on_result(self, callback: ResultCallback) -> None:
        """처리 결과 콜백을 등록한다.

        callback(source, request_text, response_text, status)
        status: "success" | "error" | "blocked"
        """
        self._result_callback = callback
        # inbox_processor에도 반영
        self._inbox_processor.set_result_callback(self._fire_result)

    async def _fire_result(self, source: dict, request_text: str,
                           response_text: str, status: str) -> None:
        """등록된 콜백이 있으면 호출한다."""
        if self._result_callback:
            try:
                await self._result_callback(source, request_text, response_text, status)
            except Exception as e:
                logger.error(f"result_callback 에러: {e}")

    # ── 메시지 핸들링 ────────────────────────────────────

    async def _on_channel_message(self, source: dict, text: str,
                                  mode: str = "default") -> None:
        """모든 채널 어댑터의 공통 메시지 콜백."""
        logger.info(f"[수신] [{source.get('channel_type')}] {text}")
        pending = self._inbox_writer.pending_count()

        keyword = extract_keyword(text)
        header = f"[{source.get('channel_type', '')}][{source.get('user_name', '')}][{keyword}]"

        # 메시지 수신 지연 감지 (60초 이상)
        delay_notice = ""
        message_ts = source.get("message_ts", 0)
        if message_ts > 0:
            delay = int(time.time()) - message_ts
            if delay >= 60:
                minutes = delay // 60
                seconds = delay % 60
                if message_ts < self._start_time:
                    # 메시지가 프로세스 시작 전에 전송됨 → 앱 정지 중 밀린 메시지
                    delay_notice = f"\n(앱 정지 중 수신된 메시지입니다. {minutes}분 {seconds}초 전 전송)"
                else:
                    # 프로세스 실행 중 지연 → 네트워크 문제
                    delay_notice = f"\n(네트워크 불안정으로 수신이 {minutes}분 {seconds}초 지연되었습니다)"
                logger.warning(f"메시지 수신 지연: {delay}초 ({source.get('channel_type')})")

        # GSD 블로킹 상태에서 사용자 응답 → gsd-resume
        if mode == "default" and self._blocked_file.exists():
            self._inbox_writer.write(source, text, mode="gsd-resume")
            try:
                self._blocked_file.unlink()
            except OSError:
                pass
            if pending > 0:
                msg = f"{header} GSD 블로킹 응답 접수. (앞선 작업 {pending}건 처리 중)"
            else:
                msg = f"{header} GSD 블로킹 응답 접수."
            await self._channel_manager.send_to(
                source.get("channel_type", ""), source.get("channel_id", ""),
                msg + delay_notice)
            return

        # GSD 세션 활성 상태에서 사용자 응답 → gsd-resume (세션 이어가기)
        if mode == "default" and self._inbox_processor.is_gsd_active():
            self._inbox_writer.write(source, text, mode="gsd-resume")
            if pending > 0:
                msg = f"{header} GSD 작업 이어서 진행합니다. (앞선 작업 {pending}건 처리 중)"
            else:
                msg = f"{header} GSD 작업 이어서 진행합니다."
            await self._channel_manager.send_to(
                source.get("channel_type", ""), source.get("channel_id", ""),
                msg + delay_notice)
            return

        # inbox 저장 (delay_notice는 source에 포함하여 inbox_processor에서 활용)
        if delay_notice:
            source["delay_notice"] = delay_notice
        self._inbox_writer.write(source, text, mode=mode)

        # 수신 확인은 inbox_processor가 .processing 전환 시 발송

    # ── 백그라운드 태스크 ────────────────────────────────

    async def _inbox_stale_check(self):
        """inbox 파일 체류 시간 감시. 5분 이상 미처리 시 알림."""
        while True:
            try:
                for f in self._config.inbox_dir.glob("*.json"):
                    age = time.time() - f.stat().st_mtime
                    if age > 300:
                        await self._channel_manager.broadcast_all(
                            "[시스템] Inbox 메시지 미처리 경고: Claude Code 상태 확인 필요"
                        )
                        break
            except Exception as e:
                logger.error(f"inbox stale check 에러: {e}")
            await asyncio.sleep(60)

    async def _run_archiver(self):
        """매시간 archiver 실행."""
        archiver = Archiver(
            self._config.sent_dir,
            self._config.archive_dir,
            self._config.message_retention_days,
        )
        while True:
            try:
                archiver.run()
            except Exception as e:
                logger.error(f"archiver 에러: {e}")
            await asyncio.sleep(3600)

    # ── 진입점 ───────────────────────────────────────────

    def run(self) -> None:
        """블로킹 실행. asyncio.run() 내부 호출."""
        asyncio.run(self._run_blocking())

    async def _run_blocking(self):
        await self.start()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("종료 신호 수신")
        finally:
            await self.stop()

    async def start(self) -> None:
        """비동기 실행. 호스트 앱의 이벤트 루프에서 호출 가능."""
        logger.info("gsd-orchestrator 시작")

        # 백그라운드 태스크를 먼저 시작 (채널 어댑터의 long-polling이 루프를 점유하기 전)
        self._tasks = [
            asyncio.create_task(self._inbox_processor.run()),
            asyncio.create_task(self._outbox_sender.run()),
            asyncio.create_task(self._inbox_stale_check()),
            asyncio.create_task(self._run_archiver()),
        ]

        await self._channel_manager.start_all(self._on_channel_message)

    async def stop(self) -> None:
        """비동기 정지."""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        await self._channel_manager.stop_all()
        logger.info("gsd-orchestrator 종료")
