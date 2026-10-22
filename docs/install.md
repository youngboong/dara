# 설치

## Windows에서 처음 설치

[한글 사용 설명서 PDF](user-guide/DARA_사용설명서_한글.pdf)의 Python 설치부터 따라 하세요.
저장소 전체를 내려받아 압축을 푼 후 `START.cmd`를 실행합니다.
Python 3.10 이상과 최초 설치용 인터넷 연결이 필요합니다.
START가 필요한 환경과 패키지를 설치하고 이 저장소의 맞춤 DARA를 실행합니다.

## 개발용 설치 — Optional(선택)

별도의 Python 가상환경을 활성화한 상태에서 저장소 루트에서 실행합니다.

```console
python -m pip install -e .
dara server
```

프런트엔드 개발 방법은 `src/dara/server/ui/README.md`에 있습니다.

## 원본 PyPI 배포판 — Optional(선택)

```console
python -m pip install dara-xrd
```

이 명령만으로는 이 저장소의 맞춤 변경사항이 설치되지 않습니다.
맞춤 버전은 START 또는 위의 개발용 설치 절차를 사용하세요.
추가 플랫폼 안내는 [원본 공식 설치 문서](https://CederGroupHub.github.io/dara/install.html)에 있습니다.
