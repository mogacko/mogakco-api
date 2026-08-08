# 프로젝트 지침

아키텍처 또는 애플리케이션 코드를 변경하기 전, 반드시 `docs/decisions/architecture.md`를 읽고 그 결정사항을 따른다. 작업과 일치하는 활성 계획이 `docs/plan/`에 있으면 함께 읽는다.

- 새로운 커뮤니티 기능은 문서에 정의된 기능 중심 모듈러 구조로 구현한다.
- 일반 CRUD 기능에는 전면적인 헥사고날 아키텍처를 도입하지 않는다.
- `docs/decisions/architecture.md`의 기준을 충족하는 기능에만 포트와 어댑터를 추가한다.

## 문서 목차

- `docs/decisions/architecture.md`: 모듈 구조와 현재 시스템 다이어그램
- `docs/decisions/authentication.md`: 소셜 인증·가입·약관 정책
- `docs/decisions/environment-configuration.md`: 환경별 설정과 DB 선택
- `docs/decisions/api-documentation.md`: Swagger 문서화 방식
- `docs/decisions/planning-documents.md`: 계획·완료 문서 관리 규칙
- `docs/plan/social-auth.md`: 소셜 인증 API 계약
- `docs/plan/social-auth-implementation.md`: 구현 상태와 남은 검증
- `docs/plan/profile-media-and-keywords.md`: 이미지 업로드·검증과 키워드 자동완성
- `docs/operations/app-links.md`: Universal Link/App Link 운영 설정
- `docs/operations/media-bucket.md`: 이미지 버킷 CORS·IAM·수명 주기 설정
- `docs/complete/README.md`: 완료 문서 보관 기준
