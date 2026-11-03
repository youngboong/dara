# DARA 한글 사용 설명서

분말 XRD 상 동정과 Rietveld 정밀화를 자동으로 처리하는 웹 프로그램

> 기준 버전: 저장소 main 브랜치(2026-10-30). 저장소 [youngboong/dara](https://github.com/youngboong/dara).
> 화면 문구는 앱의 영어 표기를 그대로 적는다. 예: Submit, Results, High confidence.

| 장 | 내용 |
| --- | --- |
| 01 개요 | DARA의 용도와 주요 기능 |
| 02 준비물 | 필수·선택 준비물, MP API 키, GEQ, CIF |
| 03 설치와 실행 | 내려받기, START.cmd 첫 실행, MP 키, 종료와 스위치 |
| 04 분석 제출 | 분석 모드, 전구체 양, DB, 속도 조절, 장비·파장, 2θ 범위, CIF, 재실행 |
| 05 시리즈 분석 | 여러 패턴 일괄 제출과 추세 그래프 |
| 06 결과 보기 | 결과 목록, 신뢰도 배너와 상별 판정, 미설명 피크, 그래프 조작, 질량 균형, 빠른 재정밀화와 버전, 상 추가·교체·제거, 두 피팅 비교, 실패 작업 재시도 |
| 07 보고서와 내보내기 | 논문형 보고서, 그림, CSV, 외부 프로그램용 파일 |
| 08 결과 해석 시 주의 | 상대 wt%, 불확도, 계산 구조, 질량 균형 해석, 검토 원칙 |
| 09 문제 해결 | 자주 겪는 상황과 대처 |

## 01 개요

DARA는 분말 X선 회절(XRD) 패턴에서 결정상 후보를 자동으로 찾고 BGMN 엔진으로 Rietveld 정밀화까지 끝내는 웹 프로그램이다. Ceder 그룹이 공개한 원본(Chemistry of Materials, 2026)을 바탕으로 설치, 입력 형식, 결과 검토, 보고서 기능을 더한 맞춤판이다. 자동 결과를 그대로 확정해 주는 도구는 아니다. 대신 판정 근거를 함께 보여 줘서 검토할 곳을 좁혀 준다.

- 패턴 한 개나 여러 개(시리즈)를 올리면 상 동정과 정량을 한 번에 끝낸다
- 분석 모드는 Phase identification, Known-phase refinement, Reaction check의 3종이다
- 결과마다 신뢰도 배너(High confidence / Check recommended / Expert review recommended)와 상별 판정이 붙는다
- 설명되지 않은 피크는 significant / minor / instrument / noise로 나누고 미량상 후보를 제시한다
- 완료된 작업에서 상을 추가·제거·교체하면 바로 재정밀화된다. 결과는 버전으로 쌓이고 두 피팅을 나란히 비교할 수 있다
- 논문형 영문 보고서, 그림(PNG/SVG), CSV 표, Profex·TOPAS·GSAS-II·HighScore용 내보내기를 지원한다

상단 메뉴는 Submit, Results, Series, Tutorial, Documentation 순서로 놓여 있고 서버 주소는 http://localhost:8898이다.

> 이 설명서의 그림은 2026-10-27~10-30에 캡처한 화면이다. 시료 이름과 수치는 조작을 설명하려고 넣은 값이므로 검증된 분석 결과로 인용하면 안 된다.

## 02 준비물

| 구분 · 항목 | 내용 |
| --- | --- |
| 필수 · Windows PC | Windows 10/11 64비트에 여유 공간 약 3 GB가 필요하다. 폴더는 OneDrive 동기화 폴더 밖의 짧은 경로(예: C:\DARA)에 두어야 한다. |
| 필수 · 인터넷(첫 실행) | uv, Python 3.12, 패키지, BGMN을 내려받는다(약 1 GB). 설치가 끝나면 오프라인으로도 실행되지만 COD/MP 구조 검색에는 계속 인터넷이 필요하다. |
| 필수 · 측정 패턴 | .xrdml을 권장한다. 다른 지원 형식은 4장에 정리되어 있다. |
| 필수 · 시료 정보 | 포함 원소, 전구체나 예상 화학식, 측정 파장이 필요하다. Reaction check로 돌리려면 열처리 온도와 분위기도 있어야 한다. |
| 선택 · MP API 키 | Materials Project의 계산 구조를 후보로 쓸 때 필요하다. 키가 없어도 COD만으로 분석된다. |
| 선택 · 장비 프로파일(GEQ/GER) | 기본 목록에 실제 장비·광학계와 맞는 항목이 없을 때만 등록한다. |
| 선택 · 참조 CIF | DB에 없는 상이나 연구실에서 검증한 구조를 추가할 때 쓴다. |

Python, Git, Anaconda는 미리 설치하지 않아도 된다. 필요한 도구는 START.cmd가 DARA 폴더 안에 직접 설치한다.

### MP API 키 받기

MP API 키는 무료이고 가입하면 자동으로 만들어진다. 키는 3장의 START.cmd 첫 실행 때 입력한다.

1. 브라우저로 [https://materialsproject.org](https://materialsproject.org/)에 접속한다. 처음 들어가면 사람 확인(Cloudflare) 화면이 잠깐 나올 수 있다.
2. 오른쪽 위 로그인 메뉴에서 가입하거나 로그인한다. 이메일이든 연동 계정이든 편한 쪽을 고르면 된다.
3. 로그인한 상태에서 위쪽 메뉴의 API를 클릭한다(주소 [https://next-gen.materialsproject.org/api](https://next-gen.materialsproject.org/api)).
4. API Key 항목의 "Your API Key" 상자에 나온 영문·숫자 문자열 전체를 앞뒤 공백 없이 한 줄 그대로 복사한다.
5. 복사한 키는 START.cmd 첫 실행 때 뜨는 입력 창에 붙여 넣는다(그림 1).

- 키는 비밀번호처럼 다뤄야 한다. 공개 문서, 메신저, Git에 올리면 안 된다.
- 키가 새어 나갔다면 같은 페이지 아래 dashboard 링크에서 새 키를 받고 예전 키는 폐기한다. 새 키를 넣는 방법은 3장에 있다.

### GEQ 장비 파일 구하기

먼저 Instrument profile 목록에서 실제 장비, 검출기, 슬릿 조건에 맞는 항목을 찾는다. 맞는 항목이 있으면 따로 등록할 필요가 없다. 목록에 없을 때는 장비 담당자에게 BGMN/Profex용 .geq(대응 .ger 포함)를 요청하거나 [Profex](https://www.profex-xrd.org/)의 Instrument → Edit Configuration에서 실제 하드웨어 값으로 직접 만든다. .xrdml 측정 파일의 확장자만 바꾼다고 .geq가 되지는 않는다.

### 참조 CIF 구하기

[COD](https://www.crystallography.net/cod/), 논문 보충자료, 이용권이 있다면 ICSD에서 구할 수 있다. 쓰기 전에 원소, 공간군, 출처를 확인한다. 검색 결과 HTML을 .cif로 이름만 바꾼 파일은 읽히지 않는다.

## 03 설치와 실행

### 내려받기

[https://github.com/youngboong/dara](https://github.com/youngboong/dara)에서 Code → Download ZIP을 선택한다. 내려받은 ZIP을 오른쪽 클릭해 "모두 압축 풀기"를 실행한다. 압축 파일 안에서 바로 실행하면 설치가 실패한다. START.cmd, src, scripts, pyproject.toml이 한 폴더에 보이면 준비는 끝났다.

### START.cmd 실행

START.cmd를 더블클릭한다. 첫 실행은 아래 순서를 자동으로 밟으며 5~15분 걸린다. 도중에 창을 닫으면 안 된다.

| 단계 | 내용 |
| --- | --- |
| 1. uv, Python | uv와 Python 3.12를 DARA 폴더의 tools\ 안에 설치한다 |
| 2. .venv | 프로젝트 환경을 만든다 |
| 3. BGMN | 정밀화 프로그램이 없으면 내려받는다 |
| 4. MP 키 | 처음 한 번 키 입력 창이 뜬다(그림 1): Save and continue 또는 Skip for now |
| 5. 시작 화면 | 서버가 준비되면 http://localhost:8898로 넘어간다 |

중간에 끊겨도 START.cmd를 다시 실행하면 멈춘 곳부터 이어 간다. 두 번째 실행부터는 설치 없이 바로 서버가 뜬다.

### MP 키 입력

START.cmd 첫 실행의 4단계에서 아래 창이 한 번 뜬다.

![그림 1. START.cmd 첫 실행 때 뜨는 Materials Project API 키 입력 창. 입력한 키는 점으로 가려 보인다.](images/mp-key-dialog.png){height=170}

(1) Open Materials Project to get an API key: 키가 아직 없을 때 누르면 MP 사이트가 열린다. 그다음은 2장 MP API 키 받기 순서를 따르면 된다.
(2) 키 입력 칸: 복사한 키를 Ctrl+V로 붙여 넣는다. 화면에는 점으로만 보인다.
(3) Save and continue: 키를 저장하고 설치를 이어 간다. 칸이 비어 있으면 저장되지 않고 안내 문구가 뜬다.
(4) Skip for now: 키 없이 넘어간다. 이후 실행에서는 다시 묻지 않는다.

- 저장한 키는 Windows 사용자 환경변수 MP_API_KEY에 남아 다음 실행부터 자동으로 쓰인다.
- 키 상태는 Submit 화면의 MP 스위치 옆 태그에 나오며 Re-check를 누르면 다시 조회한다.
- 태그 뜻: MP key valid는 정상이다. MP key rejected는 키가 틀렸거나 폐기된 상태, MP key not configured는 키가 없는 상태, MP key not verified는 인터넷 문제로 확인하지 못한 상태다.
- 키 없이 쓸 때는 Reference databases에서 MP를 끄고 COD로 분석한다.

### MP 키 바꾸기·나중에 넣기

키를 잘못 넣었거나 새로 받았거나 처음에 Skip for now를 눌렀다면 입력 창을 다시 열어 키를 넣으면 된다.

1. DARA 폴더(START.cmd가 있는 폴더)를 탐색기로 연다.
2. 탐색기 주소창에 cmd를 입력하고 Enter를 누른다. 그 폴더 위치에서 명령 창이 열린다.
3. START.cmd -SetApiKey를 입력하고 Enter를 누르면 그림 1의 입력 창이 다시 뜬다.
4. 새 키를 붙여 넣고 Save and continue를 누른다. Skip for now를 누르면 기존 키가 그대로 남는다.
5. 서버가 켜져 있었다면 "Restart required" 안내가 뜬다. 새 키는 START 창을 닫고 START.cmd를 다시 실행해야 적용된다.

### 종료, 여러 창, 스위치

- 마지막 DARA 브라우저 창을 닫으면 약 20초 뒤 서버가 스스로 꺼진다. 대기 중이거나 실행 중인 분석이 있으면 그 분석이 끝날 때까지 서버는 켜진 채로 남는다. 바로 끄려면 START 창에서 Ctrl+C를 누른다.
- 서버가 떠 있을 때 START.cmd를 다시 실행하면 새 브라우저 창만 열린다. 창 여러 개를 함께 써도 문제없다.
- 선택 스위치: -Reinstall(.venv 재설치), -Offline(네트워크 미사용), -SetupOnly(설치만), -NoBrowser(브라우저 안 엶), -SkipApiKeySetup(MP 키 입력 창 생략), -SetApiKey(MP 키 입력 창 다시 열기).

## 04 분석 제출

![그림 2. Submit 화면 위쪽. 패턴 업로드, 분석 모드, 원소·화학식, DB, 속도 조절 항목이 차례로 놓인다.](images/submit-top.png){height=470}

(1) User: 제출자를 구분하는 이름을 적는 칸.
(2) XRD pattern: 측정 파일을 끌어 놓거나 눌러서 고른다.
(3) Analysis mode: Phase identification, Known-phase refinement, Reaction check 중 하나를 선택한다.
(4) Included elements / Precursor / expected formulas: 원소 기호와 예상 화학식을 넣는 칸.
(5) Reference databases: COD·MP 사용 여부를 정하고 MP E-hull cutoff와 Maximum MP structures를 지정한다.
(6) Candidate pre-filter, Reference screening limit (s), Maximum analysis time (min): 속도 조절 3종.

### 패턴 올리기

지원 형식은 .xrdml, .xy, .xye, .txt, .csv, .dat, .asc, .scn, .ras, .rasx, .brml, .raw(Bruker RAW v2만)이다. 읽기에 실패하면 몇 번째 줄이 문제인지 알려 준다.

- XRDML 파일은 메타데이터를 읽어 가장 가까운 Instrument profile과 Wavelength를 자동으로 고른다. 실제 슬릿·광학계와 다르면 직접 고쳐야 한다.
- Data quality pre-check (advisory): 강도 정규화, S/N, 스텝 간격, 측정 범위에 대한 경고. 참고용이라 제출을 막지는 않는다.
- 파일 이름에서 화학식을 읽어 원소를 제안한다(예: BiFeO3_800C_60min.xrdml → Bi, Fe, O). 제안된 원소는 실제 시료와 맞춰 본 뒤 사용한다.

### Analysis mode 고르기

| 모드 | 쓰임과 입력 |
| --- | --- |
| Phase identification | 원소계 안에서 가능한 상을 탐색한다. Included elements 입력이 필수다(예: Bi, Fe, O). Precursor / expected formulas에 적은 상은 후보에 반드시 포함된다. |
| Known-phase refinement | 구성 상을 이미 아는 경우에 알맞다. Phases to refine에 적은 상만 정밀화하고 미지 불순물은 찾지 않는다. |
| Reaction check | 전구체, Temperature (°C), Atmosphere로 생성물을 예측한 뒤 피팅한다. 후보가 적어 가장 빠르며 MP 데이터를 쓴다. |

잔류 전구체나 홀더 성분의 원소를 Included elements에서 빠뜨리면 그 상은 처음부터 후보에 들지 못한다.

### Reference databases

COD(experimental)에는 실험 구조가, MP(computed supplement)에는 보충용 계산 구조가 들어 있다. MP E-hull cutoff(기본 0.05 eV/atom)와 Maximum MP structures(기본 100)를 늘리면 후보도 계산 시간도 함께 늘어난다.

![그림 3. Precursor / expected formulas 칸에 몰 계수를 넣은 예(위)와 칸 도움말(아래). 양을 적으면 결과에서 질량 균형 점검이 켜진다.](images/submit-precursor-amounts.png){height=250}

(1) Included elements: 원소 기호를 넣는다(예: Fe, Ti, O).
(2) Precursor / expected formulas: 전구체와 양 입력. 예시는 Fe2O3 1몰, TiO2 2몰이다.
(3) 도움말 아이콘. 마우스를 올리면 입력 형식 안내가 뜬다.
(4) 도움말 내용: 화학식 앞 몰 계수(Fe2O3, 2 TiO2) 또는 콜론 뒤 칭량 질량(In2O3:0.3022g, TiO2:0.2004g).

### 전구체 양 입력

Precursor / expected formulas에 전구체 양까지 적으면 결과에서 질량 균형을 점검한다(6장). 양을 적지 않으면 점검을 건너뛴다.

- 몰 계수: Fe2O3, 2 TiO2 (계수가 없는 항목은 1)
- 칭량 질량: In2O3:0.3022g, TiO2:0.2004g (단위는 g, mg, mol, mmol. 생략하면 g)
- 양은 모든 항목에 적어야 한다. 일부만 적으면 점검을 건너뛴다. 한 항목에 계수와 질량을 함께 쓰면 오류가 난다.
- 칸에 양이 없으면 파일 이름에서 읽는다(예: 2Fe3O4-3Y2O3_1000C_60min, In2O3_0p3022-TiO2_0p2004). 칸 입력이 우선하며 주의할 점은 8장에서 다룬다.

### 속도 조절 3종

<!-- widths: 140,347 -->
| 항목 | 동작 |
| --- | --- |
| Candidate pre-filter | 기본으로 켜져 있다. 참조 구조의 강한 피크가 측정 패턴에 없으면 그 구조를 미리 뺀다. 업로드·라이브러리·전구체 상은 빼지 않고 남겨 둔다. |
| Reference screening limit (s) | 선별 단계에서 참조 하나에 쓰는 BGMN 최대 시간. 기본값은 300 s이고 입력 범위는 60~1200이다. |
| Maximum analysis time (min) | 5~720분. 시간이 다 되면 그때까지 얻은 최선 결과를 미완료 표시와 함께 돌려준다. 비우면 제한이 없다. |

예상 후보 수와 Estimated run time은 대략적인 값이고 조건을 바꿀 때마다 새로 계산된다.

![그림 4. Submit 화면 아래쪽. 예상 실행 시간, 장비 프로파일, Final refinement controls, 파장, 2θ 범위, CIF 입력.](images/submit-estimate.png){height=460}

(1) 예상 후보 수와 Estimated run time 안내. 조건을 바꾸면 값도 바뀐다.
(2) Instrument profile: 장비 조건을 고른다. 맞는 항목이 없으면 Register instrument profile로 등록한다.
(3) Final refinement controls: Lattice parameters, Preferred orientation, Sample peak broadening.
(4) Synchrotron / Wavelength: 광원과 파장을 정한다.
(5) Analysis range · 2θ (°) / Excluded intervals · 2θ (°): 분석 범위와 제외 구간.
(6) Reference library CIFs / Additional reference CIFs: 참조 CIF를 넣는 곳.
(7) Submit: 누른 뒤 확인 창에서 확정하면 작업이 생성된다.

### 장비 프로파일과 파장

- 맞는 Instrument profile이 없으면 Register instrument profile에서 Compiled GEQ (required)와 Matching GER (optional)을 올려 등록한다. 등록과 교정은 별개이므로 표준시료를 피팅해 따로 검증해야 한다.
- 실험실 광원은 Wavelength에서 Cu, Co, Cr, Fe 등을 고른다. Synchrotron을 켜면 Wavelength (nm)에 값을 직접 입력한다(1 Å = 0.1 nm).
- XRDML의 파장과 선택한 파장이 다르면 제출 때 "The selected wavelength differs from the file" 창이 뜬다. Submit anyway와 Go back and check 중 하나를 고르면 된다.

### 2θ 범위, 최종 정밀화 조건

- Analysis range · 2θ (°)를 비워 두면 측정 범위 전체를 쓴다. Add excluded interval은 홀더나 장비 아티팩트가 확인된 위치에만 쓴다. 범위나 제외 구간이 다른 작업끼리는 Rwp를 직접 비교하면 안 된다.
- Final refinement controls는 처음에는 Keep saved/default로 둔다. 결과에서 문제를 찾은 뒤 한 항목씩 바꿔 가며 비교한다. 자유도를 늘려 Rwp가 내려가도 그것만으로 상 동정이 입증되지는 않는다.

### 참조 CIF 넣기

Reference library CIFs는 서버에 저장해 두고 여러 작업에서 다시 쓰는 구조다(Manage library → Add CIF to library). Additional reference CIFs(Select CIF files)는 이번 작업에만 쓰는 구조다. 같은 상의 CIF를 여러 개 넣으면 정량값이 쪼개지므로 상 하나에는 CIF 하나만 넣어야 한다.

### 제출과 조건 재사용

맨 아래 Submit을 누르고 확인 창에서 확정한다. 기존 작업에서 Copy conditions & rerun을 누르면 Submit 화면이 열리고 원래 패턴과 조건이 채워져 있다. 바꿀 항목만 고쳐 제출하면 새 작업이 생기고 원본은 그대로 남는다.

## 05 시리즈 분석

시리즈 분석은 온도별, 시간별, 조성별처럼 같은 조건으로 측정한 패턴 여러 개를 한 번에 분석해 추세를 보는 기능이다.

- Submit 화면에서 Submit as a series를 켠다.
- Series name을 입력한 뒤 XRD patterns에 파일 2~50개를 한꺼번에 올린다. 형식과 측정 조건이 같아야 한다.
- Series order preview에 파일 이름에서 읽은 변수(예: 200 °C, 60 min)와 순서가 나온다. 이 값은 그래프 축에만 쓰인다.
- 각 파일은 같은 설정의 독립 작업으로 처리된다.

상단 Series 메뉴에서 시리즈를 고르면 작업 표와 추세 그래프 3종이 나온다. 그래프는 완료된 작업이 2개 이상이어야 그려진다.

- Phase fractions: 상별 wt%와 오차막대
- Lattice parameters: 격자 변화
- Fit quality: 시료별 Rwp와 GOF
- Export CSV로 표를 저장하고 Compare patterns로 패턴을 겹쳐 본다

## 06 결과 보기

![그림 5. Results 목록. 상태와 신뢰도, Best Rwp, GOF, 최종 상, 채택 여부가 한눈에 들어온다.](images/results.png){height=270}

(1) Filter file name / Filter username: 파일 이름이나 사용자로 검색한다.
(2) Compare selected / Export table: 체크한 작업을 겹쳐 보거나 결과 표를 CSV로 저장한다.
(3) Status: 작업 상태 태그(COMPLETED, FIZZLED 등). 끝난 작업은 옆에 신뢰도 태그(High confidence, Check recommended, Expert review recommended)가 붙어 목록에서 바로 검토 우선순위를 정할 수 있다.
(4) Best Rwp (%) / GOF: 최저 Rwp 결과의 적합도.
(5) Final phases: 최종 정밀화된 상.
(6) Adopted: 채택한 결과인지 표시한다.
(7) Actions: 결과를 내려받거나(Download) 작업을 지운다(Delete).

| Status | 의미 |
| --- | --- |
| READY / RUNNING | 대기 중 / 분석 중. 진행 단계는 작업 페이지에 나온다. 필요하면 Stop analysis를 누른다 |
| COMPLETED | 완료. 신뢰도 배너부터 본다 |
| FIZZLED | 실패. 태그에 마우스를 올리면 원인 요약이 뜬다. 재시도 방법은 이 장 끝에 있다 |
| CANCELLING / CANCELLED | 중단 처리 중 / 중단 완료 |

![그림 6. High confidence 예시(Task 9). minor 미설명 피크 3개는 판정에 영향이 없다. 이 피크들에는 위치가 미량 Fe2O3와 맞는다는 힌트가 붙는다.](images/task-high.png){height=300}

(1) Copy conditions & rerun: 같은 패턴·조건이 채워진 Submit 화면이 열린다.
(2) Download all results: 계산 산출물 전체 ZIP.
(3) Table (CSV), Figure (PNG), Figure (SVG): 결과 표와 피팅 그림 저장.
(4) Export for...: Profex, TOPAS, GSAS-II, HighScore용 내보내기.
(5) 신뢰도 배너. 등급과 그 등급이 나온 이유를 보여 준다.
(6) How is this assessed?: 판정 기준 설명이 열린다.
(7) Adopted result & lab notes: 채택 후보 선택과 메모.

### 신뢰도 배너

배너 아래 항목은 그 등급이 나온 이유다. 등급은 통계량이 문턱값을 넘었는지로 정한 판정이며 확률을 뜻하지 않는다.

| 등급 | 조건(하나라도 해당하면 그 등급) |
| --- | --- |
| High confidence | 아래 두 등급 조건에 하나도 해당하지 않는다. GOF 3 이하, significant 미설명 피크 없음, 모든 상 Confident. |
| Check recommended | Ambiguous 또는 Weak evidence 상 존재(5 wt% 미만), GOF 3 초과, significant 피크 1~3개. 시료 변위 Warning, 넓은 배경 언덕. 선별 시간을 넘긴 참조 존재, 최대 분석 시간 도달로 중단, 질량 균형 편차 0.20 초과. |
| Expert review recommended | 5 wt% 이상인 상이 Weak evidence, significant 피크 4개 이상 또는 높이 합 20% 이상, Rwp 25% 초과, GOF 4 초과. 시료 변위 Severe, 최종 정밀화 절반 이상 실패, 참조의 25% 이상 미선별. |

### 상별 판정(Verdict)

정밀화된 상마다 Confident, Ambiguous, Weak evidence 중 하나가 붙는다. 태그에 마우스를 올리면 근거 수치가 보인다.

- wt%가 표준편차의 3배 이상이면 Confident 쪽이고 2배 미만이거나 1 wt% 미만이면 Weak다.
- 그 상이 빠진 다른 후보의 Rwp가 거의 같으면 Ambiguous가 붙고 툴팁에 대안 후보 이름이 나온다.
- MP 계산 구조는 격자가 참조값에서 1.5% 넘게 움직이면 감점된다.

![그림 7. Check recommended 예시(Task 23). significant 미설명 피크 2개(31.93°, 46.58°) 때문에 등급이 내려갔다. 점선 위치에서 중간 표를 생략했다.](images/task-check.png){height=320}

(1) Check recommended 배너.
(2) 등급을 낮춘 이유: significant 미설명 피크 2개(31.93°, 46.58°).
(3) Fit diagnostics의 Unexplained 태그: significant·minor 피크 수.
(4) Refinement quality 지표: Rwp, Rp, Rexp, GOF, χ², DW, 2θ 범위, 데이터 점 수, 반복 수.
(5) Specimen displacement 태그: 시료 변위(mm).
(6) Review cautions (automated checks, not a verdict): 자동 점검 결과를 모아 둔 곳.

### 미설명 피크 등급

Unexplained 태그의 숫자는 계산 패턴이 재현하지 못한 관측 피크의 개수다. 피크는 높이(최강 피크 대비)와 S/N으로 나눈다.

<!-- widths: 90,397 -->
| 등급 | 기준과 영향 |
| --- | --- |
| significant | 최강 피크의 2% 이상이면서 S/N 8 이상. 등급 판정에 반영되는 유일한 종류다 |
| minor | noise와 significant 사이. 정보로만 보여 준다 |
| instrument | 강한 반사의 Cu Kβ, W Lα1 위치와 일치하는 약한 피크. 장비에서 생겼을 가능성이 있다 |
| noise | 최강 피크의 0.5% 미만이거나 S/N 3 미만 |

미설명 피크 위치가 어떤 상의 강한 반사와 맞으면 "Positions are consistent with trace ..." 형태로 미량상 후보를 알려 준다. 이 힌트는 판정에 반영되지 않는다. 힌트로 나온 상은 Add phase로 넣어 검증해 보면 된다. 검색 범위는 이 장의 Add phase 창에서 설명한다.

### Review cautions와 시료 변위

- Review cautions: 자동 점검 결과 모음. 예: Durbin–Watson 1 미만(esd 과소평가 가능), 넓은 배경 언덕, 미설명 피크 요약.
- Specimen displacement: 0.3 mm 이하 OK, 0.3 mm 초과 Info, 1.0 mm 초과 Warning, 2.0 mm 초과 Severe.
- 변위가 크면 모든 피크가 함께 밀린다. 이때는 격자상수와 상 매칭 결과를 조심해서 읽어야 한다. 시료 높이와 장비 프로파일부터 다시 살펴본다.

![그림 8. 작업 페이지 아래쪽(Task 18). 위는 상별 판정·정량·격자 표, 아래는 Candidate comparison 표. 점선 위치에서 Versions 표 등 중간 부분을 생략했다.](images/task-high-lower.png){height=420}

(1) Verdict: 상별 판정(Confident, Ambiguous, Weak evidence).
(2) Quantity / Lattice parameters: wt%와 격자상수, 표준편차.
(3) Compare fits: 비교 창이 열린다. 처음에는 A에 Best fit, B에 다음 후보가 들어간다. 후보가 둘 이상일 때만 쓸 수 있다.
(4) Confidence: 후보별 신뢰도 태그. 열 머리글로 정렬할 수 있다.
(5) View fit: 후보 피팅을 본다.

### 후보 비교와 채택

- Candidate comparison 표는 Rwp, GOF, Missing, Extra, Intensity mismatch, Cautions, Confidence 열로 정렬할 수 있다.
- Adopted result & lab notes에 메모를 적고 Save review로 저장한다.
- 채택 버튼은 Versions 표의 Adopt(그림 13)와 비교 창의 Adopt B(그림 16) 두 곳에만 있다. 후보를 채택하려면 비교 창에서 그 후보를 B로 고른 뒤 Adopt B를 누른다.
- 자동 최저 Rwp 결과와 채택 결과는 따로 저장된다.

## 06 결과 보기 · 피팅 그래프와 질량 균형

![그림 9. View fit 창의 피팅 그래프(Task 9). 오른쪽 버튼으로 끌어 확대 상자를 그리는 장면.](images/fit-navigation.png){height=330}

(1) Fit intensity to view: 기본으로 켜져 있다. 보이는 2θ 구간에 맞춰 강도 축이 자동으로 조정된다.
(2) Reset zoom: 전체 패턴으로 돌아간다. 그래프를 더블클릭해도 같다.
(3) 조작 안내 문구. 마우스 조작, 범례 클릭, hover 정보를 요약한다.
(4) 오른쪽 버튼 끌기로 그린 확대 상자(파란 점선). 버튼을 놓으면 그 범위로 확대되고 끄는 중에 Esc를 누르면 취소된다.
(5) 아래 패널(관측-계산). 여기서 그린 상자는 2θ 범위만 바꾼다.
(6) 그림 저장(카메라) 버튼.

### 그래프 조작

<!-- widths: 150,337 -->
| 동작 | 결과 |
| --- | --- |
| 왼쪽 버튼 끌기 | 그래프 이동 |
| 오른쪽 버튼 끌기 | 확대 상자. Esc로 취소 |
| 마우스 휠 | 2θ 확대·축소 |
| 더블클릭, Reset zoom | 전체 패턴 보기로 복귀 |
| 범례의 상 이름 클릭 | 그 상의 프로파일과 Bragg 위치 표시 |
| 점에 마우스 올리기 | 2θ, d(Å), 강도 표시. Bragg 반사 표시에서는 hkl도 나온다 |

피팅 창과 비교 창 모두 같은 방식으로 움직인다. 피팅 창에서 ?를 누르면 단축키 목록이 열린다.

![그림 10. 질량 균형 caution 예시(Task 22, 2Fe3O4-3Y2O3). 신뢰도 배너 이유(위)와 Review cautions(아래). 점선 위치에서 중간 표를 생략했다.](images/task-mass-balance.png){height=200}

(1) 신뢰도 배너. caution 수준이라 등급은 High confidence 그대로다.
(2) 배너 이유 목록에 붙은 "Mass balance: …" 문장. 상 분율로 계산한 Fe:Y = 0.67:0.33, 파일 이름의 전구체 비율 0.50:0.50, 최대 편차 0.17.
(3) Review cautions의 같은 내용(info).

### 질량 균형 점검 읽기

이 점검은 전구체 양이 있을 때만 동작한다(4장 전구체 양 입력). 정밀화된 wt%를 원소 몰분율로 바꾼 뒤 전구체의 원소 비와 비교한다. 등급은 원소별 차이 중 가장 큰 값(최대 편차)으로 정하며 O, H, C, N은 비교에서 빠진다.

<!-- widths: 110,377 -->
| 최대 편차 | 결과 |
| --- | --- |
| 0.10 이하 | 메시지 없음 |
| 0.10~0.20 | caution. 배너 이유와 Review cautions에 한 줄이 추가된다. 등급은 바뀌지 않는다 |
| 0.20 초과 | warning. 종합 등급은 최소 Check recommended가 된다 |

- 예: Fe2O3 + 2 TiO2가 Fe2TiO5 + TiO2로 반응했다면 75/25 wt%가 나와야 한다. 26.8/73.2로 정량되면 Fe:Ti = 0.18:0.82(편차 0.32)가 되어 warning이 뜬다.
- 경고가 뜨면 전구체 양과 파일 이름 속 계수가 실제 칭량과 같은지부터 대조한다. 다르면 Copy conditions & rerun에서 양을 적어 다시 제출한다. 입력이 맞다면 8장으로 넘어간다.

## 06 결과 보기 · 결과 수정과 버전

완료된 작업에서 상을 더하거나 빼면 그 자리에서 다시 정밀화할 수 있다(빠른 재정밀화). 결과는 같은 작업 안에 v2, v3 … 버전으로 쌓이고 원래 자동 분석(v1)은 그대로 남는다.

![그림 11. Add phase 창에서 미량상 Fe2O3의 Add를 누른 직후(Task 9). 피팅 창 위쪽에 진행 막대가 뜬다. 예전 코드로 끝난 작업이라 자동 기준 재계산이 먼저 도는 중이어서 Queued (1 ahead)로 표시된다.](images/quick-progress.png){height=110}

(1) Add phase…: 상 추가 창이 열린다(그림 14).
(2) 진행 막대의 작업 이름(Added Fe2O3 (hint))과 현재 단계. 여기서는 앞선 기준 재계산을 기다리고 있다(Queued (1 ahead)).
(3) 경과 시간. 실행 단계에 들어가면 남은 시간 추정(… left 또는 estimating…)도 함께 나온다.
(4) Cancel: 진행 중인 재정밀화를 취소한다.

### 빠른 재정밀화

- 시작하는 곳: 피팅 창의 Add phase…, Select phases…, 상 표의 Swap·Remove, Advanced → Background….
- DB를 다시 검색하지 않고 바꾼 상 조합만 정밀화한다. 패턴, 장비, 파장, 2θ 범위, 제외 구간, Final refinement controls는 원래 값을 그대로 가져간다.
- 대부분 수십 초 안에 끝나고 한 작업에서는 한 번에 하나만 실행된다.
- 피팅 창을 닫아도 계산은 계속되지만 Cancel하면 버전이 만들어지지 않는다.
- 끝나면 "Version vN ready (Rwp …%)" 알림과 함께 새 버전이 열린다. Select phases…, Swap, Remove로 만든 버전은 비교 창이 바로 열린다.
- Select phases…: 이 작업에 저장된 참조 상 목록에서 넣을 상만 체크하고 Re-refine and compare를 누른다. 의심스러운 상을 빼 보거나 다른 참조로 바꿔 비교할 때 알맞다.
- Advanced → Background…: 배경 다항식 항 수와 제외 구간을 바꾼다. 항 수를 늘리면 계산이 크게 느려진다. 배경을 바꾼 버전의 Rwp는 같은 조건의 피팅끼리만 비교해야 한다.
- 예전 버전에서 끝난 작업은 첫 재정밀화 때 "Baseline (current code)" 버전(자동 기준 재계산)이 먼저 생긴다. 이후 비교는 이 버전이 기준이 된다.

![그림 12. 재정밀화가 끝난 뒤 버전 v3(Fe2O3 추가)를 연 피팅 창(Task 9). 기준 피팅 대비 변화가 태그로 나온다.](images/version-delta.png){height=150}

(1) 표시할 피팅을 고른다. Best fit, 후보, 버전(v2, v3 …) 순으로 나열된다.
(2) Change from the base fit: 기준 피팅 대비 Rwp, GOF, χ², 미설명 피크 수, 상별 wt% 변화. 좋아진 값은 초록, 나빠진 값은 주황으로 표시된다. 여기서 기준은 자동 기준 재계산 버전 v2다.
(3) 이 버전의 신뢰도 등급. 버전마다 새로 판정된다.

![그림 13. Versions 표(Task 9). v1 자동 분석, v2 자동 기준 재계산, v3 힌트 추가, v4 배경 변경. v3를 채택한 상태.](images/versions-panel.png){height=250}

(1) Versions 제목과 채택 표시(v3 adopted).
(2) 채택한 버전의 Version 칸에 붙는 ★.
(3) Origin: 버전을 만든 방법.
(4) Rwp (%), GOF, Phases, Unexplained: 버전별 적합도, 상 목록, 미설명 피크 수.
(5) View / Compare: 피팅 창에서 열거나 비교 창을 연다. 비교 창의 A에는 채택 결과가 들어가고 채택이 없으면 기준 피팅이 들어간다.
(6) Adopt: 이 버전을 채택하고 현재 lab notes도 함께 저장한다. 채택된 행에는 Adopted가 표시된다.
(7) Report: 이 버전의 논문형 보고서.
(8) Export: 이 버전을 Profex, TOPAS, GSAS-II, HighScore 형식으로 내보낸다.
(9) 삭제(휴지통). v1과 채택 중인 버전은 삭제할 수 없다.

### Versions 표

Versions 표는 작업 페이지의 Final refined phase parameters 표 아래에 있다. Origin은 Automatic(v1), Hint, Peak search, Swap / Remove, Background, Manual(Select phases…, 기준 재계산) 중 하나다. 채택한 버전은 Report (saved choice)와 기본 내보내기에 쓰인다.

## 06 결과 보기 · 상 추가와 교체

![그림 14. Add phase 창(Task 9). 미설명 피크 두 개(33.44°, 35.89°)를 고르고 첫 후보의 반사 위치를 미리 띄워 둔 상태. 후보마다 일치 강도 태그가 붙는다.](images/add-phase-drawer.png){height=380}

(1) 고른 피크 위치(빨간 점선). 보라색 세로선은 Preview ticks로 띄운 후보의 반사 위치다.
(2) Selected peaks: 고른 피크 칩(×로 하나씩 해제), Show candidates, Clear.
(3) Suggested by DARA: 미량상 힌트 목록. Add를 누르면 그 상을 넣어 재정밀화한다.
(4) Matches for the significant unexplained peaks: significant 미설명 피크 전체로 자동 검색한 후보 목록. 이 작업처럼 significant 피크가 없으면 그렇다고 알려 준다.
(5) Search by clicked peaks: 고른 피크 위치로 찾은 후보 목록.
(6) 후보 정보: 화학식, 공간군, 출처(This task, Library, COD), 일치 강도 태그(Strong/Possible/Weak match), 점수 막대, 맞은 피크 수, 강한 반사 수, 참조 이름.
(7) Preview ticks / Hide ticks: 후보의 반사 위치를 그래프 위에 겹쳐 본다.
(8) Add: 이 후보를 넣고 재정밀화한다.

### Add phase 창

피팅 창에서 Add phase…를 누르거나 그래프의 피크를 클릭하면 오른쪽에 열린다.

- Suggested by DARA: 미량상 힌트가 줄마다 나오고 오른쪽에 Add가 붙는다. 추가한 버전의 라벨은 "Added … (hint)"다.
- Matches for the significant unexplained peaks: 피팅 창을 열면 significant 미설명 피크 전체로 후보 검색이 자동으로 돈다. 피크를 하나씩 클릭하지 않아도 된다. 쓸 만한 후보(Possible match)가 있으면 Add phase… 버튼에 개수 배지가 붙는다. 위쪽 5개만 보이고 나머지는 개수로 알려 준다.
- Search by clicked peaks: 미설명 피크 표시(삼각형)나 관측점을 클릭하면 그 피크로 후보를 검색한다. Shift+클릭하면 피크를 더할 수 있다(최대 10개). 첫 검색은 15초까지 걸릴 수 있다.
- 일치 강도 태그로 먼저 거른다. Strong match는 고른 피크를 거의 다 설명하고(점수 0.75 이상) 그 상의 강한 반사 중 측정에 없는 것이 15% 이하인 경우다. Possible match는 점수가 0.5를 넘는 경우, Weak match는 일부 피크만 우연히 겹친 경우다.
- 자동 검색 목록은 피크가 여러 상에서 왔을 수 있어 Strong을 붙이지 않는다. significant 피크 1~2개는 모두, 3~4개는 2개, 5개 이상은 3개 이상을 설명하고 측정에 없는 강한 반사가 15% 이하일 때 Possible match로 표시한다.
- 점수만으로 존재를 판정하면 안 된다. Preview ticks로 다른 반사 위치까지 살펴봐야 한다. Add로 넣은 뒤 비교 창에서 Rwp가 뚜렷하게 내려가고 화학적으로도 타당할 때만 채택한다.
- 힌트와 검색은 모두 이 PC에 받아 둔 구조만 대상으로 한다(작업 후보, 입력 CIF, 참조 라이브러리, 로컬 COD 캐시). 실제 상이 여기에 없으면 그 상의 CIF를 구해 입력 CIF나 참조 라이브러리로 추가한다.
- 로컬 CIF가 없는 후보이거나 다른 재정밀화가 도는 중이면 Add가 꺼진다.

![그림 15. 피팅 창의 상 표. 위는 Task 18(Calcite에 Swap), 아래는 Task 19(TiNb·Nb2Al에 View candidate, 대안은 상 조합이 다른 후보). 점선은 서로 다른 작업 화면의 경계.](images/phase-swap.png){height=330}

(1) Ambiguous 판정. 거의 같은 Rwp로 다른 모델을 쓴 후보가 있다.
(2) Swap: 같은 화합물의 다른 구조 모델로 바꿔 재정밀화한 뒤 비교한다.
(3) Remove: 이 상을 빼고 재정밀화한 뒤 비교한다. 상이 2개 이상일 때만 보인다.
(4) View candidate N: 대안 후보 N의 피팅을 저장된 그대로 본다. 다시 계산하지는 않는다.

### Swap, Remove, View candidate

<!-- widths: 110,377 -->
| 버튼 | 동작 |
| --- | --- |
| Swap | 같은 화합물의 다른 구조 모델(다른 공간군·다형)로 이 상만 바꾼다 |
| Remove | 이 상만 빼고 다시 정밀화한다 |
| View candidate N | 대안 후보 N의 저장된 피팅으로 화면만 바꾼다. 이 후보는 상 조합이 다르며 새 버전은 생기지 않는다 |


## 06 결과 보기 · 두 피팅 비교

![그림 16. Compare fits — same measured pattern 창(Task 18). A는 자동 기준 재계산 v3, B는 CaCO3를 뺀 v4. B는 Rwp가 11.43%에서 24.03%로 오르고 미설명 피크가 2개에서 18개로 늘어 A가 더 낫다.](images/fit-compare.png){height=420}

(1) Fit A / Fit B 선택 상자. Best fit, 후보, 버전 중에서 고른다.
(2) 판정 태그(A fits better, B fits better, Similar fit). 괄호 안은 B - A의 Rwp 차이다.
(3) Adopt B / Keep A / View B. B 채택, 채택 변경 없이 닫기, B를 피팅 창에서 열기.
(4) 지표 표: Rwp, GOF, χ², 상별 wt%, Mass balance, Unexplained peaks, Cautions.
(5) 관측점과 두 계산 패턴. A는 파란 실선, B는 주황 점선이다.
(6) 두 피팅의 관측-계산 잔차.
(7) 두 계산 패턴이 가장 크게 다른 2θ 위치. 누르면 그 구간으로 확대되고 그래프에서는 노란 띠로 보인다.

### 비교 창 읽기

- 여는 곳: Candidate comparison 표 위의 Compare fits, Versions 표의 Compare. Select phases…, Swap, Remove 뒤에는 자동으로 열린다.
- 같은 작업의 두 피팅만 비교한다. 다른 시료끼리는 Results의 Compare selected로 비교하면 된다.
- Keep A를 눌러도 B 버전은 Versions 표에 남는다.
- 노란 띠는 두 모델이 다른 구간을 가리킬 뿐 어느 쪽이 맞는지는 알려 주지 않는다. Rwp 하나보다 잔차, 화학 조성, 물리적 타당성을 함께 보는 편이 안전하다.

![그림 17. 실패 작업(Task 16, EFLECH 시간 초과) 페이지(위)와 Results 목록의 FIZZLED 행(아래, Task 24). 두 작업 모두 자동 조치가 없는 원인이라 Copy conditions & rerun만 보인다.](images/retry.png){height=200}

(1) 실패 원인 요약과 조치 힌트.
(2) Copy conditions & rerun: Submit 화면이 열리고 원래 패턴과 조건이 채워져 있다. 자동 조치가 있는 원인이면 왼쪽에 Retry with suggested fix (…) 버튼이 함께 나온다.
(3) Results 목록의 Copy conditions & rerun. 자동 조치가 있으면 앞에 Retry with fix가 붙는다.

### 실패 작업 재시도

Retry with suggested fix (…)를 누르면 바뀔 설정이 먼저 보인다. Retry with fix로 확정하면 새 작업이 생기고 실패한 작업은 그대로 남는다. 자동 조치가 없는 원인이라면 Copy conditions & rerun에서 값을 고쳐 다시 제출한다.

<!-- widths: 150,337 -->
| 원인 | 자동 조치 |
| --- | --- |
| 계산 시간 초과 | 선별 시간 제한 2배, Maximum analysis time 1.5배 |
| MP 키 문제, 네트워크 오류 | MP 끄기 |
| 메모리 부족 | Candidate pre-filter 켜기, MP 구조 수 최대 50 |
| 후보 구조 없음 | COD 켜기, MP 구조 수 늘리기 |
| 모든 후보 최종 정밀화 실패 | 격자 변화 3% 제한 |
| EFLECH 시간 초과, 피크 없음, BGMN·Ray 오류 | 자동 조치 없음. 2θ 범위를 좁히거나 잡음 구간을 빼고 다시 제출한다 |

## 07 보고서와 내보내기

![그림 18. 논문형 영문 보고서(HTML) 앞부분(Task 22). 제목, 1 Summary, 2 Experimental and methods, Table 1 순으로 이어진다.](images/report.png){height=320}

(1) 제목 "XRD phase analysis — 파일 이름"과 작성 정보 줄(Task 번호 · 생성 날짜 · DARA 버전).
(2) 1. Summary: 종합 판정, 상별 wt% ± esd, Rwp·GOF, 질량 균형 요약.
(3) 2. Experimental and methods: 측정·정밀화 방법 문단. 참고문헌 번호 [1]~[5]가 붙는다.
(4) Table 1. Analysis conditions: 분석 모드, 원소, 전구체, DB, 장비 프로파일, 파장, 2θ 범위, 최종 정밀화 조건 등.

### 논문형 보고서

Best-fit report는 자동 최저 Rwp 결과를, Report (saved choice)는 채택한 결과를 내보낸다. 피팅 창의 This fit's report와 Versions 표의 Report는 지금 보고 있는 피팅이나 그 버전을 내보낸다. 보고서는 인터넷 없이도 열리는 단일 HTML 파일이다. PDF가 필요하면 브라우저에서 Ctrl+P → PDF로 저장하면 된다.

<!-- widths: 150,337 -->
| 절 | 내용 |
| --- | --- |
| 1. Summary | 종합 판정, 상별 정량, Rwp·GOF, 질량 균형 요약 |
| 2. Experimental and methods | DARA·BGMN 버전, 파장, 장비, 데이터 범위, 정밀화 변수를 문장으로 정리한다. Table 1 Analysis conditions |
| 3. Results | 피팅 그림, Table 2 Fit statistics, Table 3 Phase quantification |
| 4. Assessment | 4.1 Phase verdicts, 4.2 Mass balance, 4.3 Unexplained peaks, 4.4 Review cautions |
| 5. Limitations | 상대 wt%, esd 범위 등 해석상 한계 |
| 6. References | DARA, BGMN, COD, Materials Project, pymatgen 등 |

![그림 19. Figure (PNG) 출력 예시.](images/figure.png){height=250}

(1) 범례: Observed, Calculated, Background, Difference.
(2) 관측(검은색)과 계산(빨간색) 패턴. 가장 강한 반사 부근 예시.
(3) Background: 회색 배경 곡선.
(4) 상별 Bragg 반사 위치(tick).
(5) Difference: 관측값에서 계산값을 뺀 차이 곡선.

### 그림, 표, 외부 프로그램용 파일

- Figure (PNG): 600 dpi 피팅 그림. Figure (SVG): 같은 그림의 벡터 버전.
- Table (CSV): 상별 wt%, 표준편차, 격자상수, 품질 지표. Results 목록의 Export table은 여러 작업을 한 표로 묶는다.
- Download all results: 계산 산출물 전체(.lst 포함) ZIP.

<!-- widths: 170,317 -->
| Export for... 항목 | 내용 |
| --- | --- |
| Profex / BGMN (.sav + .str) | Profex에서 바로 여는 프로젝트와 구조 파일 |
| TOPAS (.inp starting model) | TOPAS 시작 모델 입력 파일 |
| GSAS-II (CIF + data) | 정밀화된 CIF와 패턴 데이터 |
| HighScore (CIF + data) | 정밀화된 CIF와 패턴 데이터 |


## 08 결과 해석 시 주의

- wt%는 상대값: 모델에 넣은 결정상끼리 합이 100%가 되도록 정규화한 값이다. 비정질이나 미동정 물질은 빠져 있다. 배경에 넓은 언덕이 보이면 비정질 성분을 의심해 봐야 한다.
- ± 값은 BGMN이 추정한 표준편차다. 우선 배향, 미세흡수, 시료 변위, 잘못된 구조 모델 같은 계통 오차는 포함되지 않는다. Durbin–Watson 주의가 뜨면 실제 불확도는 더 크다.
- MP 계산 구조는 격자가 몇 % 어긋나는 일이 흔하다. 최종 상이 계산 구조뿐이라면 같은 화학식의 실험 CIF로 재정밀화해 보길 권한다.
- 후보는 COD, MP, 사용자가 넣은 CIF에서만 나온다. significant 미설명 피크가 남으면 원소 목록에서 빠진 원소는 없는지, 새로운 상일 가능성은 없는지 검토한다.

### 질량 균형 편차 해석

caution이나 warning은 비율이 어긋났다는 사실만 알려 줄 뿐 원인은 직접 좁혀 가야 한다.

- 휘발 손실: 고온에서 Bi, Pb, Li, Na, K 등이 일부 날아가면 편차 자체가 실제 현상일 수 있다.
- 비정질·미동정 상: 그 상에 들어간 원소가 계산에서 빠져 비율이 치우친다. 배경 언덕, significant 미설명 피크가 있는지 함께 살펴본다.
- 미반응·누락 상: 남은 전구체나 중간상이 모델에 없으면 편차가 커진다. 힌트가 있으면 Add phase…로 넣어 비교한다.
- 정량 문제: 우선 배향, 미세흡수, 잘못된 구조 모델 때문에 wt%가 틀릴 수 있다. Ambiguous 상이나 Durbin–Watson 주의가 함께 있으면 이쪽을 먼저 의심한다.
- 입력 문제: 파일 이름의 숫자 없는 화학식은 계수 1로 읽는다. Fe2O3-TiO2_800C_60min.xrdml은 1:1(몰)로 해석되므로 실제 비율이 다르면 근거 없는 경고가 뜬다. 이름에 계수를 넣거나(예: Fe2O3-2TiO2_...) 칸에 양을 직접 적으면 해결된다.

### 자동 결과는 검토 대상

High confidence도 정답을 보증하지는 않는다. 시료 이력, 합성 조건, 가능하면 다른 분석법(SEM-EDS, XRF 등) 결과와도 대조해야 한다. 상 조합은 낮은 Rwp 하나로 확정하지 않고 미설명 피크, 강도 차이, 판정 근거를 함께 보고 정한다. 최종 판단과 채택 근거는 lab notes에 남겨 둬야 한다.

## 09 문제 해결

<!-- widths: 140,347 -->
| 상황 | 확인과 대처 |
| --- | --- |
| 첫 실행이 실패할 때 | 인터넷과 프록시를 살펴본 뒤 START.cmd를 다시 실행한다. 이미 받은 파일은 다시 쓰인다. 원인은 START 창의 마지막 메시지와 artifacts\launcher\setup-*.log에서 확인할 수 있다. |
| 폴더를 옮긴 뒤 실행되지 않을 때 | START가 알아서 다시 구성한다. 그래도 안 되면 START.cmd -Reinstall을 실행하거나 .venv 폴더를 지운 뒤 다시 실행한다. |
| MP key rejected / not configured | 3장 MP 키 바꾸기를 따른다. 급하면 MP를 끄고 COD와 CIF로 분석한다. |
| 서버가 혼자 꺼질 때 | 창을 모두 닫으면 꺼지는 정상 동작이다(3장 종료). START.cmd로 다시 실행하면 된다. "Unexpected shutdown of the worker process"가 뜬 작업은 다시 제출한다. |
| 웹 화면이 열리지 않을 때 | http://localhost:8898을 직접 입력한다. 회사 프록시가 로컬 주소를 가로채고 있지 않은지도 본다. |
| 분석이 너무 느릴 때 | 전구체를 알면 Reaction check로 돌린다. 그 밖에는 Candidate pre-filter 켜기, MP 구조 수 줄이기, Maximum analysis time 지정, 2θ 범위 좁히기 순서로 시도한다. |
| COD 연결 오류, CERTIFICATE_VERIFY_FAILED | 인터넷·프록시부터 점검한다. 기관망 HTTPS 검사 환경이라면 START.cmd를 다시 실행할 때 인증서 묶음이 자동으로 생성된다. 계속 실패하면 CIF를 직접 올려 분석한다. |
| No candidate structures were found | 원소 목록과 COD/MP 스위치를 다시 확인한다. 그래도 후보가 없으면 참조 CIF를 업로드한다. |
| BGMN refinement program failed | 이상한 CIF나 프로파일·파장 불일치가 흔한 원인이다. 의심 CIF를 빼고 장비 프로파일을 다시 본다. |
| The parallel worker (Ray) could not start | 다른 DARA 창을 닫거나 PC를 재시작한다. 보안 프로그램이 로컬 포트를 막고 있는지도 살펴본다. |
| 질량 균형 경고가 뜰 때 | 6장 질량 균형 점검 읽기, 8장 질량 균형 편차 해석 참고. |
| 재정밀화가 오래 걸리거나 실패할 때 | 배경 항 수를 Auto로 되돌리거나 불필요한 상을 Remove한다. 다른 재정밀화가 도는 중에는 버튼이 꺼지므로 기다리거나 Cancel한다. 실패 알림 문구를 읽고 다시 시도한다. |
| 실패 작업을 다시 돌리고 싶을 때 | 6장 실패 작업 재시도 참고. |
| 피팅이 나쁠 때 | 파장, 장비 프로파일, 원소 목록, 후보 CIF, 분석 범위 순으로 따져 본다. Rwp를 낮추려고 중요한 피크를 제외 구간으로 지우면 안 된다. |

### 폴더 안내

- src, scripts, pyproject.toml은 지우거나 옮기면 안 된다. tools와 .venv는 첫 실행 때 생기는 폴더다.
- 분석 데이터는 사용자 홈의 .dara-server 폴더에 있다(DARA 폴더와 별개). 백업하려면 서버를 끈 뒤 그 폴더를 복사하면 된다.
