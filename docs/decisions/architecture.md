# 아키텍처 결정

![현재 개발 아키텍처](../architecture/current.svg)

편집 원본: [`current.drawio`](../architecture/current.drawio)

클라이언트는 Flutter로 만드는 Android·iOS 앱뿐이다. 웹은 폐기했으므로 브라우저 전용 대응(CORS, 쿠키 기반 세션, 웹 리다이렉트 콜백)은 두지 않는다.

기능 중심 모듈러 모놀리스로 구현한다. 기능별 `router.py`는 HTTP, `service.py`는 규칙, `schemas.py`는 API 변환을 맡는다. ORM 모델을 응답에 직접 노출하지 않는다.

일반 CRUD에는 헥사고날 아키텍처나 `repository.py`를 추가하지 않는다. 외부 연동 교체·복잡한 상태 규칙·격리 테스트가 실제로 필요할 때만 포트, 어댑터, 저장소를 둔다. `common/`은 실제 공유 코드만 둔다.
