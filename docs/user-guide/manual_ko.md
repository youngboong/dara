# DARA 한글 사용 설명서

분말 XRD 상 동정과 Rietveld 정밀화를 자동으로 수행하는 웹 프로그램

> 기준 버전: 저장소 main 브랜치(2026-10-28). 저장소 [youngboong/dara](https://github.com/youngboong/dara).
> 화면 문구는 앱의 영어 표기를 그대로 적음. 예: Submit, Results, High confidence.

| 장 | 내용 |
| --- | --- |
| 01 개요 | DARA의 용도, 사용 대상, 화면 구성 |
| 02 준비물 | 필수·선택 준비물, MP API 키, GEQ, CIF 확보 방법 |
| 03 설치와 실행 | START.cmd, 첫 실행 과정, 자동 종료, 여러 창 |
| 04 분석 제출 | 분석 모드, 원소·전구체, 전구체 양 입력, DB, 속도 조절, 장비·파장, CIF |
| 05 시리즈 분석 | 여러 패턴 일괄 제출과 추세 그래프 |
| 06 결과 보기 | 결과 목록, 신뢰도 배너, 상별 판정, 미설명 피크, 재정밀화, 그래프 조작, 질량 균형, 빠른 재정밀화와 버전, 피크 클릭 후보, 상 교체·비교, 배경 조정, 단축키, 피팅 겹쳐 비교, 실패 작업 재시도 |
| 07 보고서와 내보내기 | 논문형 보고서, 그림, CSV, 외부 프로그램용 파일 |
| 08 결과 해석 시 주의 | 상대 wt%, 비정질, 계산 구조, 질량 균형 해석, 검토 원칙 |
| 09 문제 해결 | 자주 겪는 상황과 대처 |

## 01 개요

### DARA란

분말 X선 회절(XRD) 패턴에서 결정상 후보를 자동으로 찾고 BGMN 엔진으로 Rietveld 정밀화까지 끝내는 웹 프로그램임. Ceder 그룹이 공개한 원본(Chemistry of Materials, 2026)을 바탕으로 설치, 입력 형식, 결과 검토, 보고서 기능을 크게 늘린 맞춤판임.

### 사용 대상

XRD는 직접 측정하지만 Rietveld 전용 소프트웨어는 익숙하지 않은 연구자에게 맞춤. 전문가에게는 빠른 1차 분석과 후보 비교 도구로 쓸모 있음. 자동 결과를 그대로 확정하는 도구는 아님. 판정 근거를 함께 보여 주므로 검토할 곳이 좁혀짐.

### 주요 기능

- 패턴 한 개 또는 여러 개(시리즈)를 올려 상 동정과 정량을 한 번에 수행
- 분석 모드 3종: Phase identification, Known-phase refinement, Reaction check
- 후보 사전 필터, 참조별 선별 시간 제한, 최대 분석 시간, 제출 전 예상 시간 표시
- 결과마다 신뢰도 배너(High confidence / Check recommended / Expert review recommended)와 상별 판정
- 설명되지 않은 피크를 significant / minor / instrument / noise로 구분하고 미량상 후보 제시
- 논문형 영문 보고서, 그림(PNG/SVG), CSV 표, Profex·TOPAS·GSAS-II·HighScore용 내보내기

### 화면 구성

상단 메뉴는 Submit, Results, Series, Tutorial, Documentation 순서. 서버 주소는 http://localhost:8898. 브라우저 창을 여러 개 열어도 같은 서버를 공유함.

> 이 설명서의 그림은 2026-10-27 현재 화면을 캡처한 것. 시료 이름과 수치는 조작 설명용이며 검증된 분석 결과로 인용하면 안 됨.

## 02 준비물

| 구분 · 항목 | 내용 |
| --- | --- |
| 필수 · Windows PC | Windows 10/11 64비트. 여유 공간 약 3 GB. 폴더 경로는 짧게(예: C:\DARA), OneDrive 동기화 폴더 밖에 둘 것. |
| 필수 · 인터넷(첫 실행) | uv, Python 3.12, 과학 계산 패키지, BGMN을 내려받음. 안내 문구 기준 약 1 GB, 실측은 670 MB 안팎. 설치 후 실행은 오프라인도 가능. COD/MP 구조 검색에는 계속 인터넷 필요. |
| 필수 · 측정 패턴 | .xrdml 권장. 다른 지원 형식은 4장 참고. |
| 필수 · 시료 정보 | 포함 원소, 전구체나 예상 화학식, 측정 파장. Reaction check라면 열처리 온도와 분위기도 필요. |
| 선택 · MP API 키 | Materials Project 계산 구조를 후보에 넣을 때 사용. 없으면 COD만으로 분석 가능. |
| 선택 · 장비 프로파일(GEQ/GER) | 기본 목록에 실제 장비·광학계와 맞는 항목이 없을 때만 등록. |
| 선택 · 참조 CIF | DB에 없는 상이나 연구실에서 검증한 구조를 추가할 때 사용. |

Python, Git, Anaconda를 미리 설치할 필요 없음. START.cmd가 필요한 도구를 DARA 폴더 안에 직접 설치함.

### MP API 키 받기

[Materials Project](https://materialsproject.org/)에 가입·로그인한 뒤 대시보드에서 API 키를 복사. 첫 실행 때 뜨는 대화상자에 붙여 넣으면 끝. 키는 공개 문서나 Git에 올리지 말 것.

### GEQ 장비 파일 구하기

먼저 Instrument profile 목록에서 실제 장비, 검출기, 슬릿 조건과 맞는 항목을 찾음. 맞는 항목이 있으면 등록 불필요. 없으면 장비 담당자에게 BGMN/Profex용 .geq(대응 .ger 포함)를 요청하거나, [Profex](https://www.profex-xrd.org/)의 Instrument → Edit Configuration에서 실제 하드웨어 값으로 생성. .xrdml 측정 파일의 확장자를 바꿔서는 .geq가 되지 않음.

### 참조 CIF 구하기

[COD](https://www.crystallography.net/cod/), 논문 보충자료, 이용권이 있다면 ICSD에서 확보. 원소, 공간군, 출처를 확인한 뒤 사용. 검색 결과 HTML을 .cif로 이름만 바꾼 파일은 읽히지 않음.

## 03 설치와 실행

### 내려받기

[https://github.com/youngboong/dara](https://github.com/youngboong/dara)에서 Code → Download ZIP 선택. 내려받은 ZIP을 오른쪽 클릭해 "모두 압축 풀기". 압축 파일 안에서 바로 실행하면 설치가 실패함. START.cmd, src, scripts, pyproject.toml이 한 폴더에 보이면 준비 완료.

### START.cmd 실행

START.cmd를 더블클릭. 첫 실행은 아래 순서로 자동 진행되며 5~15분 걸림. 진행 중에는 창을 닫지 말 것.

| 단계 | 내용 |
| --- | --- |
| 1. uv | 고정 버전 uv를 받아 SHA-256 확인 후 tools\uv에 저장 |
| 2. Python | CPython 3.12를 tools\python에 설치. 시스템 PATH, 레지스트리, 사용자 프로필은 건드리지 않음 |
| 3. .venv | 프로젝트 환경 생성. 패키지 버전은 scripts\constraints-py312-win.txt로 고정 |
| 4. BGMN | src\dara\bgmn\BGMNwin에 정밀화 프로그램이 없으면 내려받음. 실행할 때마다 확인 |
| 5. MP 키 | 처음 한 번 대화상자 표시: Save and continue 또는 Skip for now |
| 6. 시작 화면 | 진행 페이지가 먼저 열리고, 서버가 준비되면 http://localhost:8898로 전환 |

중간에 끊겨도 START.cmd를 다시 실행하면 받은 파일을 재사용해 이어서 진행함. 단계별 기록은 artifacts\launcher\setup-날짜-시각.log에 남음(최근 10개 보관). 두 번째 실행부터는 설치를 건너뛰고 1초 안팎에 서버가 뜸.

### MP 키 저장과 확인

키는 Windows 사용자 환경변수 MP_API_KEY에 저장되고 다음 실행부터 자동 사용. 저장 시점에는 온라인 검증을 하지 않음. Submit 화면 MP 스위치 옆 태그로 상태 확인 가능: MP key valid, MP key rejected, MP key not configured, MP key not verified. Re-check를 누르면 다시 조회함. Skip for now를 고른 경우 그 선택도 기억되므로 Reference databases에서 MP를 끄고 COD로 분석.

### 창을 닫으면 자동 종료

마지막 DARA 브라우저 창을 닫고 약 20초 뒤 서버가 스스로 종료됨. 창이 비정상적으로 사라진 경우에는 약 3분 안에 종료. 대기 중이거나 실행 중인 분석이 있으면 끝날 때까지 서버 유지. 바로 끄려면 START 창에서 Ctrl+C.

### 여러 창 사용

START.cmd를 다시 실행하면 이미 떠 있는 서버에 새 창만 추가로 열림. 서버가 중복으로 뜨지 않음. 한 창에서 제출하고 다른 창에서 결과를 보는 식의 병행 사용 가능.

### START 스위치(선택)

<!-- widths: 110,377 -->
| 스위치 | 용도 |
| --- | --- |
| -Reinstall | .venv를 새로 만들고 패키지 재설치. tools\와 다운로드 캐시는 유지되어 오프라인에서도 가능 |
| -Offline | 네트워크를 쓰지 않음. 빠진 구성 요소가 있으면 이유를 표시하고 종료 |
| -SetupOnly | 설치·확인만 하고 서버는 띄우지 않음 |
| -NoBrowser | 브라우저 창을 열지 않음 |
| -SkipApiKeySetup | MP 키 대화상자 생략 |

## 04 분석 제출

![그림 1. Submit 화면 위쪽. 패턴 업로드, 분석 모드, 원소·화학식, DB, 속도 조절 항목이 차례로 배치됨.](images/submit-top.png){height=470}

(1) User: 제출자 식별용 이름 입력.
(2) XRD pattern: 측정 파일을 끌어 놓거나 눌러서 선택.
(3) Analysis mode: Phase identification, Known-phase refinement, Reaction check 중 선택.
(4) Included elements / Precursor / expected formulas: 원소 기호와 예상 화학식 입력.
(5) Reference databases: COD·MP 사용 여부와 MP E-hull cutoff, Maximum MP structures 설정.
(6) Candidate pre-filter, Reference screening limit (s), Maximum analysis time (min): 속도 조절 3종.

### 패턴 올리기

User에 식별용 이름을 적고 XRD pattern 영역에 파일을 끌어 놓음. 지원 형식은 .xrdml, .xy, .xye, .txt, .csv, .dat, .asc, .scn, .ras, .rasx, .brml, .raw(Bruker RAW v2만). 텍스트 형식은 머리글, 쉼표·탭 구분, 소수점 표기 차이를 웬만큼 견딤. 읽기에 실패하면 몇 번째 줄이 문제인지 알려 줌.

### 업로드 직후 확인할 것

- XRDML이면 장비·검출기·파장 메타데이터로 가장 가까운 Instrument profile과 Wavelength가 자동 선택됨. 실제 슬릿·광학계와 다르면 직접 수정.
- Data quality pre-check (advisory): 정규화된 강도인지, S/N, 스텝 간격, 측정 범위를 점검해 경고 표시. 참고용이며 제출을 막지 않음.
- 파일 이름에서 화학식을 읽어 원소를 제안함(예: BiFeO3_800C_60min.xrdml → Bi, Fe, O). 실제 시료와 대조 후 사용.

### Analysis mode 고르기

| 모드 | 쓰임과 입력 |
| --- | --- |
| Phase identification | 원소계 안에서 가능한 상을 탐색. Included elements 필수(예: Bi, Fe, O). Precursor / expected formulas는 선택이며, 적어 둔 상은 후보에 반드시 포함됨. |
| Known-phase refinement | 구성 상을 이미 알 때. Phases to refine에 적은 상만 정밀화. 미지 불순물이나 반응 생성물은 찾지 않음. |
| Reaction check | 전구체, Temperature (°C), Atmosphere로 생성물을 예측한 뒤 피팅. 후보가 20~60개 수준이라 가장 빠름. MP 데이터 사용. |

Included elements에는 원소 기호(Bi, Fe, O), 화학식 칸에는 화학식(Bi2O3, Fe2O3)을 넣음. 잔류 전구체나 홀더 성분의 원소를 빠뜨리면 그 상은 처음부터 후보에서 빠짐.

### Reference databases

COD(experimental)는 실험 구조, MP(computed supplement)는 계산 구조 보충용. MP E-hull cutoff 기본값 0.05 eV/atom, Maximum MP structures 기본값 100. 값을 늘리면 후보와 계산 시간이 함께 늘어남. 이 값은 상이 존재할 확률과 무관함.

## 04 분석 제출 · 전구체 양 입력

![그림 2. Precursor / expected formulas 칸에 몰 계수를 넣은 예(위)와 칸 도움말(아래). 양을 적으면 결과에서 질량 균형 점검이 켜짐.](images/submit-precursor-amounts.png){height=250}

(1) Included elements: 원소 기호 입력(예: Fe, Ti, O).
(2) Precursor / expected formulas: 전구체와 양 입력. 예시는 Fe2O3 1몰, TiO2 2몰.
(3) 도움말 아이콘. 마우스를 올리면 입력 형식 안내가 뜸.
(4) 도움말 내용: 화학식 앞 몰 계수(Fe2O3, 2 TiO2) 또는 콜론 뒤 칭량 질량(In2O3:0.3022g, TiO2:0.2004g).

### 양 표기법

Precursor / expected formulas 칸에 전구체 양을 함께 적으면 결과의 상 분율로 질량 균형을 점검함(06장 질량 균형 점검 참조). 양을 적지 않으면 지금까지처럼 화학식만 쓰이고 점검은 건너뜀.

<!-- widths: 175,312 -->
| 입력 예 | 의미 |
| --- | --- |
| Fe2O3, 2 TiO2 (2TiO2, 2*TiO2도 가능) | 몰 계수. 계수가 없는 항목은 1로 계산 |
| 2Fe3O4, 3 Y2O3 | 몰 계수. 숫자와 화학식을 붙여 써도 됨 |
| In2O3:0.3022g, TiO2:0.2004g | 칭량 질량. 단위는 g, mg, mol, mmol. 단위를 생략하면 g |
| Fe2O3, TiO2 | 양 없음. 점검 건너뜀. 1:1로 가정하지 않음 |

- 질량이나 몰수를 적을 때는 모든 항목에 적어야 함. 일부만 적으면 양을 모르는 것으로 보고 점검 생략.
- 한 항목에 계수와 질량을 함께 쓰면 오류("Give either a coefficient or an amount, not both").
- 칸에 양이 없으면 파일 이름에서 양을 읽음. 2Fe3O4-3Y2O3_1000C_60min은 몰 계수(숫자가 없으면 1), In2O3_0p3022-TiO2_0p2004--8min은 그램(p는 소수점)으로 해석. 칸에 적은 전구체가 이름 속 화학식과 하나씩 정확히 대응할 때만 사용. 이때 결과 메시지에 "amounts read from the file name"이 붙음.
- 칸에 적은 양이 파일 이름보다 우선함. 파일 이름의 비율이 실제 칭량과 다르면 칸에 직접 적을 것.

## 04 분석 제출 · 속도와 장비 조건

![그림 3. Submit 화면 아래쪽. 예상 실행 시간, 장비 프로파일, Final refinement controls, 파장, 2θ 범위, CIF 입력.](images/submit-estimate.png){height=460}

(1) 예상 후보 수와 Estimated run time 안내. 조건을 바꾸면 갱신됨.
(2) Instrument profile: 장비 조건 선택. 맞는 항목이 없으면 Register instrument profile로 등록.
(3) Final refinement controls: Lattice parameters, Preferred orientation, Sample peak broadening.
(4) Synchrotron / Wavelength: 광원과 파장 지정.
(5) Analysis range · 2θ (°) / Excluded intervals · 2θ (°): 분석 범위와 제외 구간.
(6) Reference library CIFs / Additional reference CIFs: 참조 CIF 추가.
(7) Submit: 누른 뒤 확인 창에서 확정하면 작업 생성.

### 속도 조절 3종

<!-- widths: 140,347 -->
| 항목 | 동작 |
| --- | --- |
| Candidate pre-filter | 기본 켜짐. 계산 패턴의 강한 피크가 측정 패턴에 없으면 그 참조 구조를 미리 제외. 업로드·라이브러리·전구체 상은 제외 대상이 아님. 벤치마크 60개 시료에서 정답 상 유지율 100%. |
| Reference screening limit (s) | 선별 단계에서 참조 하나에 쓰는 BGMN 최대 시간. 기본 300 s, 입력 범위 60~1200. 아주 큰 구조는 두 배(최대 1200 s)까지 허용. 비우면 원래 값 1200 s. |
| Maximum analysis time (min) | 5~720분. 시간이 지나면 탐색을 멈추고 그때까지의 최선 결과를 미완료 표시와 함께 반환. 비우면 제한 없음. |

### 예상 시간

조건을 바꿀 때마다 예상 후보 수와 실행 시간 범위가 갱신됨. 예: "Estimated run time 8 min–33 min (typically 16 min) on 12 workers". 후보가 많으면 줄이는 방법도 함께 제안함. 대략적인 값이므로 PC 사양과 구조 크기에 따라 달라짐.

### 장비 프로파일과 파장

Instrument profile에서 실제 장비 조건을 선택. 목록에는 operator verified / unverified가 함께 표시됨. 맞는 항목이 없으면 Register instrument profile에서 Compiled GEQ (required)와 Matching GER (optional)을 올려 등록. 등록은 파일을 들여오는 일이고 교정은 아니므로 표준시료 피팅으로 따로 확인 필요. 실험실 광원은 Wavelength에서 Cu, Co, Cr, Fe 등 선택. Synchrotron을 켜면 Wavelength (nm)에 직접 입력(1 Å = 0.1 nm).

![그림 4. Register instrument profile 창. GEQ·GER 파일과 장비 정보를 넣어 프로파일 등록.](images/instrument-registration.png){height=400}

(1) 파일을 들여오는 것은 자동 교정이 아니라는 안내.
(2) Instrument / profile name, Geometry, slits and detector, Radiation (e.g. Cu Kα): 필수 입력.
(3) Compiled GEQ (required): 실제 광학계·검출기용 BGMN GEQ 파일. 필수.
(4) Matching GER (optional): 짝이 되는 GER 파일. 있으면 함께 올림.
(5) Verified against a standard by an operator / Verified by: 표준시료 확인 여부와 확인자.
(6) Standard, calibration date, validated 2θ range and notes: 표준시료, 교정일, 검증 범위 메모.
(7) OK: 등록 실행.

### 파장 불일치 확인

XRDML에 기록된 파장과 선택한 파장이 다르면 제출할 때 "The selected wavelength differs from the file" 창이 뜸. Submit anyway 또는 Go back and check 중 선택. 어느 쪽 값도 몰래 바꾸지 않음.

### 2θ 범위와 제외 구간

Analysis range · 2θ (°)를 비우면 측정 범위 전체 사용. 홀더나 장비 아티팩트가 확인된 위치만 Add excluded interval로 제외. 제외 구간은 피팅과 피크 진단에서 빠지지만 원본 강도는 그대로 보존됨. 범위나 제외 구간이 다른 작업끼리는 Rwp를 직접 비교하지 말 것.

### 최종 정밀화 조건

Final refinement controls의 Lattice parameters, Preferred orientation, Sample peak broadening은 처음엔 Keep saved/default 유지. 결과에서 구체적인 문제를 확인한 뒤 한 항목씩 바꿔 비교. 자유도를 늘려 Rwp가 내려가도 상 동정이 입증되지는 않음.

### 참조 CIF 넣기

Reference library CIFs는 서버에 저장해 두고 여러 작업에서 다시 쓰는 구조. Manage library → Add CIF to library로 추가. Additional reference CIFs(Select CIF files)는 이번 작업에만 쓰는 구조. 같은 상의 CIF를 여러 개 넣으면 정량값이 쪼개지므로 상 하나에 하나만 넣을 것.

### 제출과 조건 재사용

맨 아래 Submit을 누르고 확인 창에서 확정. 기존 작업 페이지의 Copy conditions & rerun을 누르면 원래 패턴과 조건이 채워진 Submit 화면이 열림. 바꿀 항목만 고쳐 제출하면 원본 작업은 그대로 둔 채 새 작업이 생김.

## 05 시리즈 분석

### 언제 쓰나

온도별, 시간별, 조성별처럼 같은 조건으로 측정한 패턴 여러 개를 한 번에 분석하고 추세를 볼 때 사용.

### 제출 방법

- Submit 화면에서 Submit as a series를 켬.
- Series name 입력 후 XRD patterns에 파일 2~50개를 한꺼번에 올림. 형식과 측정 조건이 같아야 함.
- Series order preview에 파일 이름에서 읽은 변수(예: 200 °C, 60 min)와 순서가 표시됨. 이 값은 그래프 축에만 쓰이고 분석에는 영향 없음.
- 나머지 분석 조건은 단일 패턴과 같음. 각 파일은 같은 설정의 독립 작업으로 처리됨.

### 시리즈 페이지

상단 Series 메뉴에서 시리즈를 고르면 작업 표와 추세 그래프 3종이 나옴. Phase fractions는 상별 wt%와 BGMN 추정 표준편차 오차막대, Lattice parameters는 상과 파라미터를 골라 변화 추적, Fit quality는 시료별 Rwp와 GOF. 추세 그래프는 완료된 작업이 2개 이상일 때 그려짐. Export CSV로 표를 받고, Compare patterns로 패턴을 겹쳐 볼 수 있음.

> 이 설명서 작성 시점의 서버에는 시리즈 작업이 없어 화면 그림은 생략함.

## 06 결과 보기

![그림 5. Results 목록. 상태, Best Rwp, GOF, 최종 상, 채택 여부를 한눈에 봄.](images/results.png){height=270}

(1) Filter file name / Filter username: 파일 이름·사용자로 검색.
(2) Compare selected / Export table: 체크한 작업 겹쳐 보기, 결과 표 CSV 저장.
(3) Status: 작업 상태 태그(COMPLETED, FIZZLED 등).
(4) Best Rwp (%) / GOF: 최저 Rwp 결과의 적합도.
(5) Final phases: 최종 정밀화된 상.
(6) Adopted: 채택 결과 여부.
(7) Actions: 결과 Download, 작업 Delete.

### Results 목록

열 구성은 ID #, Name, Status, Best Rwp (%), GOF, Final phases, Adopted, 시리즈 이름. Filter file name과 Filter username으로 검색. 완료 작업을 체크한 뒤 Compare selected(2~20개 패턴 겹쳐 보기) 또는 Export table(CSV) 사용.

| Status | 의미 |
| --- | --- |
| READY / RUNNING | 대기 중 / 분석 중. 작업 페이지에서 진행 단계와 후보 처리 수 확인. 필요하면 Stop analysis |
| COMPLETED | 완료. 신뢰도 배너부터 확인 |
| FIZZLED | 실패. 태그에 마우스를 올리면 원인 요약과 조치 힌트 표시 |
| CANCELLING / CANCELLED | 중단 처리 중 / 중단됨 |

### 작업 페이지 첫 화면

위쪽 버튼 줄에 Copy conditions & rerun, Download all, Download all results, Table (CSV), Figure (PNG), Figure (SVG), Export for... 배치. 그 아래 신뢰도 배너, Adopted result & lab notes, 조건·품질 표 순서.

## 06 결과 보기 · 신뢰도 판정

![그림 6. High confidence 예시(Task 9). minor 미설명 피크 3개는 판정에 영향 없음, 위치가 미량 Fe2O3와 맞는다는 힌트가 붙음.](images/task-high.png){height=300}

(1) Copy conditions & rerun: 같은 패턴·조건으로 Submit 화면 열기.
(2) Download all results: 계산 산출물 전체 ZIP.
(3) Table (CSV), Figure (PNG), Figure (SVG): 결과 표와 피팅 그림 저장.
(4) Export for...: Profex, TOPAS, GSAS-II, HighScore용 내보내기.
(5) 신뢰도 배너. 등급과 그 등급을 정한 이유 표시.
(6) How is this assessed?: 판정 기준 설명 열기.
(7) Adopted result & lab notes: 채택 후보 선택과 메모.

### 신뢰도 배너

배너 아래 항목이 그 등급을 정한 이유. How is this assessed?를 누르면 기준 설명이 열림. 확률이 아니라 통계량에 대한 문턱값 판정임.

| 등급 | 조건(하나라도 해당하면 그 등급) |
| --- | --- |
| High confidence | 아래 두 등급 조건에 하나도 해당하지 않음. GOF 3 이하, significant 미설명 피크 없음, 모든 상 Confident. |
| Check recommended | Ambiguous 또는 Weak evidence 상 존재(5 wt% 미만), GOF 3 초과, significant 피크 1~3개, 시료 변위 Warning, 넓은 배경 언덕, 선별 시간 초과 참조 존재, 최대 분석 시간에 걸려 중단, 질량 균형 편차 0.20 초과. |
| Expert review recommended | 5 wt% 이상 상이 Weak evidence, significant 피크 4개 이상 또는 높이 합 20% 이상, Rwp 25% 초과, GOF 4 초과, 시료 변위 Severe, 최종 정밀화 절반 이상 실패, 참조 25% 이상 미선별. |

### 상별 판정(Verdict)

정밀화된 상마다 Confident, Ambiguous, Weak evidence 중 하나가 붙음. 태그에 마우스를 올리면 근거 수치가 보임.

- 후보 모델 60% 이상에 등장하면 존재 근거로 인정.
- wt%를 표준편차로 나눈 값이 3 이상이면 Confident 쪽, 2 미만이면 Weak. 1 wt% 미만도 Weak.
- 그 상이 빠진 다른 후보의 Rwp가 max(0.5%p, Rwp의 5%) 안이면 Ambiguous. 툴팁에 대안 후보 이름 표시.
- Hamilton 검정(α = 0.01)으로 그 상이 피팅을 유의하게 개선하는지 확인.
- MP 계산 구조는 격자가 참조값에서 1.5% 넘게 움직이면 감점. 격자가 정밀화 한계에 닿은 경우는 모든 상에서 표시.

## 06 결과 보기 · 피크, 주의, 변위

![그림 7. Check recommended 예시(Task 23). significant 미설명 피크 2개(31.93°, 46.58°)가 등급을 낮춤. 점선 위치는 중간 표 생략.](images/task-check.png){height=320}

(1) Check recommended 배너.
(2) 등급을 낮춘 이유: significant 미설명 피크 2개(31.93°, 46.58°).
(3) Fit diagnostics의 Unexplained 태그: significant·minor 피크 수.
(4) Refinement quality 지표: Rwp, Rp, Rexp, GOF, χ², DW, 2θ 범위, 데이터 점 수, 반복 수.
(5) Specimen displacement 태그: 시료 변위(mm).
(6) Review cautions (automated checks, not a verdict): 자동 점검 결과 모음.

### 미설명 피크 등급

Fit diagnostics의 Unexplained 태그는 계산 패턴이 재현하지 못한 관측 피크 수. 각 피크를 평활 잔차의 높이(최강 피크 대비)와 S/N으로 분류함.

<!-- widths: 90,397 -->
| 등급 | 기준과 영향 |
| --- | --- |
| significant | 최강 피크의 2% 이상이면서 S/N 8 이상. 등급 판정에 반영되는 유일한 종류 |
| minor | noise와 significant 사이. 정보로만 표시하고 등급은 바꾸지 않음 |
| instrument | 강한 반사의 Cu Kβ, W Lα1 위치와 일치하는 약한 피크. 장비 유래 가능성 |
| noise | 최강 피크의 0.5% 미만이거나 S/N 3 미만 |

minor·significant 피크 위치가 후보 구조, 입력 구조, 로컬 COD 캐시의 어떤 상과 맞으면 "Positions are consistent with trace ..." 형태로 미량상 후보를 알려 줌. 이 힌트는 판정에 반영되지 않음. 해당 상을 넣고 다시 정밀화해 볼 가치가 있는지는 힌트를 출발점 삼아 판단.

### Review cautions

Refinement quality 아래 "Review cautions (automated checks, not a verdict)"에 자동 점검 결과가 모임. 예: Durbin–Watson이 1 미만이면 잔차 상관이 강해 esd가 과소평가됐을 가능성, 넓은 배경 언덕, 미설명 피크 요약.

### 시료 변위(Specimen displacement)

BGMN의 EPS2를 장비 프로파일의 고니오미터 반경으로 환산해 mm 단위로 표시(s = EPS2 × R). 0.3 mm 이하 OK, 0.3 mm 초과 Info, 1.0 mm 초과 Warning, 2.0 mm 초과 또는 정밀화 한계 도달 시 Severe. 변위가 크면 모든 피크가 함께 밀리므로 격자상수와 상 매칭을 조심해서 읽어야 함. 시료 높이와 장비 프로파일부터 재확인.

## 06 결과 보기 · 후보 비교와 재정밀화

![그림 8. 작업 페이지 아래쪽. 상별 판정·정량·격자 표와 Candidate comparison 표.](images/task-high-lower.png){height=330}

(1) Edit phases: 상 선택 재정밀화 창 열기.
(2) Verdict: 상별 판정(Confident, Ambiguous, Weak evidence).
(3) Quantity / Lattice parameters: wt%와 격자상수, 표준편차.
(4) Compare two candidate fits: 두 후보 피팅 겹쳐 보기. 후보가 둘 이상일 때 사용 가능.
(5) Confidence: 후보별 신뢰도 태그. 열 머리글로 정렬 가능.
(6) View fit / Adopt: 후보 피팅 보기와 채택.

### 후보 비교

Candidate comparison 표는 Rwp, GOF, Missing, Extra, Intensity mismatch, Cautions, Confidence 열로 정렬 가능. 모든 후보에 같은 기준의 신뢰도 태그가 붙으므로 대안 상 조합도 같은 눈금으로 비교됨. 후보가 둘 이상이면 Compare two candidate fits로 두 피팅을 같은 2θ 축에 겹쳐 보고 차이가 큰 위치로 바로 이동함.

### 채택과 메모

Adopted result & lab notes에서 과학적으로 맞다고 판단한 후보를 고르고 메모를 적은 뒤 Save review. 자동 최저 Rwp 결과와 채택 결과는 따로 저장됨. View adopted fit으로 채택한 피팅을 다시 확인.

![그림 9. Edit phases → Select phases & re-refine. 체크한 상만 함께 정밀화함.](images/phase-selection.png){height=380}

(1) 화학식이나 COD / MP 참조 ID로 목록 거르기.
(2) Final refinement controls: 새 작업에 쓸 최종 정밀화 조건.
(3) 선택한 상 수 표시(최대 20).
(4) 체크박스: 함께 정밀화할 상 선택.
(5) Starting candidate: 원래 후보에 들어 있던 상은 Included 표시.
(6) Run as new task: 새 작업으로 실행. 원래 결과는 유지.

### 선택 상 재정밀화

Final refined phase parameters의 Edit phases를 누르면 저장된 참조 상 목록이 열림. 넣을 상만 체크하고 Run as new task. 원래 패턴, 장비, 2θ 범위, 제외 구간, 최종 정밀화 설정을 그대로 쓰며 DB 검색은 반복하지 않음. 원래 결과는 유지되고 새 작업이 생김. 미량상 힌트가 나온 상을 추가하거나 의심스러운 상을 빼서 비교할 때 유용함.

## 06 결과 보기 · 피팅 그래프 조작

![그림 10. View fit 창의 피팅 그래프(Task 9). 오른쪽 버튼으로 끌어 확대 상자를 그리는 중인 장면.](images/fit-navigation.png){height=330}

(1) Fit intensity to view: 기본 켜짐. 보이는 2θ 구간에 맞춰 강도 축을 자동 조정.
(2) Reset zoom: 전체 패턴으로 복귀. 그래프를 더블클릭해도 같음.
(3) 조작 안내 문구. 마우스 조작, 범례 클릭, hover 정보 요약.
(4) 오른쪽 버튼 끌기로 그린 확대 상자(파란 점선). 버튼을 놓으면 그 범위로 확대, 끄는 중에 Esc를 누르면 취소.
(5) 아래 패널(관측-계산). 여기서 그린 상자는 2θ 범위만 바꿈.
(6) 그림 저장(카메라) 버튼. Plotly 도구 모음에는 이 버튼만 남음.

### 마우스 조작

작업 페이지의 View fit, Visualize final result, Visualize result 창과 Compare two candidate fits 창, Results의 Compare selected로 여는 패턴 비교 화면이 같은 방식으로 움직임.

<!-- widths: 150,337 -->
| 동작 | 결과 |
| --- | --- |
| 왼쪽 버튼 끌기 | 그래프 이동(pan) |
| 오른쪽 버튼 끌기 | 확대 상자 그리기. 위 패널은 2θ와 강도 범위를 함께 지정, 아래 관측-계산 패널은 2θ만 지정. Esc로 취소 |
| 마우스 휠 | 2θ 확대·축소. Fit intensity to view를 끈 상태와 두 비교 창에서는 강도 축도 함께 확대 |
| 더블클릭, Reset zoom | 전체 패턴 보기로 복귀. 비교 화면은 Reset view |

- Fit intensity to view가 켜져 있으면 확대·이동할 때마다 보이는 구간의 최대 강도에 맞춰 세로축이 다시 잡힘. 약한 피크를 자세히 볼 때 유용함. 끄면 강도 축이 고정되고 휠과 끌기로 세로축도 함께 움직임.
- 위 패널에서 상자로 강도 범위를 직접 정하면 그 범위가 유지됨. 자동 맞춤으로 돌아가려면 Reset zoom.
- Plotly 기본 도구 모음의 확대·이동·초기화 버튼은 없앴음. 그림 저장(카메라) 버튼만 남아 있음.
- 범례에서 상 이름을 누르면 그 상의 프로파일과 Bragg 위치가 표시됨.

![그림 11. hover 정보. 관측점(위)과 Rutile의 Bragg 반사 표시(아래)에 마우스를 올린 실제 화면(Task 9, Cu Kα1).](images/fit-hover-d.png){height=430}

(1) 관측점 hover: 2θ, d, 관측 강도. 27.7461°는 Cu Kα1(1.5406 Å)에서 d 3.2126 Å.
(2) Rutile의 Bragg 반사 표시(세로 눈금). 범례에서 상을 눌러야 나타남.
(3) Bragg 반사 hover: 2θ, d, hkl (1 1 0), 상대 반사 강도, 참조 구조 이름.

### d 간격 표시

그래프 위 점에 마우스를 올리면 "2θ …°"와 함께 "d … Å"가 표시됨. d는 Bragg 식 d = λ / (2 sin θ)로 계산하며 λ는 분석에 쓴 Kα1 파장. Bragg 반사 표시에서는 hkl과 d가 같이 나옴. 패턴 비교 화면에서는 시료마다 자기 분석 파장으로 d를 계산하므로 파장이 다른 시료도 d로 바로 비교 가능. 파장이 기록되지 않은 작업은 d가 빠지고 2θ만 표시됨.

## 06 결과 보기 · 질량 균형 점검

![그림 12. 질량 균형 caution 예시(Task 22, 2Fe3O4-3Y2O3). 신뢰도 배너 이유(위)와 Review cautions(아래). 점선 위치는 중간 표 생략.](images/task-mass-balance.png){height=200}

(1) 신뢰도 배너. caution 수준이라 등급은 High confidence 그대로임.
(2) 배너 이유 목록에 붙은 "Mass balance: …" 문장. 상 분율로 계산한 Fe:Y = 0.67:0.33, 파일 이름의 전구체 비율 0.50:0.50, 최대 편차 0.17.
(3) Review cautions의 같은 내용(info).

### 점검 방식

정밀화된 wt%를 원소 몰분율로 바꿔 전구체의 원소 몰분율과 비교함. 피팅이 좋아 보여도 정량이 틀렸거나 화학 조성이 맞지 않는 상이 들어간 경우를 잡아내는 점검.

- 비교 대상은 시료 밖으로 빠져나가기 어려운 원소. O, H, C, N과 Reaction check의 열린 원소는 제외. 남는 원소가 2개 이상이어야 계산함.
- 지표는 원소별 몰분율 차이 중 가장 큰 절댓값(최대 편차).
- wt% 표준편차를 반영해 편차에서 2σ만큼 여유를 둔 뒤 등급을 정함.
- 전구체 양은 Submit 화면의 Precursor / expected formulas 입력이 우선이고, 없으면 파일 이름에서 읽음(04장 전구체 양 입력 참조). 둘 다 없으면 점검 생략.

<!-- widths: 110,377 -->
| 최대 편차 | 결과 |
| --- | --- |
| 0.10 이하 | 메시지 없음(ok) |
| 0.10~0.20 | caution. info 수준의 Review cautions와 배너 이유 한 줄 추가. 등급은 바뀌지 않음 |
| 0.20 초과 | warning. Review cautions 경고와 배너 이유 추가. 종합 등급은 최소 Check recommended |

- 이 점검만으로 등급이 Expert review recommended까지 올라가지는 않음.
- 예: Fe2O3 + 2 TiO2(Fe:Ti = 1:1)가 Fe2TiO5 + TiO2로 반응했다면 75 wt% Fe2TiO5, 25 wt% TiO2가 나와야 함. 26.8/73.2로 정량된 결과는 Fe:Ti = 0.18:0.82(편차 0.32)라 warning.
- 문턱값 근거: 쌍 반응 기준 시료 20개의 전문가 정밀화 중 12개는 0.07 미만, 5개는 0.11~0.17(미동정 물질이나 모델에 빠진 상), 3개는 0.29 초과.
- 결과는 보고서의 질량 균형 문단과 표(그림 25), Table (CSV)와 Export table의 mass_balance_deviation 열에도 남음.

## 06 결과 보기 · 결과 수정과 버전

완료된 작업에서 상을 더하거나 빼고 배경 조건을 바꿔 그 자리에서 다시 정밀화할 수 있음(빠른 재정밀화). 결과는 같은 작업 안에 v2, v3 … 버전으로 쌓이고 원래 자동 분석(v1)은 그대로 남음.

![그림 13. 미량상 힌트의 Add and re-refine을 누른 직후(Task 9). 피팅 창 위쪽에 진행 막대가 뜸.](images/quick-hint-progress.png){height=190}

(1) 미량상 힌트와 Add and re-refine. 힌트의 상을 넣고 다시 정밀화해 새 버전 생성.
(2) 진행 막대의 작업 이름(Added Fe2O3 (hint))과 현재 단계(BGMN refinement).
(3) 경과 시간(4 s)과 남은 시간 추정(15 s left).
(4) Cancel: 진행 중인 재정밀화 취소.

### 빠른 재정밀화

- DB 검색과 후보 탐색을 다시 하지 않음. 바꾼 상 조합만 정밀화.
- 정밀화 조건은 자동 분석의 최종 정밀화와 같음. 패턴, 장비 프로파일, 파장, 2θ 범위, 제외 구간, Final refinement controls를 그대로 씀.
- 대기열을 거치지 않고 서버 안에서 바로 시작함. 걸리는 시간은 거의 BGMN 계산 시간. Task 9 실측: 상 2개 16 s, 상 3개 24 s.
- 진행 단계는 Queued → Preparing inputs → BGMN refinement → Peak diagnostics → Storing version 순.
- 남은 시간은 같은 작업에서 상 수와 배경 항 수가 같은 이전 버전의 BGMN 시간(중앙값)으로 추정. 비교할 버전이 없으면 "estimating…" 표시.
- 한 작업에서는 한 번에 하나만 실행. 다른 작업의 재정밀화가 먼저 돌고 있으면 "Queued (n ahead)" 표시.
- 피팅 창을 닫아도 계속 진행되고 페이지를 새로 고쳐도 진행 막대가 다시 붙음.
- Cancel을 누르면 "Re-refinement cancelled." 표시 후 버전을 만들지 않음. 실패하면 빨간 알림에 원인이 나옴.
- 끝나면 "Version vN ready (Rwp …%)" 알림과 함께 피팅 창이 새 버전으로 바뀜.

Edit phases → Run as new task(그림 9)는 별도 작업을 새로 만들어 대기열에서 실행함. 빠른 재정밀화는 같은 작업 안에 버전만 추가하므로 상 하나를 넣거나 빼 보는 비교에 더 적합함.

![그림 14. 버전 v3를 연 피팅 창(Task 9). 기준 피팅 대비 변화가 태그로 나옴.](images/version-delta.png){height=140}

(1) 표시할 피팅 선택. Best fit, 후보, 버전(v2, v3 …) 순으로 나열.
(2) Change from the base fit: 기준 피팅 대비 Rwp, GOF, χ², 미설명 피크 수, 상별 wt% 변화. 좋아진 값은 초록, 나빠진 값은 주황.
(3) 기준이 v1일 때 붙는 안내. v1은 작업 실행 당시 코드로 계산됐다는 뜻.
(4) 이 버전의 신뢰도 등급. 버전마다 새로 판정함.

### 미량상 힌트에서 추가

피팅 창의 Review cautions 아래에 "Trace Fe2O3 (R-3c) matches 33.44°, 35.89°" 같은 힌트가 줄마다 나오고 오른쪽에 Add and re-refine 버튼이 붙음. 누르면 그 상을 넣어 바로 재정밀화함(라벨 "Added Fe2O3 (hint)"). 로컬에 CIF가 없는 참조나 다른 재정밀화가 도는 중에는 버튼이 꺼짐. Candidate comparison 표의 Missing 칸에 마우스를 올려도 같은 힌트를 볼 수 있음.

## 06 결과 보기 · 버전 관리

![그림 15. Versions 표(Task 9). v1 자동 분석, v2 기준 재계산, v3 힌트 추가, v4 배경 변경. v3를 채택한 상태.](images/versions-panel.png){height=230}

(1) Versions 제목과 채택 표시(v3 adopted).
(2) Re-refine baseline: 원래 상 조합을 현재 코드로 다시 정밀화.
(3) Origin: 버전을 만든 방법.
(4) Rwp (%), GOF, Phases, Unexplained: 버전별 적합도, 상 목록, 미설명 피크 수.
(5) View / Compare: 피팅 창에서 열기 / 채택 결과(없으면 v1)와 나란히 비교.
(6) Adopt: 이 버전을 채택하고 현재 lab notes도 함께 저장.
(7) Report: 이 버전의 논문형 보고서.
(8) Export: 이 버전을 Profex, TOPAS, GSAS-II, HighScore 형식으로 내보내기.
(9) 삭제(휴지통). v1과 채택 중인 버전은 삭제 불가.

### Versions 표

작업 페이지의 Final refined phase parameters 표 아래에 있음. 진행 막대(그림 13)도 이 표 위에 함께 표시됨. 다른 창에서 만든 버전까지 15초마다, 그리고 창으로 돌아올 때 새로 읽어 옴.

<!-- widths: 110,377 -->
| Origin | 만든 방법 |
| --- | --- |
| Automatic | 자동 분석 결과(v1) |
| Hint | 미량상 힌트의 Add and re-refine |
| Peak search | 피크 클릭 후보 창의 Add and re-refine |
| Swap / Remove | 상 표의 Swap·Switch / Remove |
| Background | Background… 창의 Re-refine |
| Manual | Re-refine baseline 등 그 밖의 재정밀화 |

- 지운 버전 번호는 다시 쓰지 않음. 채택 중인 버전을 지우려면 다른 결과를 먼저 채택해야 함("Un-adopt first").
- 채택한 버전은 Adopted result & lab notes의 View adopted fit과 Report (saved choice)에 쓰임. 선택을 지정하지 않고 받는 보고서·내보내기도 채택 결과 기준.

### Re-refine baseline과 기준 차이

v1은 작업을 실행한 당시의 코드로 정밀화된 결과임. 이후 코드가 바뀌었다면 새 버전과 v1의 차이에는 상 조합 효과와 코드 변경 효과가 섞임. 그래서 v1을 기준으로 한 변화 표시에는 "v1 was refined when the task ran; re-refine the baseline to compare with the current code." 안내가 붙음(그림 14의 (3)). Re-refine baseline을 누르면 원래 상 조합을 현재 코드로 다시 계산한 버전(라벨 "Baseline (current code)")이 생기고, 끝나면 v1과의 비교 창이 열림. 이 버전과 다른 버전을 비교하면 상 조합 효과만 볼 수 있음. Task 9는 v1과 기준 재계산이 모두 Rwp 2.96%로 같았음.

## 06 결과 보기 · 피크 클릭과 상 교체

![그림 16. 피팅 그래프에서 미설명 피크 두 개(33.44°, 35.89°)를 고른 뒤 열린 후보 창(Task 9). 첫 후보의 반사 위치를 미리 표시한 상태.](images/peak-drawer.png){height=380}

(1) 고른 피크 위치(빨간 점선). 보라색 세로선은 Preview ticks로 띄운 후보의 반사 위치.
(2) Selected peaks: 고른 피크 칩(×로 하나씩 해제), Show candidates, Clear.
(3) 후보 창 제목. 고른 피크 위치 표시.
(4) 후보 정보: 화학식, 공간군, 출처(This task, Library, COD), 점수 막대, 맞은 피크 수, 강한 반사 수, 참조 이름.
(5) Preview ticks / Hide ticks: 후보의 반사 위치를 그래프에 겹쳐 보기.
(6) Add and re-refine: 이 후보를 넣어 재정밀화.

### 피크 클릭으로 후보 찾기

- 미설명 피크 표시(삼각형)나 관측점을 클릭하면 그 피크가 선택되고 오른쪽에 후보 창이 열림. 관측점은 0.3° 안의 미설명 피크 표시, 없으면 ±0.15° 안의 극대점으로 맞춰짐.
- Shift+클릭으로 피크를 더함(최대 10개). 같은 피크를 다시 Shift+클릭하면 빠짐.
- 후보는 이 작업의 후보 구조, 입력 CIF, 참조 라이브러리, 로컬 COD 캐시에서 찾음. 첫 검색은 15초까지 걸릴 수 있고 COD를 더 찾는 중이면 "Still searching COD…" 표시.
- 점수는 고른 피크를 얼마나 설명하는지 기준. 그 상의 강한 반사가 데이터에 안 보이면 "… not seen in the data"가 붙고 점수가 내려감. 점수만으로 존재를 판정하지 말고 Preview ticks로 다른 반사 위치도 확인할 것.
- 로컬 CIF가 없는 후보는 Add and re-refine이 꺼짐. 추가한 버전의 라벨은 "Added … (peak search)".
- Esc를 누르면 피크 선택이 먼저 지워지고 한 번 더 누르면 피팅 창이 닫힘.

![그림 17. 피팅 창의 상 표(Task 18). Ambiguous로 판정된 Calcite 행에 Swap 버튼이 붙음.](images/phase-swap.png){height=140}

(1) Ambiguous 판정. 거의 같은 Rwp로 다른 구조 모델을 쓴 후보가 있음.
(2) Swap: 대안 구조 모델로 바꿔 재정밀화 후 비교.
(3) Remove: 이 상을 빼고 재정밀화 후 비교. 상이 2개 이상일 때만 표시.

### Swap, Switch, Remove

피팅 창의 상 표 Actions 열에 나옴. 상별 판정 근거에 대안 후보가 있을 때만 Swap 또는 Switch가 보임.

<!-- widths: 80,407 -->
| 버튼 | 동작 |
| --- | --- |
| Swap | 같은 화합물의 다른 구조 모델(다른 공간군·다형)로 이 상만 바꿈. 다른 상은 그대로 |
| Switch | 대안 후보 N의 상 조합으로 통째로 바꿈. 없는 상은 넣고 후보에 없는 상은 뺌 |
| Remove | 이 상만 빼고 다시 정밀화 |

세 버튼 모두 끝나면 원래 피팅(A)과 새 버전(B)을 나란히 보는 비교 창이 바로 열림.

## 06 결과 보기 · 두 피팅 비교(FitCompare)

![그림 18. Swap 직후 열린 비교 창(Task 18). Calcite R-3c를 P12₁/c1 모델로 바꾸자 Rwp가 11.43%에서 11.85%로 오르고 미설명 피크가 2개에서 6개로 늘어 A가 더 나음.](images/fit-compare.png){height=470}

(1) A·B 이름과 판정 태그(A fits better, B fits better, Similar fit). 괄호 안은 B - A의 Rwp 차이.
(2) Adopt B / Keep A / View B. B 채택, 채택 변경 없이 닫기, B를 피팅 창에서 열기.
(3) 지표 표: Rwp, GOF, χ², 상별 wt%, Mass balance, Unexplained peaks, Cautions.
(4) 관측점과 두 계산 패턴. A는 파란 실선, B는 주황 점선.
(5) 두 피팅의 관측-계산 잔차.
(6) 두 계산 패턴이 가장 크게 다른 2θ 위치. 누르면 그 구간으로 확대. 그래프에서는 노란 띠로 표시.

### 비교 창 읽기

- 같은 측정 패턴의 두 피팅을 같은 축에 겹쳐 보여 줌. 확대·이동 방식은 다른 피팅 그래프와 같음.
- Keep A를 눌러도 B 버전은 Versions 표에 남음. 나중에 Compare 버튼으로 다시 열 수 있음.
- Adopt B는 B를 채택하면서 현재 lab notes를 함께 저장함.
- 노란 띠는 두 모델이 서로 다른 구간일 뿐, 어느 쪽이 맞다는 증거는 아님. Rwp 하나보다 잔차, 상의 화학 조성, 물리적 타당성을 함께 볼 것.

## 06 결과 보기 · 배경 조정과 단축키

![그림 19. 피팅 창의 Background… 설정 창. Auto를 끄고 배경 다항식 항 12개, 제외 구간 14–18°를 넣은 예.](images/background-popover.png){height=280}

(1) Background…: 배경 설정 창 열기.
(2) Background polynomial terms: Auto(BGMN이 결정) 또는 항 수 2~20 직접 지정.
(3) Exclude a hump: 피팅에서 뺄 2θ 구간(비정질 언덕 등). Add interval로 추가, Use current view로 지금 보이는 2θ 범위를 그대로 추가, 휴지통으로 삭제.
(4) 주의 문구. 배경이나 제외 구간을 바꾸면 Rwp도 바뀌므로 같은 조건의 피팅끼리만 비교.
(5) Re-refine: 이 조건으로 재정밀화. 라벨 예 "Background: 12 terms".

### 배경 조정 시 주의

- 항 수를 늘리면 BGMN 계산이 크게 느려짐. Task 9에서 Auto는 16~24 s, 12항은 98 s 걸림. 남은 시간 추정도 같은 항 수의 이전 버전만 참고하므로 처음에는 "estimating…" 표시.
- 배경을 바꾼 버전에는 "Background model or excluded ranges changed from the original analysis; compare Rwp only with fits that use the same ranges." caution이 붙음. Rwp가 낮아졌다고 모델이 나아졌다고 보면 안 됨.
- 추가한 제외 구간은 그래프에 음영으로 표시되고 원래 작업의 제외 구간 위에 더해짐.

![그림 20. 피팅 창에서 ?를 눌러 연 Keyboard shortcuts 창.](images/shortcuts.png){height=230}

(1) Shortcuts (?): 단축키 표 열기. ?키와 같음.
(2) 단축키 표.

### 키보드 단축키

피팅 창(View fit, Visualize final result 등)이 열려 있을 때만 동작함. 비교 창이 떠 있으면 꺼짐.

<!-- widths: 90,397 -->
| 키 | 동작 |
| --- | --- |
| ← / → | 이전·다음 피팅. Best fit → 후보 → 버전 순으로 돌아감 |
| 1–9 | 범례의 n번째 상 프로파일 켜기·끄기 |
| R | Reset zoom(전체 패턴 보기) |
| F | Fit intensity to view 켜기·끄기 |
| Esc | 피크 선택 해제, 선택이 없으면 피팅 창 닫기. 오른쪽 끌기 확대 중에는 상자만 취소 |
| ? | 단축키 표 보기 |

입력란, 선택 상자, 숫자 칸에 글자를 치는 중에는 단축키가 동작하지 않음(lab notes 작성, Background… 숫자 입력 등). Ctrl, Alt, Cmd를 함께 누른 경우도 무시함.

## 06 결과 보기 · 피팅 겹쳐 비교와 실패 작업 재시도

![그림 21. Compare fits 화면(?fits=9:best,9:version:3). Task 9의 best fit(파랑)과 v3(주황) 계산 패턴이 거의 겹침. 아래 패널에 A - B 차이 표시.](images/compare-fits.png){height=440}

(1) Compare fits 화면. Back to Results, Measured patterns only(측정 패턴 비교로 전환), Clear stored comparison list.
(2) Show calculated / Show observed: 계산 패턴, 관측 패턴 표시.
(3) Show difference A - B: 목록 첫 두 피팅의 차이를 아래 패널에 표시. 피팅이 2개 이상일 때 사용 가능.
(4) 겹친 계산 패턴. Normalize max intensity to 100, Vertical offset으로 보기만 조정.
(5) A - B 차이 패널.
(6) 피팅 목록: Fit, Pattern, Rwp (%), Points, Wavelength (Å). 이름을 누르면 그 작업으로 이동.

### 여러 피팅 겹쳐 보기

- 피팅 창의 Add to comparison을 누르면 그 피팅(최적 결과, 후보, 버전)이 브라우저에 저장된 비교 목록에 들어감. 알림의 Open comparison으로 바로 열 수 있음.
- 목록은 최대 6개이며 넘치면 오래된 것부터 빠짐. Compare 화면 위쪽 "N fits in your comparison list" 알림의 Compare fits 버튼으로도 열림.
- 주소는 /compare?fits=작업:선택 형식. 선택은 best, 후보 번호(0부터), version:n. 예: /compare?fits=9:best,9:version:3.
- Show phase markers를 켜면 상별 반사 위치가 0 아래에 표시됨.
- 파장이 다른 피팅이 섞이면 "Check the radiation before comparing peak positions." 경고. 같은 2θ가 같은 d를 뜻하지 않음.
- Rwp는 같은 데이터, 같은 피팅 범위끼리만 비교 가능.

![그림 22. 실패 작업(Task 16, EFLECH 시간 초과) 페이지(위)와 Results 목록의 Retry(아래, Task 24).](images/retry.png){height=230}

(1) 실패 원인 요약과 조치 힌트.
(2) Retry unchanged: 같은 조건으로 새 작업 실행. 자동 조치가 있는 원인이면 왼쪽에 Retry with suggested fix (…) 버튼이 함께 나옴.
(3) Results 목록의 Retry: 확인 후 같은 조건으로 재실행.

### 실패 작업 재시도

FIZZLED 작업 페이지 위쪽에 원인 요약과 재시도 버튼이 나옴. 어느 버튼이든 새 작업을 만들고 실패한 작업은 그대로 남기며 새 작업 페이지로 이동함. Retry with suggested fix를 누르면 바꿀 설정 목록(설정 이름 → 새 값)을 먼저 보여 주고 Retry with fix로 확정함.

<!-- widths: 150,337 -->
| 원인 | Retry with suggested fix가 바꾸는 것 |
| --- | --- |
| 계산 시간 초과(A calculation step timed out) | Retry with longer time limits. 선별 시간 제한 2배(최대 1200 s), Maximum analysis time을 지정했다면 1.5배(최대 720분) |
| MP 키 문제, 네트워크 오류 | Retry without Materials Project. MP 끄기. COD와 업로드 CIF가 모두 없으면 COD 켜기. Reaction check이거나 MP를 안 쓴 작업은 해당 없음 |
| 메모리 부족 | Retry with fewer MP structures and the candidate pre-filter. Candidate pre-filter 켜기, MP 구조 수 최대 50 |
| 후보 구조 없음 | Retry with COD enabled and more MP structures. COD 켜기, MP 사용 시 구조 수 300 이상 |
| 모든 후보 최종 정밀화 실패 | Retry with lattice refinement limited to 3 %. 격자 변화 3% 제한 |
| EFLECH 시간 초과, 중단, 피크 없음, BGMN 오류, Ray 오류 | 자동 조치 없음. Retry unchanged만 표시 |

- EFLECH(피크 검출)는 고정 시간 제한이 있어 선별 시간을 늘려도 소용없음. 2θ 범위를 좁히거나 잡음 구간을 제외 구간으로 빼서 Copy conditions & rerun으로 다시 제출할 것.
- 선택 상 재정밀화로 만든 작업은 Retry unchanged만 가능.
- 이미 시간 제한이 최댓값이라 바꿀 값이 없으면 자동 조치 버튼 대신 안내 메시지가 나옴.

## 07 보고서와 내보내기

![그림 23. 논문형 영문 보고서(HTML) 앞부분. Summary, Experimental and methods, 기록된 조건 순으로 이어짐.](images/report.png){height=330}

(1) 제목과 작업 정보: Task 번호, 생성 시각, 최저 Rwp.
(2) Summary: 종합 판정과 상별 정량 요약.
(3) Experimental and methods: 측정·정밀화 방법 문단.
(4) Recorded settings: 기록된 분석 조건 표.

### 논문형 보고서

Best-fit report는 최저 Rwp 결과, Report (saved choice)는 채택한 후보(없으면 최저 Rwp)를 보고서로 내보냄. 인터넷 없이 열리는 단일 HTML 파일이며 Summary, Experimental and methods, Results, 표, 그림, 참고문헌으로 구성. 방법 문단에는 DARA·BGMN 버전, 파장, 고니오미터 반경, 데이터 범위, 정밀화 변수가 문장으로 들어가 논문 초안에 옮기기 쉬움. PDF가 필요하면 브라우저에서 열고 Ctrl+P → PDF로 저장.

![그림 24. 보고서 Results의 표 부분(피팅 그림은 그림 26과 같아 생략).](images/report-tables.png){height=450}

(1) Fit statistics: Rwp, Rp, Rexp, GOF, χ², Durbin–Watson, 2θ 범위, 데이터 점 수.
(2) Phase quantification and crystallography: 상별 wt% ± esd, 격자상수, 판정, 참조 구조.
(3) Confidence assessment and review cautions: 상별 판정 근거 표.
(4) Unexplained observed peaks: 미설명 피크의 위치, 상대 높이, S/N, 등급.

![그림 25. 보고서의 질량 균형 문단과 원소 표(Task 22). 자동 점검 표에도 mass_balance 행이 들어감.](images/report-mass-balance.png){height=300}

(1) Mass balance 문단: 상 분율과 전구체의 원소 비, 판정(differ moderately), 최대 편차, 제외한 휘발 원소.
(2) 원소별 비교 표: Precursors (mole fraction), Refined phases, Deviation.
(3) 자동 점검 표의 mass_balance 행(Level info).

### 그림과 표

- Figure (PNG): 600 dpi 피팅 그림. 관측·계산·배경·차이 곡선과 상별 Bragg 위치 포함.
- Figure (SVG): 같은 그림의 벡터 버전. 논문 편집용.
- Table (CSV): 상별 wt%, 표준편차, 격자상수, 품질 지표, 미설명 피크 등급 수. Results 목록의 Export table은 여러 작업을 한 표로 묶음.

### 외부 프로그램용 내보내기

결과를 다른 프로그램에서 이어서 검토하거나 수정하려면 Export for... 메뉴에서 선택.

<!-- widths: 170,317 -->
| 메뉴 항목 | 내용 |
| --- | --- |
| Profex / BGMN (.sav + .str) | Profex에서 바로 여는 프로젝트와 구조 파일 |
| TOPAS (.inp starting model) | TOPAS 시작 모델 입력 파일 |
| GSAS-II (CIF + data) | 정밀화된 CIF와 패턴 데이터 |
| HighScore (CIF + data) | 정밀화된 CIF와 패턴 데이터 |

Download all results는 계산 산출물 전체(.lst 포함)를 ZIP으로 묶음. 원본 측정 파일, 사용한 CIF, 장비 조건, 보고서와 함께 시료별 폴더에 보관하면 재검토가 쉬움. 서버 데이터는 사용자 홈의 .dara-server 폴더에 있으며 Git 저장소와 별개임.

![그림 26. Figure (PNG) 출력 예시.](images/figure.png){height=330}

(1) 범례: Observed, Calculated, Background, Difference.
(2) 관측(검은색)과 계산(빨간색) 패턴. 가장 강한 반사 부근 예시.
(3) Background: 회색 배경 곡선.
(4) 상별 Bragg 반사 위치(tick).
(5) Difference: 관측값에서 계산값을 뺀 차이 곡선.

## 08 결과 해석 시 주의

### wt%는 상대값

정량값은 모델에 포함된 결정상끼리 합이 100%가 되도록 정규화한 값. 비정질이나 동정되지 않은 물질은 빠져 있음. 시료 전체의 절대 함량으로 읽으면 안 됨. 배경에 넓은 언덕이 보이면 비정질 성분을 의심.

### 불확도의 범위

표시되는 ± 값은 최소제곱 피팅의 BGMN 추정 표준편차. 우선 배향, 미세흡수, 시료 변위, 잘못된 구조 모델, 입자 통계 같은 계통 오차는 포함되지 않음. Durbin–Watson 주의가 뜨면 실제 불확도는 더 큼.

### 계산 구조(MP) 사용 시

MP 구조는 실험 구조보다 격자가 몇 % 어긋나는 일이 흔함. DARA는 실험 구조를 먼저 쓰고, MP 구조는 격자 허용 범위를 넓혀 정밀화함. 최종 상이 계산 구조뿐이면 같은 화학식의 실험 CIF를 찾아 재정밀화해 보는 편이 안전함.

### DB에 없는 상은 못 찾음

후보는 COD, MP, 사용자가 넣은 CIF에서만 나옴. 어디에도 없는 상은 탐색 불가. significant 미설명 피크가 남으면 원소 목록 누락이나 새로운 상 가능성을 검토.

### 질량 균형 편차 해석

질량 균형 caution이나 warning은 어디가 틀렸는지 알려 주지 않음. 비율이 어긋났다는 사실만 알려 주므로 원인은 직접 좁혀야 함.

- 휘발 손실: 고온에서 Bi, Pb, Li, Na, K 같은 원소가 일부 날아가면 실제 조성이 전구체 비율과 달라짐. 이런 원소가 있는 계에서는 편차 자체가 실제 현상일 수 있음.
- 비정질·미동정 상: wt%는 모델에 넣은 결정상끼리 정규화한 값. 비정질이나 DB에 없는 상에 들어간 원소는 계산에서 빠지므로 남은 상의 원소 비가 치우침. 배경 언덕, significant 미설명 피크와 함께 확인.
- 미반응·누락 상: 반응이 끝나지 않아 남은 전구체나 중간상이 모델에 없으면 편차가 커짐. 미량상 힌트가 있으면 Edit phases로 넣고 재정밀화해 비교.
- 정량 문제: 우선 배향, 미세흡수, 잘못된 구조 모델, 비슷한 상끼리 강도를 나눠 갖는 현상 때문에 wt%가 틀릴 수 있음. 판정이 Ambiguous인 상이 있거나 Durbin–Watson 주의가 함께 뜨면 이쪽을 먼저 의심.
- 입력 문제: 전구체 양이 실제 칭량과 다르면 맞는 결과도 어긋나 보임. 아래 파일 이름 규칙 확인.

### 파일 이름의 1:1 규칙

Precursor / expected formulas에 양이 없으면 파일 이름에서 양을 읽음. 이때 숫자 없는 화학식은 계수 1로 처리하므로 Fe2O3-TiO2_800C_60min.xrdml 같은 이름은 Fe2O3:TiO2 = 1:1(몰)로 해석됨. 반면 칸에 "Fe2O3, TiO2"만 적은 경우는 1:1로 가정하지 않고 점검을 건너뜀. 실제 혼합비가 1:1이 아닌데 이름에 계수를 적지 않았다면 잘못된 비율로 점검되어 근거 없는 경고가 뜰 수 있음. 둘 중 하나로 해결: 파일 이름에 계수나 질량을 넣거나(예: Fe2O3-2TiO2_..., In2O3_0p3022-TiO2_0p2004_...), 제출 때 칸에 양을 직접 적기(칸 입력이 우선).

### 자동 결과는 검토 대상

High confidence도 정답 보증이 아님. 시료 이력, 합성 조건, 가능하면 다른 분석법(SEM-EDS, XRF 등) 결과와 대조. 낮은 Rwp 하나로 상 조합을 확정하지 말고 미설명 피크, 강도 차이, 판정 근거를 함께 볼 것. 최종 판단과 채택 근거는 lab notes에 남겨 둘 것.

## 09 문제 해결

<!-- widths: 140,347 -->
| 상황 | 확인과 대처 |
| --- | --- |
| 첫 실행이 실패함 | 인터넷과 프록시 확인 후 START.cmd 재실행. 받은 파일은 재사용됨. 원인은 START 창의 마지막 메시지와 artifacts\launcher\setup-*.log에서 확인. 종료 코드 2는 인터넷 없음, 3은 BGMN 다운로드 실패, 4는 uv 다운로드·검증 실패, 6은 패키지 설치 실패. |
| 폴더를 옮긴 뒤 실행 안 됨 | START가 이동을 감지해 환경을 자동 재구성함. 그래도 안 되면 START.cmd -Reinstall 또는 .venv 폴더 삭제 후 재실행. |
| MP key rejected / not configured | START.cmd 재실행 시 키를 다시 입력하거나 Windows 환경변수 MP_API_KEY 확인. 급하면 MP 스위치를 끄고 COD와 CIF로 분석. |
| 서버가 혼자 꺼짐 | 모든 DARA 창을 닫으면 약 20초 뒤 정상 종료되는 동작임. 분석 중에는 꺼지지 않음. 다시 쓰려면 START.cmd 실행. |
| 웹 화면이 안 열림 | http://localhost:8898을 직접 입력. 회사 프록시가 로컬 주소를 가로채는지 확인. START는 기본 브라우저 실패 시 Edge나 Chrome을 시도함. |
| 분석이 너무 느림 | 먼저 예상 시간 확인. 전구체를 알면 Reaction check가 가장 빠름. Candidate pre-filter 켜기, MP 구조 수 줄이기, Maximum analysis time 지정(중간 최선 결과 반환), 2θ 범위 좁히기 순으로 시도. |
| COD 연결 오류 | "Network error while contacting a structure database"가 뜨면 인터넷·프록시 확인. 이전에 받은 구조는 로컬 캐시에서 재사용되고 일부 다운로드 실패는 건너뜀. 계속 실패하면 필요한 CIF를 직접 올려 분석. |
| CERTIFICATE_VERIFY_FAILED, CODDownloadError | 기관망처럼 HTTPS를 검사하는 네트워크에서 발생. START.cmd가 Windows 인증서 저장소와 certifi를 합친 CA 묶음(tools\certs\dara-ca-bundle.pem)을 만들어 서버에 넘기므로 START.cmd로 다시 실행. 끄려면 환경변수 DARA_CA_BUNDLE=off. |
| No candidate structures were found | 원소 목록, COD/MP 스위치 확인. 그래도 없으면 참조 CIF 업로드. |
| BGMN refinement program failed | 이상한 CIF나 프로파일·파장 불일치가 흔한 원인. 의심 CIF를 빼고 장비 프로파일 확인. |
| The parallel worker (Ray) could not start | 다른 DARA 창을 닫거나 PC 재시작. 보안 프로그램이 로컬 포트를 막는지 확인. |
| 질량 균형 경고가 뜸 | 배너나 Review cautions에 "Mass balance: …"가 보이면 먼저 전구체 양이 맞는지 확인. 파일 이름에서 읽었다는 표시가 있으면 이름 속 계수가 실제 칭량과 같은지 점검. 다르면 Copy conditions & rerun에서 Precursor / expected formulas에 양을 적어 다시 제출. 입력이 맞다면 08장 질량 균형 편차 해석 참조. |
| 재정밀화가 오래 걸림 | 진행 막대의 단계와 남은 시간 확인. 대부분 BGMN 계산 시간이며 배경 다항식 항 수와 상 수가 많을수록 늘어남(Task 9: Auto 16 s, 12항 98 s). Background…에서 Auto로 되돌리거나 항 수를 줄이고, 불필요한 상은 Remove로 뺀 뒤 다시 시도. 기다리기 어려우면 Cancel. |
| 재정밀화 취소·실패 | Cancel하면 버전이 생기지 않음. 빨간 "Re-refinement failed" 알림이 뜨면 문구 확인 후 다시 시도. 다른 재정밀화가 도는 중에는 Add and re-refine, Swap 등이 꺼져 있으므로 끝날 때까지 기다리거나 Cancel. |
| 실패 작업을 다시 돌리고 싶음 | 작업 페이지의 Retry with suggested fix 또는 Retry unchanged, Results 목록의 Retry 사용. EFLECH 시간 초과는 자동 조치가 없으므로 2θ 범위를 좁혀 Copy conditions & rerun으로 재제출(06장 실패 작업 재시도 참조). |
| 피팅이 나쁨 | 파장, 장비 프로파일, 원소 목록, 후보 CIF, 분석 범위 순으로 점검. Rwp를 낮추려고 중요한 피크를 제외 구간으로 지우지 말 것. |

### 폴더 안내

START.cmd만 실행하면 됨. src, scripts, pyproject.toml은 실행에 필요하므로 지우거나 옮기지 말 것. tools와 .venv는 첫 실행 때 생기는 폴더이며 Git에 올라가지 않음. artifacts에는 로그와 시험 출력이 쌓임. 분석 DB는 사용자 홈의 .dara-server에 있으므로 백업은 서버를 끈 뒤 그 폴더를 따로 복사.

> 문서 근거: README.md, docs/user-guide/results-confidence.md, scripts/start_dara_web.ps1, src/dara/server 소스. PDF 재생성: python docs/user-guide/build_manual.py(reportlab 필요).
