# 프로젝트 지침

아키텍처 또는 애플리케이션 코드를 변경하기 전, 반드시 `docs/decisions/architecture.md`를 읽고 그 결정사항을 따른다. 작업과 일치하는 활성 계획이 `docs/plan/`에 있으면 함께 읽는다.

- 새로운 커뮤니티 기능은 문서에 정의된 기능 중심 모듈러 구조로 구현한다.
- 클라이언트는 Flutter Android·iOS 앱뿐이다. 웹 전용 대응은 추가하지 않는다.
- 일반 CRUD 기능에는 전면적인 헥사고날 아키텍처를 도입하지 않는다.
- `docs/decisions/architecture.md`의 기준을 충족하는 기능에만 포트와 어댑터를 추가한다.
- 이미지를 쓰는 기능은 `asset_usages`에 사용처 행을 남긴다. 남기지 않으면 정리 배치가 24시간 뒤 그 이미지를 지운다.
- 주기 실행이 필요하면 앱에 스케줄러를 넣지 말고 `python -m` 진입점만 만든다. 부르는 쪽은 배포 환경이 정한다.

## 구현 전에 읽는 문서

- `docs/decisions/architecture.md`: 모듈 구조, 포트·어댑터를 두는 기준, 저장소별 역할
- `docs/decisions/api-documentation.md`: 라우트 `summary`와 OpenAPI 규칙
- `docs/decisions/environment-configuration.md`: 환경 변수 주입 방식과 배포 형태
- `docs/decisions/planning-documents.md`: 계획 문서를 쓰고 완료 처리하는 규칙
- `docs/decisions/authentication.md`: 인증이 걸린 기능을 만들 때

## 그 밖의 문서

작업에 해당할 때만 본다.

- `docs/plan/`: 진행 중인 계획. 작업과 맞는 문서가 있으면 함께 읽는다
- `docs/complete/`: 완료된 기능의 계약과 검증 결과. 기존 동작을 확인할 때 본다
- `docs/operations/`: 운영 절차. 버킷·캐시·앱 링크·주기 실행 설정
- `docs/architecture/current.svg`: 현재 구성도 (편집 원본은 같은 폴더의 `.drawio`)
- `deploy/systemd/`: 미니PC용 주기 실행 유닛 파일
