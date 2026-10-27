# 설치

## Windows에서 처음 설치

저장소 전체를 내려받아 압축을 푼 후 `START.cmd`를 더블클릭합니다. **Python을 미리 설치할 필요가 없습니다.**
최초 실행에는 인터넷 연결이 필요하며 약 1 GB를 내려받습니다(5–15분 정도 걸리며, 창을 닫으면 안 됩니다).
필요한 디스크 여유 공간은 약 3 GB입니다.
START가 아래 항목을 모두 DARA 폴더 안에 설치하고, 마지막에 이 저장소의 맞춤 DARA를 실행합니다.

- `tools\uv\`: 버전이 고정된 uv (SHA-256 검증)
- `tools\python\`: CPython 3.12.15 (시스템 전역 설치, PATH, 레지스트리는 변경하지 않습니다)
- `.venv\`: 프로젝트 가상 환경과 패키지 (`scripts\constraints-py312-win.txt`의 검증된 버전 사용)
- BGMN 정제 프로그램: `src\dara\bgmn\BGMNwin`에 없을 때만 내려받으며, 매 실행마다 확인합니다

폴더 경로는 짧게(예: `C:\DARA`) 두고 OneDrive 등 동기화 폴더는 피하세요. 작은 파일이 수만 개 생성되며,
경로가 너무 길면 설치가 실패할 수 있습니다. 이후 실행은 설치를 건너뛰고 약 1초 만에 서버를 시작합니다.
인터넷이 없으면 이미 설치된 환경은 업데이트 확인 없이 그대로 시작합니다.

지원하는 패턴 파일: .xrdml, .xy, .xye, .txt, .csv, .dat, .asc, .scn, .ras, .rasx, .brml, .raw (Bruker RAW v2만 지원).

### START 스위치

`START.cmd` 뒤에 붙여 사용합니다. 예: `START.cmd -Reinstall`

| 스위치 | 동작 |
| --- | --- |
| `-Reinstall` | `.venv`를 새로 만들고 패키지를 다시 설치합니다. `tools\`와 다운로드 캐시는 유지하므로 오프라인에서도 가능합니다 |
| `-SetupOnly` | 설치·검증만 하고 서버는 시작하지 않습니다 |
| `-Offline` | 네트워크를 사용하지 않습니다. 필요한 것이 없으면 명확한 메시지와 함께 중단합니다 |
| `-PythonExe <경로>` | 전문가용: 지정한 Python을 그대로 사용하고 설치를 건너뜁니다(환경 변수 `DARA_PYTHON`도 동일). DARA 의존 패키지가 이미 있어야 합니다 |
| `-NoBrowser` | 브라우저를 열지 않습니다 |
| `-SkipApiKeySetup` | Materials Project API 키 입력 창을 표시하지 않습니다 |
| `-StartupTimeoutSeconds <초>` | 서버 시작 대기 시간(기본 180) |

### 종료 코드와 로그

| 코드 | 의미 |
| --- | --- |
| 0 | 정상 시작·종료, 또는 `-SetupOnly` 완료 |
| 1 | 예기치 않은 오류, 서버 시작 실패, 또는 다른 START 창에서 설치 진행 중 |
| 2 | 인터넷이 없고 사용할 수 있는 환경도 없음 |
| 3 | BGMN 프로그램이 없고 내려받지 못함 |
| 4 | uv 다운로드 실패 또는 SHA-256 검증 실패 |
| 5 | Python 3.12를 설치하거나 찾지 못함 |
| 6 | 패키지 설치 실패 |
| 7 | 설치된 환경의 import 검증 실패 |

설치 과정은 `artifacts\launcher\setup-날짜-시간.log`에 기록됩니다(최근 10개 보관).

### 문제 해결과 초기화

- 설치가 중단되면(창 닫기, 네트워크 오류 등) START를 다시 실행하세요. 이미 받은 파일을 재사용하며 이어서 진행합니다.
- DARA 폴더를 이동하거나 복사하면 START가 이를 감지하고 환경을 자동으로 다시 만듭니다. `tools\uv-cache`가 온전하면 인터넷 없이도 가능합니다(연결이 없을 때는 `START.cmd -Offline`).
- 환경을 초기화하려면 `.venv` 폴더를 삭제하거나 `START.cmd -Reinstall`을 실행하세요. 모든 것을 다시 내려받으려면 `tools\`도 삭제합니다.

## 개발용 설치 — Optional(선택)

별도의 Python 3.10 이상 가상환경을 활성화한 상태에서 저장소 루트에서 실행합니다(START와는 별개입니다).

```console
uv pip install -e . -c scripts\constraints-py312-win.txt
dara server
```

`uv`가 없으면 `python -m pip install -e .`로도 설치할 수 있습니다.

프런트엔드 개발 방법은 `src/dara/server/ui/README.md`에 있습니다.

## 원본 PyPI 배포판 — Optional(선택)

```console
python -m pip install dara-xrd
```

이 명령만으로는 이 저장소의 맞춤 변경사항이 설치되지 않습니다.
맞춤 버전은 START 또는 위의 개발용 설치 절차를 사용하세요.
추가 플랫폼 안내는 [원본 공식 설치 문서](https://CederGroupHub.github.io/dara/install.html)에 있습니다.
