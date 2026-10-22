# 내부 실행 및 개발 스크립트

일반 사용자는 저장소 루트의 **START.cmd**만 실행합니다.
검증·데이터 준비 도구는 Optional(선택)이며 저장소 루트에서 실행합니다.

| 파일 | 용도 |
| --- | --- |
| `start_dara_web.ps1` | START에서 호출하는 설치·시작 처리 |
| `launch_dara_web.py` | 시작 진행 페이지, 서버 실행과 브라우저 연결 |
| `materials_project_key.ps1` | 최초 MP API 키 입력과 저장된 선택 재사용 |
| `serve_workflow_qa.py` | 8899 포트의 합성 데이터 UI 검증 서버 |
| `check_workflow_browser.mjs` | 위 검증 서버의 화면 흐름 확인; Node.js와 스크립트에 지정된 Chrome 설치 필요 |
| `smoke_worker_controls.py` | 임시 DB에서 작업 완료·중단 확인 |
| `worker_fixture_job.py` | 작업 제어 검증에 사용하는 합성 작업 |
| `filter_cod.py`, `filter_icsd.py` | 로컬 구조 DB에서 후보 색인을 재생성하는 유지보수 도구 |
| `run_binary_reaction.py` | 논문 데이터 배치 분석; 로컬 ICSD 설정 필요 |

DB 필터는 `src/dara/data/mp_struct_info.json.gz`와 설정된 로컬 구조 DB를 사용합니다.
일반 분석에는 포함된 색인을 사용하므로 재생성이 필요하지 않습니다.
ICSD 도구는 데이터 이용 권한과 로컬 CIF가 있을 때 사용합니다.
검증 출력은 `artifacts/`, 배치 결과는 `benchmarks/`에 저장되며 Git에서 제외됩니다.
