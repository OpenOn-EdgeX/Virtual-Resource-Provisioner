# Virtual Resource Provisioner

Kubernetes Deployment를 생성해 CPU·메모리·GPU를 요청하고, 실행 중인 Deployment의 자원과 복제본 수를 변경하거나 해지하는 API입니다.

| 작업 | 경로 (`/rest/1.0/deployment_management` 기준) |
| --- | --- |
| 생성 | `POST /create` |
| 자원 변경 | `PATCH /resources` |
| 복제본 조정 | `PATCH /replicas` |
| 해지 | `DELETE /delete/{namespace}/{name}` |

[설치 및 호출 예제](USAGE.md). 전용 네임스페이스와 명시적인 Kubernetes 컨텍스트가 필요합니다. 기본 서버는 로컬에서만 수신하며, 변경·삭제는 이 도구가 관리하는 Deployment로 제한됩니다. 생성 응답은 Pod 실행 완료를 뜻하지 않으며, 해지 대기 시간이 지나면 완료 대신 `202`를 반환합니다.

`capacity.py`는 전달받은 노드별 가용량으로 배치 가능성을 추정하는 독립 함수입니다. API에서 자동 실행되거나 스케줄러의 배치를 보장하지 않습니다.
