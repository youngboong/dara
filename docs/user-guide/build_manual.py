"""Build the Korean manual. Requires reportlab and Windows Malgun Gothic fonts.

Run: python docs/user-guide/build_manual.py
Set DARA_MANUAL_FONT_DIR to use a different directory containing malgun.ttf/malgunbd.ttf.
Screenshots are actual DARA UI captures from synthetic workflow QA (2026-10-14).
"""
import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle

ROOT = Path(__file__).resolve().parent
FONT = Path(os.environ.get('DARA_MANUAL_FONT_DIR', 'C:/Windows/Fonts'))
pdfmetrics.registerFont(TTFont('Korean', str(FONT / 'malgun.ttf')))
pdfmetrics.registerFont(TTFont('KoreanBold', str(FONT / 'malgunbd.ttf')))
pdfmetrics.registerFontFamily('Korean', normal='Korean', bold='KoreanBold')
NAVY = colors.HexColor('#15324F')
TEAL = colors.HexColor('#007E87')
styles = {
    'title': ParagraphStyle('title', fontName='KoreanBold', fontSize=28, leading=39, textColor=NAVY, spaceAfter=20),
    'h1': ParagraphStyle('h1', fontName='KoreanBold', fontSize=19, leading=27, textColor=NAVY, spaceAfter=16),
    'h2': ParagraphStyle('h2', fontName='KoreanBold', fontSize=11.5, leading=18, textColor=TEAL, spaceBefore=12, spaceAfter=6),
    'body': ParagraphStyle('body', fontName='Korean', fontSize=10, leading=16.5, wordWrap='CJK', spaceAfter=9),
    'caption': ParagraphStyle('caption', fontName='Korean', fontSize=8, leading=12, textColor=colors.HexColor('#536375'), spaceAfter=10, wordWrap='CJK'),
    'cell': ParagraphStyle('cell', fontName='Korean', fontSize=9, leading=14, wordWrap='CJK'),
}
story = []


def p(text, style='body'):
    story.append(Paragraph(text, styles[style]))


def page(title):
    if story:
        story.append(PageBreak())
    p(title, 'h1')


def section(title, text):
    p(title, 'h2')
    p(text)


def shot(name, caption, height=285):
    img = Image(str(ROOT / 'images' / f'{name}.png'))
    ratio = min(487 / img.imageWidth, height / img.imageHeight)
    img.drawWidth = img.imageWidth * ratio
    img.drawHeight = img.imageHeight * ratio
    story.append(img)
    p(caption + ' 화면의 QA 시료 및 수치는 조작 설명용 합성 데이터입니다.', 'caption')


def table(rows, widths=(145, 342)):
    cells = [[Paragraph(cell, styles['cell']) for cell in row] for row in rows]
    item = Table(cells, colWidths=widths, hAlign='LEFT')
    item.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E6EEF4')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#D4DFE7')),
        ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(item)
    story.append(Spacer(1, 10))


p('DARA\n한글 사용 설명서'.replace('\n', '<br/>'), 'title')
p('Windows · START.cmd 통합 실행 구성', 'h2')
p('분말 X선 회절 데이터의 상 탐색부터 Rietveld 정밀화, 후보 비교 및 보고서 보존까지', 'body')
p('작성 기준: 2026년 10월 22일 · 사용자 맞춤 저장소 youngboong/dara<br/>대상: 이 저장소의 START.cmd와 웹 화면을 사용하는 연구자', 'caption')
section('가장 먼저 할 일', '① 저장소 전체를 내려받아 압축을 풉니다. ② Python 3.10 이상을 준비합니다. ③ START.cmd를 더블클릭합니다. ④ 최초 API 키 입력 또는 건너뛰기를 선택합니다. ⑤ 웹 화면에서 측정 파일을 올리고 분석합니다.')
section('이 설명서의 화면', '실제 DARA 웹 프로그램에서 저장한 화면을 사용했습니다. 화면 캡처는 2026년 10월 14일의 합성 시험 데이터(QA fixture)이며, 실제 측정 시료의 검증된 분석 결과가 아닙니다. 입력 항목과 동작 설명은 현재 소스 및 저장소 문서를 확인해 작성했습니다.')
table([
    ['찾아보기', '페이지'],
    ['시작과 최초 API 키', '2'], ['측정 파일과 입력 화면', '3'],
    ['분석 모드·구조 데이터베이스', '4'], ['장비·파장·분석 구간', '5'],
    ['최종 정밀화·상 선택', '6'], ['진행 확인·중단', '7'],
    ['피팅·후보 해석', '8'], ['채택·연구 메모', '9'],
    ['조건 복사·시료 비교', '10'], ['보고서·결과 보존', '11'],
    ['문제 해결·폴더 안내', '12'],
])

page('01  START.cmd로 시작하기')
section('1. 준비와 실행', '저장소 전체를 같은 폴더에 두고 START.cmd를 더블클릭합니다. START.cmd만 따로 복사하면 scripts와 소스 파일을 찾지 못합니다. Python 3.10 이상이 필요하며, 첫 설치에는 인터넷 연결이 필요합니다. 이 환경에서는 상위 폴더의 xrd 가상환경을 우선 사용합니다. 새 PC에서는 사용 가능한 프로젝트 환경이 없으면 .venv를 만들고 필요한 패키지와 이 저장소의 맞춤 코드를 설치합니다.')
section('2. Materials Project 키 입력', '처음 실행하면 선택적인 API 키 입력 창이 나타납니다. MP를 사용하려면 본인의 Materials Project 키를 입력하고 <b>Save and continue</b>를 누릅니다. 키는 Windows 사용자 환경변수 MP_API_KEY에 저장되며 다음 실행에서 자동으로 사용됩니다. 키를 저장하는 시점에는 온라인 유효성 검사를 하지 않습니다.')
section('3. 키 없이 시작하기', '<b>Skip for now</b>를 선택하면 키 없이 실행합니다. 건너뛰기 선택도 기억되므로 다음 실행에서 반복해서 묻지 않습니다. Submit 화면의 <b>Reference databases → MP를 끄고 COD를 켭니다.</b> 현재 입력 화면은 MP가 기본으로 켜져 있으므로 이 설정을 반드시 확인합니다. 별도의 키 변경 실행 파일이나 변경 메뉴는 제공하지 않습니다.')
section('4. 시작 화면과 웹 주소', '과학 계산 라이브러리를 불러오는 동안 시작 진행 화면이 먼저 열리고, 준비되면 <b>http://localhost:8898</b>로 전환됩니다. 이미 서버가 실행 중이면 기존 서버를 엽니다. 처음에는 설치와 라이브러리 로딩에 시간이 걸릴 수 있습니다. 오류가 표시되면 START 창의 메시지와 artifacts/launcher 안의 로그를 확인합니다.')
section('5. 종료', '사용 중에는 START 명령 창을 열어 두세요. 브라우저 탭만 닫아도 서버는 계속 실행될 수 있습니다. 분석을 끝내고 START 창에서 <b>Ctrl+C</b>를 눌러 해당 창에서 시작한 서버를 종료합니다. 실행 중인 분석은 작업 상세의 Stop analysis로 먼저 정리하는 편이 좋습니다.')
table([['구성', '역할'], ['START.cmd', '일반 사용자가 실행할 유일한 CMD 파일'], ['scripts/', '서버 시작·최초 키 입력 등의 내부 실행 코드'], ['src/ 및 pyproject.toml', '맞춤 DARA 프로그램과 설치 정보'], ['docs/user-guide/', '이 PDF, 화면 이미지 및 PDF 생성 소스']])

page('02  측정 파일 올리기')
shot('copied-conditions', '그림 1. Submit 입력 화면. 이 예시는 기존 작업을 복사한 상태이므로 파란 안내와 Replacement pattern이 표시됩니다.', 300)
section('1. 사용자와 패턴', '상단 <b>Submit</b>으로 이동해 User에 식별용 이름을 입력합니다. XRD pattern 영역을 클릭하거나 파일을 끌어 놓습니다. 한 번에 측정 패턴 한 개를 제출합니다. 새 분석에서는 파일이 필수이고, 복사한 분석에서는 파일을 생략하면 원본 패턴을 재사용합니다.')
section('2. 파일 형식', '<b>.xrdml, .xy, .xye, .txt, .scn, .raw, .rasx</b>를 지원합니다. XY 계열은 2θ(도)와 강도 열을 준비합니다. 장비 전용 파일은 형식·버전에 따라 읽기가 실패할 수 있으므로, 실패하면 장비 소프트웨어에서 2θ–강도 텍스트로 내보내어 사용합니다.')
section('3. 읽기 결과 확인', 'XRDML은 장비·검출기·파장 정보를 이용해 가장 가까운 장비 설정을 적용할 수 있습니다. 표시된 측정 범위와 데이터 점 수가 원본과 맞는지 확인하고, 자동으로 선택된 프로파일도 실제 광학계와 대조합니다. Pattern could not be read가 나오면 원본 형식과 데이터 열을 먼저 확인합니다.')

page('03  분석 모드와 후보 범위')
table([
    ['Analysis mode', '선택 기준과 입력'],
    ['Phase identification', '미지상이 섞인 시료의 상 탐색. Included elements에 원소 기호를 쉼표로 구분해 입력합니다. 예: Bi, Fe, O. 예상 화학식은 선택적으로 추가합니다.'],
    ['Known-phase refinement', '구성 상을 이미 알고 있을 때. Phases to refine에 상의 화학식을 입력합니다. 예: Bi2O3, Fe2O3. 알려지지 않은 상을 찾는 용도로 해석하지 않습니다.'],
    ['Reaction check', '전구체와 반응 조건으로 후보를 좁힐 때. 전구체 화학식, 온도와 분위기를 입력합니다. 반응 예측에는 MP 연동 및 해당 계산 환경이 필요할 수 있습니다.'],
])
section('원소와 화학식의 차이', 'Included elements에는 <b>Bi, Fe, O</b>처럼 원소 기호를 입력합니다. Precursor / expected formulas 또는 Phases to refine에는 <b>Bi2O3, Fe2O3</b>처럼 화학식을 입력합니다. 전구체 잔류상이나 산화물 등 실제로 존재할 수 있는 원소를 빠뜨리면 해당 상이 탐색 범위에서 제외될 수 있습니다.')
section('COD와 MP', '<b>COD</b>는 실험 구조 후보, <b>MP</b>는 계산 구조 보충 후보에 사용됩니다. 키 없이 시작했다면 MP를 끄고 COD를 켭니다. MP E-hull cutoff의 단위는 eV/atom이며 기본 입력값은 0.05, Maximum MP structures는 100입니다. 제한을 넓히거나 후보 수를 늘리면 계산량이 증가할 수 있습니다. 이 값이 상의 존재 확률을 뜻하지는 않습니다.')
section('내가 가진 CIF 추가', '<b>Additional reference CIFs → Select CIF files</b>에서 구조 파일을 추가할 수 있습니다. 파일이 분석 대상의 화학식·결정 구조에 맞는지 확인합니다. 구조 데이터베이스에서 빠진 후보를 보완할 때 유용합니다. 같은 상의 동등한 CIF를 별도 성분으로 여러 개 넣어 정량값을 나누지 않도록 주의합니다.')
section('첫 실습 예시', '실제 Bi–Fe–O 계열 패턴이 있다면 Phase identification, Included elements = Bi, Fe, O로 시작합니다. 필요하면 예상 화학식을 추가하고 실제 장비·파장을 지정합니다. 이 설명서의 BiFeO3 화면 수치가 내 시료에서도 나와야 하는 것은 아닙니다. 제출 전 파일·원소·파장·DB 선택을 다시 확인합니다.')

page('04  장비·파장·분석 구간')
shot('instrument-registration', '그림 2. 장비 프로파일 등록 화면. 실제 광학계에 맞는 GEQ 파일을 준비해야 합니다.', 255)
section('Instrument profile / Wavelength', '실제 장비의 검출기와 슬릿·광학계에 맞는 프로파일을 선택합니다. 실험실 광원은 Cu, Co, Cr, Fe 중 실제 조건을 선택합니다. Synchrotron을 켜면 프로파일이 전환되고 파장을 <b>nm</b>로 직접 입력합니다. Å와 nm를 혼동하지 마세요(1 Å = 0.1 nm).')
section('프로파일 직접 등록', '<b>Register instrument profile</b>에서 이름, 광학계, 방사선 정보와 BGMN GEQ 파일을 입력합니다. 대응하는 GER가 있으면 함께 준비합니다. 검증자와 표준시료 메모는 실제 확인한 내용을 기록합니다. 등록 자체가 장비 교정이나 적합성 검증을 수행하지는 않습니다.')
section('범위와 제외 구간', '<b>Analysis range · 2θ</b>의 시작·끝을 비우면 측정 범위를 사용합니다. 홀더나 기기 아티팩트가 확인된 위치는 <b>Add excluded interval</b>로 구간을 입력합니다. 제외 구간은 피팅과 피크 진단에서 제외되며 원본 강도를 지우지 않습니다. 범위나 제외 구간이 다른 작업의 Rwp를 직접 비교하지 마세요.')

page('05  최종 정밀화와 상 선택')
shot('phase-selection', '그림 3. Edit phases에서 상과 최종 정밀화 조건을 선택하는 화면.', 275)
section('처음에는 Keep saved/default', '<b>Final refinement controls</b>는 최종 후보 정밀화에 적용됩니다. 빠른 후보 선별 조건은 바꾸지 않습니다. 처음에는 저장된 조건/기본값을 유지한 뒤, 결과의 구체적인 문제를 확인하고 한 종류의 설정씩 바꾸어 비교합니다.')
section('세 가지 조절 항목', '<b>Lattice parameters</b>: 참조 CIF 격자 고정 또는 허용 ±% 범위 내 정밀화. <b>Preferred orientation</b>: 무배향 또는 구면조화함수 차수 2·4. <b>Sample peak broadening</b>: K1·K2·B1 정밀화 또는 모두 0으로 고정. 자유도를 늘려 Rwp가 낮아져도 상 동정이 입증되는 것은 아닙니다.')
section('상 조합을 바꾸어 다시 계산', '후보의 <b>Visualize result → Edit phases</b>에서 포함할 참조 CIF만 체크하고 <b>Run as new task</b>를 누릅니다. 원본은 유지되고 새 작업이 생깁니다. 저장된 참조 CIF에서 다시 시작하며, 이전에 정밀화된 원자 좌표에서 계속 계산하는 기능은 아닙니다. 원본 패턴·장비·파장·구간 조건을 이어받고 DB 탐색은 반복하지 않습니다.')

page('06  제출·진행 확인·중단')
section('작업 제출', '입력 화면 아래의 <b>Submit</b>을 누르고 확인 창에서 제출을 확정합니다. 성공 메시지를 확인한 후 상단 <b>Results</b>로 이동합니다. 같은 파일을 여러 조건으로 제출할 수 있으므로, 작업 ID와 조건을 함께 기록하면 혼동을 줄일 수 있습니다.')
table([
    ['표시 상태', '의미와 다음 행동'], ['READY', '대기 상태. 서버와 작업 실행 환경이 유지되고 있는지 확인합니다.'],
    ['RUNNING', '분석 중. 작업 상세에서 단계와 후보 처리 수를 확인합니다.'],
    ['COMPLETED', '작업 완료. 후보·피팅·조건을 검토합니다.'],
    ['FIZZLED', '실패. 작업 오류와 서버 로그를 확인한 후 조건을 수정해 새로 제출합니다.'],
    ['CANCELLING / CANCELLED', '중단 처리 중 / 중단됨. 완료된 후보가 있으면 체크포인트를 내려받을 수 있습니다.'],
])
section('진행률 읽기', '작업 상세의 진행 단계는 자동으로 갱신됩니다. 후보 정밀화의 n / N은 처리한 후보 수이지 전체 소요 시간의 백분율이 아닙니다. 검색·다운로드처럼 총량을 모르는 단계에는 확정적인 완료 시간을 기대하지 마세요. CPU 사용량과 후보 수에 따라 분석 시간이 달라집니다.')
section('해당 작업만 중단', '<b>Stop analysis</b>를 누르고 확인하면 대기 작업을 취소하거나 해당 작업의 계산 프로세스를 중단합니다. 다른 작업을 멈추는 버튼은 아닙니다. 중단 처리 동안에는 연속해서 제출하거나 서버를 강제 종료하기보다 상태 변경을 확인합니다.')
section('완료 후보만 보존', '<b>Download completed candidates</b>는 최종 정밀화까지 끝난 후보의 체크포인트 ZIP을 내려받습니다. 중간 계산 중이던 후보는 유효한 완료 결과에 포함하지 않습니다. 체크포인트의 실행 순서가 최종 Rwp 순위는 아니며, 다운로드한 파일로 자동 재개하는 기능도 아닙니다.')

page('07  피팅과 후보를 해석하기')
shot('candidate-differences', '그림 4. 두 후보의 피팅과 상 차이를 동일 축에서 비교하는 화면.', 300)
section('Rwp 하나만으로 결정하지 않기', 'Rwp는 측정선과 계산선의 일치도를 나타내는 지표이며 일반적으로 작을수록 피팅이 좋습니다. 그러나 낮은 Rwp만으로 실제 상 조합이 확정되지는 않습니다. 설명되지 않은 피크, 예상되지 않은 계산 피크, 강도 차이와 시료 조성을 함께 확인합니다.')
section('Visualize result', '측정선·계산선·배경·차이 곡선과 상별 기여를 살펴봅니다. 그래프를 확대해 주요 피크의 위치·폭·강도를 비교합니다. 회색 음영은 제외 구간입니다. 격자상수와 불확도가 제공되면 단위까지 함께 기록하고, 표시되지 않는 불확도를 임의로 만들어 넣지 않습니다.')
section('Compare two candidate fits', '작업 상세에서 후보 두 개를 선택해 같은 2θ 축으로 비교합니다. 차이가 큰 위치 버튼으로 확대할 수 있습니다. 후보가 하나이면 비교할 수 없습니다. 이 비교는 제외 구간을 별도로 반영하지 않으므로 회색 구간과 실제 피팅 범위를 함께 확인합니다. 다른 측정점·관측 강도를 쓰는 결과는 직접 비교할 수 없습니다.')

page('08  결과 채택과 연구 메모')
shot('review', '그림 5. Adopted result & lab notes에서 채택 후보와 메모를 관리합니다.', 310)
section('채택 후보 지정', '후보의 <b>Adopt</b> 또는 <b>Adopted result &amp; lab notes</b>에서 과학적으로 타당하다고 판단한 후보를 선택합니다. 최저 Rwp 결과와 채택 결과는 별도로 관리됩니다. <b>View adopted fit</b>으로 실제 채택한 피팅을 다시 확인합니다.')
section('메모를 저장하는 순서', '시료 이력, 예상 잔류상, 제외 구간의 이유, 채택 근거, 검토자를 적은 뒤 <b>Save review</b>를 누릅니다. 예: “고온 처리 시료. 홀더 기여 28–29° 제외. 미동정 피크가 남아 정량값은 잠정 사용.” 저장하지 않은 메모가 보고서에 포함되었다고 가정하지 마세요.')
section('정량값의 범위', '상별 정량값은 모델에 포함된 결정상 사이에서 정규화된 질량분율입니다. 미동정상이나 비정질 함량까지 자동으로 포함한 전체 시료의 절대 함량은 아닙니다. 채택은 연구자의 판단 기록이며 자동 검증 표시가 아닙니다.')

page('09  조건 재사용과 여러 시료 비교')
shot('comparison', '그림 6. 여러 작업의 패턴을 정규화·오프셋으로 비교하는 화면.', 290)
section('같은 시료를 다른 조건으로', '작업 상세의 <b>Copy conditions &amp; rerun</b>을 누르면 Submit에 저장된 패턴과 조건이 채워집니다. 바꿀 항목만 수정하고 제출합니다. 새 파일을 올리면 패턴을 교체할 수 있고, 원본 작업은 유지됩니다. 격자 조건·제외 구간·DB 선택 등 변경점을 메모에 남기면 재현하기 쉽습니다.')
section('온도별·시료별 패턴 비교', '<b>Results</b>에서 완료 작업 <b>2–20개</b>를 선택하고 <b>Compare selected</b>를 누릅니다. 공통 2θ 범위, 정규화, 수직 오프셋, Overlay를 조절해 피크 생성·소멸·이동을 확인합니다. 여러 작업의 패턴 비교와 한 작업의 후보 피팅 비교는 목적이 다릅니다.')
section('해석할 때 확인할 조건', '정규화·오프셋·확대는 표시만 바꾸며 원본 강도를 수정하지 않습니다. 정규화된 피크 높이를 바로 함량으로 해석하지 마세요. 파장이 다르면 같은 2θ가 같은 d-spacing을 의미하지 않으므로 동일 조건 여부부터 확인합니다. 상대 비교에도 측정 장비·시료 장착 차이가 영향을 줄 수 있습니다.')

page('10  보고서와 결과 파일 보존')
shot('report', '그림 7. 인터넷 없이 열 수 있는 HTML 분석 보고서의 예시.', 300)
section('채택 결과의 보고서', '메모를 Save review로 저장한 뒤 <b>Report (saved choice)</b>를 누릅니다. 채택 후보가 있으면 그 후보를, 없으면 최저 Rwp 결과를 내보냅니다. Visualize 안의 보고서 버튼은 현재 표시한 후보를 내보내므로 후보 번호를 확인하세요.')
section('분석 보고서를 PDF로 저장', '다운로드한 HTML을 브라우저로 열고 <b>Ctrl+P → PDF로 저장</b>을 선택합니다. 인쇄 미리보기에서 그래프·표가 잘리지 않는지 확인하고 저장합니다. 이 사용 설명서 PDF와 분석 결과 보고서 PDF는 서로 다른 문서입니다. 보고서에는 조건, Rwp, 정량값, 제공된 격자 정보, 피팅 그래프, 참조와 저장 메모가 포함됩니다.')
section('원본과 계산 결과 함께 보관', '<b>Download all / Download all results</b> 등 제공되는 ZIP 다운로드로 계산 자료도 보관합니다. 원본 측정 파일, 사용한 참조 CIF·장비 조건, 작업 ID, 채택 근거, 보고서와 ZIP을 같은 시료 폴더에 정리하면 재검토가 쉽습니다. 기본 서버 저장 위치는 사용자 홈의 .dara-server/montydb이며 Git 저장소와 별개입니다.')

page('11  문제 해결과 파일 안내')
table([
    ['증상', '확인 방법'],
    ['START가 시작되지 않음', 'Python 3.10 이상 설치 여부, 압축 해제 여부, scripts 폴더가 있는지 확인합니다. 오류 창의 마지막 메시지를 기록합니다.'],
    ['첫 설치 실패', '인터넷·프록시와 pip 오류를 확인한 뒤 START를 다시 실행합니다. 설치 중 창을 닫으면 다음 실행에서 설치를 다시 시도할 수 있습니다.'],
    ['웹 화면이 열리지 않음', 'START 창과 artifacts/launcher 로그를 확인하고 http://localhost:8898을 직접 엽니다. 로컬 주소가 회사 프록시로 전달되는지 확인합니다.'],
    ['MP 인증 오류', '최초 키 입력 여부와 Reference databases의 MP 선택을 확인합니다. 키 없이 사용한다면 MP를 끄고 COD를 사용합니다.'],
    ['구조 다운로드·BGMN 오류', '오류에 표시된 파일 경로·네트워크 상태를 확인합니다. 패키지 설치만으로 모든 외부 데이터와 계산 환경이 검증되지는 않습니다.'],
    ['피팅이 좋지 않음', '파장, 장비 프로파일, 원소 목록, 후보 CIF, 분석 구간을 먼저 확인합니다. Rwp를 줄이기 위해 임의로 중요한 피크를 제외하지 마세요.'],
    ['메모·채택이 보고서와 다름', 'Save review 완료 여부와 Report (saved choice) / 현재 후보 보고서 중 어느 버튼을 사용했는지 확인합니다.'],
])
section('정리된 저장소', '<b>START.cmd</b>만 사용자용 실행 파일로 남겼습니다. src·scripts·tests·docs·dataset·notebooks 등은 실행 코드, 검증, 문서 또는 재현용 자료이므로 역할에 맞게 보존했습니다. 루트의 sample_*_check.html 세 파일은 로컬 artifacts/legacy-checks로 이동해 기존 출력을 보존하고 Git 추적에서 제외했습니다.')
section('백업 위치', 'artifacts는 로그와 개발 산출물로 Git에 올라가지 않습니다. Python 가상환경도 Git에 포함되지 않습니다. 서버 데이터와 사용자 설정은 저장소 밖에 있을 수 있으므로 Git 푸시가 분석 DB 백업을 대신하지 않습니다. 서버를 종료한 후 별도로 백업하세요. 등록 장비 파일과 체크포인트는 MontyDB 상위 폴더의 instrument-profiles와 task-checkpoints에 저장됩니다.')
p('문서 근거: README.md, docs/practical-workflows.md, scripts/start_dara_web.ps1, scripts/materials_project_key.ps1, src/dara/server/ui/src 및 서버 설정 소스. 화면: artifacts/workflow-qa의 실제 UI 캡처. PDF 재생성: reportlab 설치 후 python docs/user-guide/build_manual.py 실행.', 'caption')


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor('#D4DFE7'))
    canvas.line(54, 45, A4[0] - 54, 45)
    canvas.setFont('Korean', 8)
    canvas.setFillColor(colors.HexColor('#536375'))
    canvas.drawString(54, 30, 'DARA · 한글 사용 설명서 · 2026-10-22')
    canvas.drawRightString(A4[0] - 54, 30, str(doc.page))
    canvas.restoreState()


if __name__ == '__main__':
    target = ROOT / 'DARA_사용설명서_한글.pdf'
    doc = SimpleDocTemplate(str(target), pagesize=A4, rightMargin=54, leftMargin=54,
                            topMargin=48, bottomMargin=62, title='DARA 한글 사용 설명서', author='DARA 프로젝트')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(target)
