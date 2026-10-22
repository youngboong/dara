# DARA 웹 프런트엔드 개발

일반 실행은 저장소 루트의 `START.cmd`를 사용합니다. Node.js는 프런트엔드를
수정할 때만 필요합니다. Python 서버는 포함된 `public/` 빌드를 제공하므로
이 폴더를 단순 생성물로 보고 삭제하지 마세요.

이 디렉터리에서 의존성을 설치하고 개발 서버를 실행합니다.

```console
npm ci
npm run develop
```

개발 모드의 API 주소는 `http://localhost:8001/api`입니다.
Python 환경에서 `dara server --port 8001`로 백엔드를 별도로 실행합니다.
기본 START 서버는 8898 포트를 사용합니다.

배포할 화면을 만들려면 다음 명령을 실행하고 `public/` 변경사항도 함께 반영합니다.

```console
npm run build
```

페이지는 `src/pages/`, 공통 UI는 `src/components/`, 앱 내 안내는 `src/md-pages/`에 있습니다.
개발 캐시 `.cache/`와 `node_modules/`는 커밋하지 않습니다.
