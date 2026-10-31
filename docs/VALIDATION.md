# 구현 검증 기록

2026-10-31 기준, Linux / Python 3.12.14 / Chromium에서 확인했습니다.

## 자동 테스트

```bash
python3 -m unittest discover -s tests -v
```

**17개 통과**: 물리 모델 9개, 실제 로컬 HTTP 요청을 사용하는 API 통합 테스트 8개.

- 충·방전 효율, 저장량 갱신, 전력망 에너지 수지
- SOC·출력 한계, 동시 충·방전 방지
- 시간별·일별 및 모든 외란에서 구간·전체 에너지 수지
- 미래 실현값을 바꿔도 그 시점 이전 예측·의사결정이 변하지 않음
- SOC 센서만 정체되고 독립된 부하·발전 계측은 계속 갱신됨
- CSV 시간축·단위 열·유한값·설정 검증
- 합성 자료 시드의 재현성과 변화
- JSON에 포함된 전체 입력과 설정으로 비교 결과 재현
- 페이지·정적 파일·API·잘못된 요청의 응답

## 브라우저 기능 검증

실제 Chromium에서 다음 흐름을 확인했으며 JavaScript 페이지 오류는 없었습니다.

- 첫 접속 시 합성 자료로 4개 정책 실행
- 예측·운영 그래프, 재생·일시정지·처음으로 이동
- 시드 변경에 따른 합성 자료 재생성
- SOC 센서 정체 시나리오 실행 및 로그 CSV 저장
- 전체 입력을 포함한 실험 JSON 저장
- 일별 지표와 ESS 없는 기준의 해당 없음 표시
- 예제 CSV 다운로드 → 재업로드 → 실험 실행
- 잘못된 CSV의 오류 안내
- 개인 학습 노트 저장
- 390px 모바일 화면에서 페이지 가로 넘침 없음

CLI의 시간별 발전 감소와 일별 센서 정체 실험도 CSV·JSON 출력까지 실행했습니다. 프로그램 실행에는 브라우저 테스트 도구나 외부 패키지가 필요하지 않습니다.

## 아직 검증하지 않은 항목

- Windows/macOS 실제 실행: 실행 안내와 Windows 배치 파일을 제공하지만 해당 OS에서 시험하지 않았습니다.
- 실제 캠퍼스 원시 부하·태양광 자료: 논문 PDF만으로 원시 시계열을 복원하지 않았습니다.
- 학습자 교육 효과, 실제 건물 절감량, 실물 배터리 성능: 별도 연구 자료와 실험이 필요합니다.

## 저장 결과 재현

실험 JSON은 예측 이력까지 포함한 `input_records`, 전체 `config`, 입력 SHA-256을 저장합니다. 다운로드한 JSON을 프로젝트 폴더에 둔 뒤 다음처럼 재계산할 수 있습니다.

```python
import json
from energy_sim import run_experiment

with open("energy_loop_experiment.json", encoding="utf-8") as stream:
    saved = json.load(stream)
rerun = run_experiment(saved["input_records"], saved["config"])
assert rerun["comparison"] == saved["comparison"]
```

동일한 프로그램 버전과 입력·설정에서 재현하는 예입니다. 원시 CSV나 실험 JSON을 외부에 공유하기 전에는 데이터 이용 범위를 확인합니다.
