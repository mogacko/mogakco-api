# 이미지 버킷 운영 설정

클라이언트가 S3로 직접 올리므로 버킷 쪽 설정이 없으면 API가 정상이어도 업로드가 통째로 실패한다.

## 버킷

퍼블릭 접근을 모두 차단하고 CloudFront를 앞에 둔다. `S3_PUBLIC_BASE_URL`에는 버킷 주소가 아니라 CDN 도메인을 넣는다. 에셋의 `url` 컬럼이 이 값으로 만들어져 그대로 응답에 나가므로, 나중에 바꾸면 이미 저장된 URL과 어긋난다.

## CORS

설정하지 않는다. 클라이언트가 Flutter Android·iOS 앱뿐이라 업로드가 브라우저를 거치지 않고, 네이티브 요청은 CORS를 타지 않는다. 웹을 다시 지원하게 되면 그때 `AllowedOrigins`에 웹 도메인을 넣어 추가한다.

## IAM

API 역할에 세 가지가 모두 필요하다. presigned POST는 API의 자격 증명으로 서명하므로 `s3:PutObject`가 있어야 하고, 완료 검증에 `s3:GetObject`, 위장 파일 정리에 `s3:DeleteObject`가 쓰인다. `DeleteObject`가 빠지면 거절은 되지만 버킷에 쓰레기가 남는다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::mogakco-media/images/*"
    }
  ]
}
```

## 고아 이미지 정리

하루 한 번 부른다. DB 행과 S3 객체를 함께 지운다.

```
uv run python -m app.images.cleanup
uv run python -m app.images.cleanup --dry-run   # 지울 대상만 출력하고 아무것도 안 지운다
```

`--dry-run`은 S3를 아예 부르지 않으므로 자격 증명 없이도 돈다. **타이머를 처음 걸 때 이걸로 한 번 확인하고 넘어간다.** S3 삭제는 되돌릴 수 없다.

대상은 **`asset_usages`에 행이 없고** 생성된 지 `MEDIA_ORPHAN_AGE_HOURS`(기본 24)가 지난 이미지다. 업로드 URL만 받고 올리지 않은 것과, 프로필 사진을 바꿔서 참조가 끊긴 이전 이미지가 모두 여기 걸린다. 올린 직후 아직 어디에도 붙이기 전인 이미지를 지우지 않으려고 시간 여유를 둔다.

사용처를 한 표로 모아 뒀으므로 **행사 썸네일 같은 새 사용처가 생겨도 이 배치는 고치지 않는다.** 새 사용처가 `asset_usages`에 행을 남기기만 하면 된다. 남기지 않으면 그 이미지는 24시간 뒤 지워진다. 설계 배경은 [프로필 이미지와 키워드 자동완성](../complete/profile-media-and-keywords.md)의 「사용처 표」에 있다.

### 미니PC에 타이머 걸기

유닛 파일은 [`deploy/systemd/`](../../deploy/systemd)에 있다. `cron` 대신 systemd 타이머를 쓰는 이유는 **`Persistent=true`** 하나다. 미니PC는 재부팅·절전·정전으로 꺼져 있는 시간이 있는데, `cron`은 실행 시각에 꺼져 있으면 그날치를 그냥 건너뛴다. 타이머는 부팅 직후 따라잡는다.

```bash
sudo cp deploy/systemd/mogakco-cleanup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mogakco-cleanup.timer
```

`ExecStart`의 서비스 이름(`api`)과 `WorkingDirectory`(`/srv/mogakco`)를 실제 compose 구성에 맞춘다. 환경 변수는 compose가 읽는 `.env`에서 오므로 유닛 파일에 따로 적지 않는다 — **`cron`에서 흔히 겪는 "`DATABASE_URL`이 없어서 실패"가 이 구조에서는 생기지 않는다.**

`--no-deps`는 일부러 빼 뒀다. `Persistent=true`로 부팅 직후 따라잡을 때 DB 컨테이너가 아직 안 떠 있을 수 있는데, 붙어 있으면 그 실행이 연결 실패로 끝난다. 이미 떠 있으면 compose가 다시 띄우지 않으므로 빼서 손해 보는 것도 없다.

확인:

```bash
systemctl list-timers mogakco-cleanup.timer   # 다음 실행 시각
journalctl -u mogakco-cleanup.service         # "지운 이미지 N개"가 남는다
sudo systemctl start mogakco-cleanup.service  # 지금 한 번 돌려보기
```

**AWS로 옮길 때 버리는 것은 이 두 파일뿐이다.** EventBridge Scheduler에서 같은 이미지에 같은 커맨드로 ECS RunTask를 걸면 된다. `DATABASE_URL`·`REDIS_URL`·`S3_*`가 전부 환경 변수 뒤에 있어 RDS·ElastiCache로 갈아타도 코드는 그대로다.

수명 주기 규칙은 미완료 멀티파트 업로드에만 건다. 이건 객체가 아니라 조각이라 DB에도 남지 않고 배치가 볼 수 없다.

```json
{
  "Rules": [
    {
      "ID": "abort-incomplete-multipart",
      "Status": "Enabled",
      "Filter": { "Prefix": "images/" },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 1 }
    }
  ]
}
```

객체 만료 규칙은 걸지 않는다. 프로필 사진은 오래됐다고 지우면 안 되고, 참조 여부는 S3가 알 수 없다.

## 클라이언트 업로드 주의

`POST /images/upload-url` 응답의 `fields`를 폼에 그대로 넣고 **파일을 맨 마지막에 붙인다.** S3는 정책 필드보다 앞에 온 파일을 거절한다.

```dart
final req = http.MultipartRequest('POST', Uri.parse(uploadUrl))
  ..fields.addAll(fields.cast<String, String>())
  ..files.add(await http.MultipartFile.fromPath('file', path)); // 반드시 마지막

await req.send();
await http.post(Uri.parse('$apiBase/images/$assetId/complete'), headers: authHeader);
```

`package:http`의 `MultipartRequest`는 `fields`를 모두 쓴 뒤 `files`를 쓰므로 순서가 저절로 맞는다. Dio의 `FormData`를 쓴다면 `fields`를 먼저 넣고 파일 항목을 나중에 넣어야 한다.

성공 시 S3는 본문 없이 204를 준다. 업로드가 끝나도 `complete`를 부르기 전에는 `PENDING`이라 프로필에 붙일 수 없다.
