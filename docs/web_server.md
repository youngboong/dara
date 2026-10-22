# 웹 서버 사용

설치와 화면별 조작은 [한글 사용 설명서 PDF](user-guide/DARA_사용설명서_한글.pdf),
고급 기능의 동작과 제한은 [실무 분석 작업 흐름](practical-workflows.md)을 참고하세요.

## 기본 실행

저장소 루트의 `START.cmd`를 실행하면 최초 API 키 안내, 필요한 패키지 설치,
서버 시작과 브라우저 열기가 이어집니다. 기본 주소는 <http://localhost:8898>입니다.
API 키는 Optional(선택)이며, 키 없이 사용할 때에는 Submit에서 MP를 끄고 COD를 켭니다.
사용 중에는 START 창을 열어 두고, 종료할 때에는 그 창에서 `Ctrl+C`를 누릅니다.
시작 로그는 `artifacts/launcher/`에 저장됩니다.

## 개발자용 직접 실행 — Optional(선택)

설치된 DARA 환경에서 실행합니다.

```console
dara server
```

서버 옵션은 `dara server --help`에서 확인할 수 있습니다.
설정 정의는 `src/dara/server/setting.py`가 기준입니다.
기본값은 호스트 `127.0.0.1`, 포트 `8898`, 파일 기반 MontyDB입니다.
기본 DB 위치는 사용자 홈의 `.dara-server/montydb`이며 별도 MongoDB 설치는 필요하지 않습니다.
환경변수 설정에는 `DARA_SERVER_` 접두사를 사용합니다.

API 요청 형식은 실행 중인 서버의 <http://localhost:8898/docs>에서 확인하세요.
