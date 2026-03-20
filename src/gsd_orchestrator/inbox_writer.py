import json
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))


def extract_keyword(text: str, max_len: int = 20) -> str:
    """사용자 메시지에서 핵심 키워드를 추출한다."""
    cleaned = re.sub(r"[^\w가-힣a-zA-Z0-9]", "", text)
    return cleaned[:max_len] if cleaned else "메시지"


class InboxWriter:
    def __init__(self, inbox_dir: Path):
        self._dir = inbox_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def write(self, source: dict | str, message_id_or_text: int | str = 0,
              text: str = "", mode: str = "default") -> Path:
        """inbox에 메시지를 원자적으로 저장한다.

        새 형식: write(source_dict, text, mode=...)
        하위 호환: write(chat_id_str, message_id_int, text_str, mode=...)
        """
        if isinstance(source, str):
            # 하위 호환: (chat_id, message_id, text, mode)
            chat_id = source
            message_id = message_id_or_text
            actual_text = text
            source_obj = {
                "channel_type": "telegram",
                "channel_id": chat_id,
                "user_id": chat_id,
                "user_name": chat_id,
                "message_id": message_id,
                "thread_ts": None,
            }
        else:
            # 새 형식: (source_dict, text, mode=...)
            source_obj = source
            actual_text = str(message_id_or_text) if message_id_or_text else text
            if not actual_text:
                actual_text = text

        now = datetime.now(KST)
        short_id = uuid.uuid4().hex[:8]
        keyword = extract_keyword(actual_text)
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}.json"

        data = {
            "id": str(uuid.uuid4()),
            "source": source_obj,
            # 하위 호환 필드
            "chat_id": source_obj.get("channel_id", ""),
            "message_id": source_obj.get("message_id", 0),
            "keyword": keyword,
            "mode": mode,
            "request": {
                "text": actual_text,
                "timestamp": now.isoformat(),
            },
            "response": None,
        }

        tmp = self._dir / f".{filename}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        target = self._dir / filename
        tmp.rename(target)
        return target

    def pending_count(self) -> int:
        """inbox에 대기 중인 파일 수를 반환한다."""
        return len(list(self._dir.glob("*.json")))
