"""App Bridge — 외부 비즈니스 앱 통합 인프라.

GSD 는 명령 라우팅 / whitelist / correlation 만 담당하고, 비즈니스 로직은
외부 앱이 처리한다. 두 가지 통합 모드 지원:

- file 모드: 외부 앱이 별도 프로세스로 실행. external_inbox/{app}/ 폴링.
- api  모드: 같은 Python 프로세스에서 핸들러 등록. AppBridge.register().
"""

from .router import AppRouter, RouteResult
from .command_writer import AppCommandWriter
from .correlator import AppResponseCorrelator, PendingCommand

__all__ = [
    "AppRouter",
    "RouteResult",
    "AppCommandWriter",
    "AppResponseCorrelator",
    "PendingCommand",
]
